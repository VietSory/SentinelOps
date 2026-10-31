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
from sentinelops.experiments.local_corpus import (KINDS, ROLES, PROJECT, bind, clean_source,
    group_name, inspect_completed_capture, load_plan, read_binding, recipe, registration_path)
from sentinelops.ml.local_data import audit_local_corpus, verify_capture
from sentinelops.telemetry.core import stable_id, timestamp


def planned_slots(plan):
    return [(role, ordinal, kind) for role in ROLES[:-1]
            for ordinal in range(1, plan['minimum_groups'][role]+1) for kind in KINDS]


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


def run_campaign(store, *, project=PROJECT, follow_existing=False):
    """Acquire exactly the minimum registered nonfinal plan, then report G2."""
    with campaign_lock(store):
        source = clean_source(project)
        plan = load_plan(project/'configs/local_http_capture_plan.json')
        identity = {'campaign_version':'local-http-acquisition/v1','controller_source_commit':source,
                    'plan_sha256':stable_id(plan),'slots':[list(slot) for slot in planned_slots(plan)],
                    'sealed_final_allowed':False,'automatic_fitting_allowed':False}
        controller_ref = register_controller(store,project,identity)
        _event(store,{'status':'CONTROLLER_RESUMED','controller_ref':controller_ref})
        previous_role = None
        try:
            for role, ordinal, kind in planned_slots(plan):
                if previous_role is not None and role != previous_role:
                    report, _, _ = audit_local_corpus(store,plan=plan,project=project)
                    require_completed_role_admission(report,previous_role)
                previous_role = role
                if (store.root/'dataset-freeze.json').exists():
                    raise ValueError('corpus is frozen; campaign cannot append acquisition')
                group = group_name(plan,role,ordinal)
                slot = group+'-'+kind
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
                        environment = {**os.environ,'PYTHONPATH':str(capture_project/'src')+os.pathsep+str(capture_project)}
                        log = store.root/'campaign/logs'/(slot+'.log')
                        log.parent.mkdir(parents=True,exist_ok=True)
                        _event(store,{'status':'CAPTURE_STARTED','slot':slot,'capture_checkout':str(capture_project)})
                        with log.open('x') as stream:
                            subprocess.run(command,cwd=capture_project,env=environment,stdout=stream,stderr=subprocess.STDOUT,
                                check=True,timeout=config.warmup_seconds+config.active_seconds+config.recovery_seconds+1200)
                inspect_completed_capture(store,slot,project=project)
                _, session = read_binding(store,registration_path(store,group))
                verified = verify_capture(store,plan,session,kind,project=project)
                report, _, _ = audit_local_corpus(store,plan=plan,project=project)
                report_ref = store.write('manifests',report)
                _event(store,{'status':'SOURCE_VERIFIED','slot':slot,'source_result_ref':verified['source_result_ref'],
                    'oracle_ref':verified['oracle_ref'],'dataset_report_ref':report_ref,
                    'healthy_rows':sum(row['healthy'] for row in verified['rows']),
                    'positive_rows':sum(row['risk_label'] == 1 for row in verified['rows'])})
                print(json.dumps({'slot':slot,'status':'SOURCE_VERIFIED','G2':report['status'],
                                  'verified_completed_runs':report['verified_completed_runs']}),flush=True)
            report, _, _ = audit_local_corpus(store,plan=plan,project=project)
            report_ref = store.write('manifests',report)
            _event(store,{'status':'ACQUISITION_BUDGET_COMPLETE','dataset_report_ref':report_ref,
                         'G2':report['status'],'activation_allowed':False})
            return {'dataset_ref':report_ref,'G2':report['status'],'models_fitted':0,'holdout_opened':False,
                    'activation_allowed':False}
        except Exception as exc:
            _event(store,{'status':'STOPPED_REQUIRES_INVESTIGATION','error_type':type(exc).__name__,
                         'reason':str(exc),'source_preserved':True,'automatic_retry':False})
            raise
