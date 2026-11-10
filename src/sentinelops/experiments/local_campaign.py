"""Bounded nonfinal acquisition, resumable from immutable slot receipts.

This controller never fits/promotes models, retries an interrupted workload or
changes the predeclared acquisition budget in response to measured outcomes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import (ALIBABA_BINDING, ALIBABA_PLAN, DETECTOR_PLAN, KINDS, ROLES, PROJECT,
    bind, clean_source, group_name, inspect_completed_capture, is_alibaba_plan, is_detector_plan, load_plan,
    plan_kinds, plan_roles, read_alibaba_epoch, read_binding, recipe, registered_sessions, registration_path)
from sentinelops.ml.local_data import audit_alibaba_local, audit_detector_events, audit_local_corpus, verify_capture
from sentinelops.telemetry.core import stable_id, timestamp


def planned_slots(plan):
    roles = [role for role in plan_roles(plan) if role != 'sealed-final']
    return [(role, ordinal, kind) for role in roles
            for ordinal in range(1, plan['minimum_groups'][role]+1) for kind in plan_kinds(plan)]


def attempt_log(store, slot):
    """A new exclusive log per attempt; an earlier failed attempt's log is kept."""
    directory = store.root/'campaign/logs'
    directory.mkdir(parents=True,exist_ok=True)
    log = directory/(slot+'.log')
    attempt = 1
    while log.exists():
        attempt += 1
        log = directory/f'{slot}.attempt-{attempt}.log'
    return log


def _audit(store, plan, project):
    if is_alibaba_plan(plan):
        return audit_alibaba_local(store, plan, project=project)
    if is_detector_plan(plan):
        return audit_detector_events(store, plan, project=project)
    return audit_local_corpus(store, plan=plan, project=project)


@contextmanager
def campaign_lock(store):
    store.root.mkdir(parents=True, exist_ok=True)
    with (store.root/'.campaign.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('another campaign controller owns this corpus') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def source_checkout(project, commit):
    """Reuse a clean registered worktree; never switch an active capture."""
    if clean_source(project) == commit:
        return project
    output = subprocess.check_output(['git','worktree','list','--porcelain'],cwd=project,text=True)
    for block in output.strip().split('\n\n'):
        fields = dict(line.split(' ',1) for line in block.splitlines() if ' ' in line)
        if fields.get('HEAD') == commit:
            path = Path(fields['worktree'])
            try:
                if clean_source(path) == commit:
                    return path
            except ValueError:
                continue
    raise ValueError('registered capture needs its original clean source worktree: '+commit)


def await_existing_result(store, slot, config, *, now=None, sleep=time.sleep):
    root = store.root/'runs'/slot
    _, registered = read_binding(store,root/'registration.json')
    deadline = utc_time(registered['registered_at']) + timedelta(seconds=
        config.warmup_seconds+config.active_seconds+config.recovery_seconds+1200)
    while not (root/'result.json').exists():
        for path in (root/'captures').glob('run-*/run.json'):
            source = json.loads(path.read_bytes())
            if source.get('status') == 'failed':
                raise ValueError('existing capture failed; source retained, no automatic replacement: '+slot)
        if (now() if now else datetime.now(timezone.utc)) > deadline:
            raise ValueError('existing capture is incomplete beyond its budget; investigate without overwriting: '+slot)
        sleep(30)
    return read_binding(store,root/'result.json')


def _event(store, value):
    directory = store.root/'campaign/events'
    existing = sorted(directory.glob('*.json'))
    previous = read_binding(store,existing[-1])[0] if existing else None
    return bind(store,directory/f'{len(existing)+1:06d}.json',{
        'campaign_event_version':'local-http-acquisition/v1','previous_ref':previous,
        'recorded_at':timestamp(datetime.now(timezone.utc)),**value})


def require_completed_role_admission(report, role):
    """Do not make an earlier split unrepairable by starting a later one."""
    deficits = [condition for condition in report['failed_conditions']
                if condition['condition'].startswith(role+'/')]
    if deficits or report['quarantined_slots']:
        raise ValueError('previous role lacks source-admitted support before chronological transition: '
                         +json.dumps({'role':role,'deficits':deficits,'quarantine':report['quarantined_slots']},sort_keys=True))


def register_controller(store, project, identity):
    """Retain the original campaign and bind a same-plan descendant fix explicitly."""
    path = store.root/'campaign/registration.json'
    if not path.exists():
        return bind(store,path,{**identity,'registered_at':timestamp(datetime.now(timezone.utc))})
    original_ref, saved = read_binding(store,path)
    prior = {k:v for k,v in saved.items() if k not in ('registered_at','controller_source_commit')}
    if prior != {k:v for k,v in identity.items() if k != 'controller_source_commit'}:
        raise ValueError('campaign plan/roles/budgets are frozen; cannot silently replace them')
    if saved['controller_source_commit'] == identity['controller_source_commit']:
        return original_ref
    subprocess.run(['git','merge-base','--is-ancestor',saved['controller_source_commit'],identity['controller_source_commit']],
                   cwd=project,check=True)
    upgrade = store.root/'campaign/controllers'/(identity['controller_source_commit']+'.json')
    if upgrade.exists():
        reference, value = read_binding(store,upgrade)
        if value['original_campaign_ref'] != original_ref or value['controller'] != identity:
            raise ValueError('controller successor differs from its immutable registration')
        return reference
    return bind(store,upgrade,{'controller_successor_version':'local-http-same-plan/v1',
        'original_campaign_ref':original_ref,'controller':identity,
        'registered_at':timestamp(datetime.now(timezone.utc)),'capture_registrations_rewritten':False})


def register_alibaba_epoch(store, plan, source, bundle, manifest_sha256):
    from sentinelops.ml.alibaba_bundle import BUNDLE_VERSION
    from sentinelops.ml.alibaba_release import _pinned
    from sentinelops.ml.alibaba_training import load_config
    from sentinelops.ml.models import read_bundle_manifest

    if bundle is None or manifest_sha256 is None:
        raise ValueError('ADR-016 campaign requires --bundle and --manifest-sha256')
    bundle = Path(bundle).resolve(strict=True)
    manifest = read_bundle_manifest(bundle, expected_manifest_sha256=manifest_sha256)
    if manifest['bundle_version'] != BUNDLE_VERSION or manifest['provenance']['config'] != load_config():
        raise ValueError('ADR-016 requires the frozen Alibaba v3 feature contract')
    if set(manifest['artifacts']) != {'detector.joblib', 'risk.json'}:
        raise ValueError('ADR-016 requires both pinned Alibaba estimators')
    for name, digest in manifest['artifacts'].items():
        _pinned(bundle/name, digest)
    _pinned(bundle/'report.json', manifest['provenance']['report_sha256'])
    value = {'epoch_version': 'alibaba-local-epoch/v1', 'plan_sha256': stable_id(plan), 'source_commit': source,
             'model_version': manifest['model_version'], 'manifest_sha256': manifest_sha256}
    path = store.root/ALIBABA_BINDING
    if path.exists():
        reference, previous = read_alibaba_epoch(store, plan)
        if any(previous[k] != v for k, v in value.items()):
            raise ValueError('ADR-016 epoch is already registered to another bundle or source')
        return reference
    if registered_sessions(store):
        raise ValueError('ADR-016 candidate cannot be pinned after registration')
    return bind(store, path, {**value, 'registered_at': timestamp(datetime.now(timezone.utc))})


SLOT_OVERHEAD_SECONDS = 180  # container start, source verification and audit per slot


def group_budget_seconds(plan, role, ordinal):
    """Predeclared wall time for one whole group: every slot's schedule plus fixed overhead."""
    total = 0
    for kind in plan_kinds(plan):
        config = recipe(plan, role, ordinal, kind)
        total += config.warmup_seconds + config.active_seconds + config.recovery_seconds + SLOT_OVERHEAD_SECONDS
    return total


def run_campaign(store, *, project=PROJECT, follow_existing=False, plan_path=None,
                 bundle=None, manifest_sha256=None, window_end=None, clock=None):
    """Acquire exactly the minimum registered nonfinal plan, then report G2.

    With the ADR-015 detector plan the same controller acquires the
    evaluation-only epoch and reports its evaluation readiness instead.
    """
    with campaign_lock(store):
        source = clean_source(project)
        plan_file = Path(plan_path) if plan_path else project/'configs/local_http_capture_plan.json'
        plan = load_plan(plan_file)
        alibaba = is_alibaba_plan(plan)
        detector = is_detector_plan(plan) or alibaba
        identity = {'campaign_version':'local-http-acquisition/v1','controller_source_commit':source,
                    'plan_sha256':stable_id(plan),'slots':[list(slot) for slot in planned_slots(plan)],
                    'sealed_final_allowed':False,'automatic_fitting_allowed':False}
        if detector:
            identity['plan_version'] = plan['plan_version']
        if alibaba:
            identity['evaluation_epoch_ref'] = register_alibaba_epoch(store, plan, source, bundle, manifest_sha256)
        elif bundle is not None or manifest_sha256 is not None:
            raise ValueError('candidate pins apply only to the ADR-016 evaluation plan')
        controller_ref = register_controller(store,project,identity)
        _event(store,{'status':'CONTROLLER_RESUMED','controller_ref':controller_ref})
        previous_role = None
        try:
            from sentinelops.experiments.local_recovery import effective_slots
            for role, ordinal, kind in effective_slots(store, plan, planned_slots(plan), project=project):
                if previous_role is not None and role != previous_role:
                    report, _, _ = _audit(store,plan,project)
                    require_completed_role_admission(report,previous_role)
                previous_role = role
                if (store.root/'dataset-freeze.json').exists() or (store.root/'evaluation-freeze.json').exists():
                    raise ValueError('corpus is frozen; campaign cannot append acquisition')
                group = group_name(plan,role,ordinal)
                slot = group+'-'+kind
                if window_end is not None and not registration_path(store,group).exists():
                    # Host availability window: a group must never straddle a shutdown, because an
                    # interrupted group is excluded whole. Pause at the boundary; nothing is retried.
                    now_at = (clock or (lambda: datetime.now(timezone.utc)))()
                    needed = group_budget_seconds(plan,role,ordinal)
                    if now_at + timedelta(seconds=needed) > window_end:
                        _event(store,{'status':'PAUSED_AT_GROUP_BOUNDARY','next_group':group,
                                      'needed_seconds':needed,'window_end':window_end.isoformat()})
                        return {'status':'PAUSED_AT_GROUP_BOUNDARY','next_group':group,'needed_seconds':needed,
                                'models_fitted':0,'holdout_opened':False,'activation_allowed':False}
                root = store.root/'runs'/slot
                config = recipe(plan,role,ordinal,kind)
                if not (root/'result.json').exists():
                    if root.exists():
                        if not follow_existing:
                            raise ValueError('existing incomplete slot requires explicit follow mode: '+slot)
                        _event(store,{'status':'WAITING_FOR_EXISTING_CAPTURE','slot':slot})
                        await_existing_result(store,slot,config)
                    else:
                        session_path = registration_path(store,group)
                        capture_project = project
                        if session_path.exists():
                            _, session = read_binding(store,session_path)
                            capture_project = source_checkout(project,session['source_commit'])
                        command = [sys.executable,str(capture_project/'scripts/capture_local_corpus.py'),
                            '--root',str(store.root),'--role',role,'--group',str(ordinal),'--kind',kind]
                        if not session_path.exists(): command.append('--prepare')
                        if detector:
                            selected_plan = ALIBABA_PLAN if alibaba else DETECTOR_PLAN
                            command.extend(['--plan', str(capture_project/'configs'/selected_plan.name)])
                        environment = {**os.environ,'PYTHONPATH':str(capture_project/'src')+os.pathsep+str(capture_project)}
                        log = attempt_log(store, slot)
                        _event(store,{'status':'CAPTURE_STARTED','slot':slot,'capture_checkout':str(capture_project)})
                        with log.open('x') as stream:
                            subprocess.run(command,cwd=capture_project,env=environment,stdout=stream,stderr=subprocess.STDOUT,
                                check=True,timeout=config.warmup_seconds+config.active_seconds+config.recovery_seconds+1200)
                inspect_completed_capture(store,slot,project=project)
                _, session = read_binding(store,registration_path(store,group))
                verified = verify_capture(store,plan,session,kind,project=project)
                report, _, _ = _audit(store,plan,project)
                report_ref = store.write('manifests',report)
                _event(store,{'status':'SOURCE_VERIFIED','slot':slot,'source_result_ref':verified['source_result_ref'],
                    'oracle_ref':verified['oracle_ref'],'dataset_report_ref':report_ref,
                    'healthy_rows':sum(row['healthy'] for row in verified['rows']),
                    'positive_rows':sum(row['risk_label'] == 1 for row in verified['rows'])})
                print(json.dumps({'slot':slot,'status':'SOURCE_VERIFIED',('evaluation_status' if detector else 'G2'):report['status'],
                                  'verified_completed_runs':report['verified_completed_runs']}),flush=True)
            report, _, _ = _audit(store,plan,project)
            report_ref = store.write('manifests',report)
            _event(store,{'status':'ACQUISITION_BUDGET_COMPLETE','dataset_report_ref':report_ref,
                         ('evaluation_status' if detector else 'G2'):report['status'],'activation_allowed':False})
            return {'dataset_ref':report_ref,('evaluation_status' if detector else 'G2'):report['status'],'models_fitted':0,'holdout_opened':False,
                    'activation_allowed':False}
        except Exception as exc:
            _event(store,{'status':'STOPPED_REQUIRES_INVESTIGATION','error_type':type(exc).__name__,
                         'reason':str(exc),'source_preserved':True,'automatic_retry':False})
            raise
