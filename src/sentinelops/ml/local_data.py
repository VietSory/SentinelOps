"""Recompute prospective local corpus evidence before any numerical fitting.

Registration authorizes acquisition, not health. Certificates are derived from
original observations/client/server records; caller-authored approval flags are
never an admission authority. Sealed final source roots are not opened here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.experiments.labels import SLOConfig, feature_vector
from sentinelops.experiments.local_corpus import KINDS, ROLES, group_name, load_plan, read_binding, recipe, validate_end_context, docker_clock
from sentinelops.experiments.local_labels import oracle_from_requests, window_labels
from sentinelops.experiments.verification import verify_source_replay
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.public_data.policy import load_training_policy, policy_hash
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_KEYS, FEATURE_SET, build_feature_window, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

PROJECT = Path(__file__).resolve().parents[3]


def _inside(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(p in ('..', '.') for p in path.parts):
        raise ValueError('prospective source path escapes the corpus')
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('prospective source cannot traverse symlinks')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('prospective source path escapes the corpus root')
    return target


def _source_files(run, project):
    commit = run['source_commit']
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('prospective source commit identity differs')
    files = run['parameters'].get('loaded_source_sha256')
    if not isinstance(files, dict) or not 1 <= len(files) <= 1000:
        raise ValueError('prospective source lacks bounded frozen implementation provenance')
    required = set()
    import hashlib
    for absolute, expected in files.items():
        relative = None
        for anchor in ('src/sentinelops/', 'contracts/', 'scripts/run_experiment.py', 'configs/s5_development_protocol.json'):
            marker = '/' + anchor
            if marker in absolute:
                relative = anchor + absolute.split(marker, 1)[1]
                break
        if relative is None or '..' in PurePosixPath(relative).parts:
            raise ValueError('unrecognized prospective implementation provenance path')
        try:
            body = subprocess.check_output(['git','show',commit + ':' + relative], cwd=project, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as exc:
            raise ValueError('prospective implementation does not exist in the recorded commit') from exc
        if hashlib.sha256(body).hexdigest() != expected:
            raise ValueError('prospective loaded implementation differs from its source commit')
        required.add(relative)
    if not {'src/sentinelops/experiments/runner.py', 'src/sentinelops/experiments/local_corpus.py',
            'contracts/training-data-policy-v2.schema.json'} <= required:
        raise ValueError('historical/pilot capture is not an ADR-014 prospective producer')


def causal_features(serving, manifest, run):
    """Freeze all event/availability-bounded frames before consulting outcomes."""
    original = serving.read(manifest['normalization_manifest_ref'])
    raw = [serving.read(ref) for ref in original['raw_refs']]
    receipts = defaultdict(list)
    for item in raw:
        receipts[item['bucket_end']].append(utc_time(item['collected_at']))
    observations = [serving.read(ref) for ref in original['observation_refs']]
    frames, excluded = [], Counter()
    for end_text in sorted(receipts):
        end = utc_time(end_text)
        cutoff = max(receipts[end_text])
        if cutoff < end or cutoff-end > timedelta(seconds=60):
            excluded['outside-live-source-lateness-bound'] += len(BUSINESS_ROUTES)
            continue
        for service in BUSINESS_ROUTES:
            frame = build_feature_window(observations, service=service, window_end=end, as_of=cutoff,
                environment=run['environment'], source_profile=run['source_profile'])
            if (not frame['quality']['eligible_for_inference'] or utc_time(frame['window_start']) < utc_time(run['started_at'])
                or end <= utc_time(run['warmup_end_at']) or end > utc_time(run['ended_at'])):
                excluded['ineligible-or-partial-or-warmup'] += 1
                continue
            feature_vector(frame['payload']['features'])
            frames.append(frame)
    return frames, dict(excluded)


def verify_capture(store, plan, session, kind, *, project=PROJECT, as_of=None):
    role = session['role']
    group = group_name(plan, role, session['ordinal'])  # rejects sealed before any run source read
    if group != session['group_id'] or session['policy_sha256'] != policy_hash(load_training_policy()):
        raise ValueError('registered session differs from its approved source/role')
    if session['plan_sha256'] != stable_id(plan) or store.read(session['plan_ref']) != plan:
        raise ValueError('registered session changed the pre-capture plan')
    slot = group + '-' + kind
    root = store.root / 'runs' / slot
    registration_ref, registration = read_binding(store, root / 'registration.json')
    result_ref, result = read_binding(store, root / 'result.json')
    decision = as_of or datetime.now(timezone.utc)
    if utc_time(result['completed_at']) > decision:
        raise ValueError('source result was not available at admission cutoff')
    session_ref = 'artifact://manifests/' + stable_id(session) + '.json'
    if (registration['session_ref'] != session_ref or registration['slot_id'] != slot
        or registration['policy_sha256'] != plan['policy_sha256']
        or stable_id(registration['recipe']) != stable_id(asdict(recipe(plan, role, session['ordinal'], kind)))
        or result['registration_ref'] != registration_ref or result['status'] != 'completed'):
        raise ValueError('prospective result/recipe/role differs from pre-traffic registration')
    parts = PurePosixPath(result['run_directory']).parts
    if len(parts) != 4 or parts[:3] != ('runs', slot, 'captures') or not re.fullmatch('run-[0-9a-f]{32}', parts[3]):
        raise ValueError('prospective source path differs from registered role/slot')
    captured = _inside(store.root, result['run_directory'])
    run = json.loads((captured / 'run.json').read_bytes())
    validate_payload('experiment-run.schema.json', run)
    if (stable_id(run) != result['run_sha256'] or run['run_id'] != parts[3] or run['session_id'] != group
        or run['status'] != 'completed' or run['dirty_worktree']
        or run['source_commit'] != session['source_commit'] or registration['source_commit'] != session['source_commit']
        or run['image_digests'] != session['context']['images'] or run['source_profile'] != plan['source_profile']
        or run['environment'] != plan['environment']):
        raise ValueError('completed prospective run identity/source differs')
    if not (utc_time(session['registered_at']) <= utc_time(registration['registered_at']) <= utc_time(run['started_at'])
            < utc_time(run['ended_at']) <= utc_time(result['completed_at'])):
        raise ValueError('capture is not prospectively registered or its clocks conflict')
    actual_recipe = {key:run['parameters'].get(key) for key in registration['recipe']}
    if stable_id(actual_recipe) != stable_id(registration['recipe']):
        raise ValueError('actual workload recipe differs from prospective registration')
    if (run['scenario_id'] != registration['recipe']['scenario']
        or run['declared_split'] != registration['recipe']['split']):
        raise ValueError('run scenario/split differs from its predeclared source recipe')
    slo = SLOConfig()
    if run['parameters'].get('slo') != asdict(slo):
        raise ValueError('prospective oracle changed the frozen local SLO definition')
    _source_files(run, project)
    _, receipt = read_binding(store, root/'end-context.json')
    if (receipt.get('context_receipt_version') != 'local-http-context-end/v1'
        or receipt['source_result_ref'] != result_ref or receipt['session_ref'] != session_ref):
        raise ValueError('end-context receipt belongs to another prospective source')
    inspection = store.read(receipt['inspection_ref'])
    if utc_time(inspection['collected_at']) > decision:
        raise ValueError('source context was not observed by admission cutoff')
    observer = inspection.get('observer_source_commit')
    if not isinstance(observer, str) or not re.fullmatch('[0-9a-f]{40}', observer):
        raise ValueError('context observer lacks a versioned implementation source')
    import hashlib
    try:
        body = subprocess.check_output(['git','show',observer+':src/sentinelops/experiments/local_corpus.py'],
                                       cwd=project, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        raise ValueError('context observer source cannot be reproduced') from exc
    if hashlib.sha256(body).hexdigest() != inspection.get('observer_code_sha256') or b'def inspect_completed_capture(' not in body:
        raise ValueError('context observation implementation provenance differs')
    validate_end_context(session, inspection, utc_time(run['ended_at']))
    serving, truth = ArtifactStore(captured / 'serving'), ArtifactStore(captured / 'truth')
    manifest = serving.read(run['source_manifest_ref'])
    verify_source_replay(serving, manifest, run)
    frames, excluded = causal_features(serving, manifest, run)
    frozen_features = stable_id(frames)
    full_ref = run['parameters'].get('full_logs_ref')
    if not full_ref:
        raise ValueError('prospective capture lacks unsampled request-start oracle')
    full = truth.read(full_ref)
    completions = serving.read(run['logs_ref'])
    ledger = truth.read(run['request_outcomes_ref'])
    if full.get('kind') != 'full-experiment-logs':
        raise ValueError('prospective full oracle source kind differs')
    oracle = oracle_from_requests(full['records'], completions['records'], ledger['outcomes'],
                                  started_at=run['started_at'], ended_at=run['ended_at'], slo=slo)
    # Control observations corroborate the kind of experiment but never supply
    # health, outcome time or a positive risk label without independent evidence.
    ramp = []
    if run['scenario_id'] == 'S3':
        target = registration['recipe']['target_service']
        if run['root_cause_ground_truth'] != target or set(run['affected_services_ground_truth']) != {target, 'checkout-service'}:
            raise ValueError('registered target differs from actual recorded intervention')
        lower, upper = utc_time(run['fault_start_at']).timestamp()*1000, utc_time(run['recovery_start_at']).timestamp()*1000
        ramp = sorted((r['timestamp_unix_ms'], r['delay_ms']) for r in full['records'] if
            r.get('service') == target and r.get('event') == 'fault_configuration_changed'
            and r.get('mode') == 'latency' and type(r.get('delay_ms')) is int
            and type(r.get('timestamp_unix_ms')) is int and lower <= r['timestamp_unix_ms'] < upper)
    gradual = (len({v for _,v in ramp}) >= 3 and all(0 < v <= registration['recipe']['maximum_delay_ms'] for _,v in ramp)
               and all(a[1] <= b[1] for a,b in zip(ramp, ramp[1:])))
    observation_objects = [(ref,serving.read(ref)) for ref in serving.read(manifest['normalization_manifest_ref'])['observation_refs']]
    observations = [value for _,value in observation_objects]
    oracle_ref = store.write('manifests', {'certificate_version':'local-http-oracle/v1',
        'source_result_ref':result_ref,'source_logs_ref':run['logs_ref'],'source_full_logs_ref':full_ref,
        'source_client_ledger_ref':run['request_outcomes_ref'],'oracle':oracle})
    rows = []
    for frame in frames:
        labels = window_labels(frame, oracle, gradual_run=gradual, healthy_reference_run=run['scenario_id'] == 'S0')
        source_observations = [o for o in observations if o['service'] == frame['service']
            and frame['window_start'] < o['bucket_end'] <= frame['window_end']
            and utc_time(o['available_at']) <= utc_time(frame['as_of']) and o['payload'] is not None]
        if labels['healthy'] and any(o['payload']['p95_latency_ms'] > slo.latency_ms or o['payload']['error_rate'] > slo.error_rate
            or o['measurements']['request_count'] < slo.minimum_requests for o in source_observations):
            labels['healthy'] = False
        ref = store.write('features', frame)
        inputs = {r.removeprefix('observation://') for r in frame['provenance']['input_refs']}
        source_refs = sorted(r for r,o in observation_objects if o['record_id'] in inputs
                             and utc_time(o['available_at']) <= utc_time(frame['as_of']))
        label_ref = store.write('manifests', {'certificate_version':'local-http-window-label/v1','feature_ref':ref,
            'source_result_ref':result_ref,'oracle_ref':oracle_ref,'source_observation_refs':source_refs,**labels})
        rows.append({'service':frame['service'], 'split':role, 'group_id':group, 'run_id':run['run_id'],
            'window_start':frame['window_start'], 'window_end':frame['window_end'],
            'features':[frame['payload']['features'][key] for key in FEATURE_KEYS],
            'feature_ref':ref, 'label_ref':label_ref,'source_result_ref':result_ref, **labels})
    if stable_id(frames) != frozen_features:
        raise ValueError('outcome labeling changed causal predictor frames')
    return {'source_result_ref':result_ref, 'registration_ref':registration_ref, 'run_id':run['run_id'],
        'group_id':group, 'role':role, 'kind':kind, 'started_at':run['started_at'], 'ended_at':run['ended_at'],
        'oracle':oracle,'oracle_ref':oracle_ref, 'rows':rows, 'excluded':excluded, 'causal_features_sha256':frozen_features,
        'observed_gradual_control_steps':len(ramp), 'gradual_control_verified':gradual}


def _session_identity(session, plan):
    group = group_name(plan, session['role'], session['ordinal'])
    if (session.get('registration_version') != 'local-http-session/v1' or session.get('group_id') != group
        or session.get('fitting_allowed_by_registration') is not False
        or type(session.get('initial_payment_rows')) is not int or session['initial_payment_rows'] != 0
        or session.get('compose_project') != 'sentinelops-corpus-' + group
        or session.get('policy_sha256') != policy_hash(load_training_policy())
        or session.get('plan_sha256') != stable_id(plan)):
        raise ValueError('prospective registration identity/authority differs')
    context = session['context']
    if set(context['images']) != set(BUSINESS_ROUTES):
        raise ValueError('prospective images omit/add a business identity')
    lower, upper = utc_time(session['context_created_after']), utc_time(session['registered_at'])
    expected_services = set(BUSINESS_ROUTES) | {'postgres','prometheus','otel-collector','jaeger'}
    if set(context['containers']) != expected_services or not lower <= upper:
        raise ValueError('prospective context scope/clock differs')
    for service, item in context['containers'].items():
        if not re.fullmatch('[0-9a-f]{64}', item['container_id']) or not lower <= utc_time(item['created_at']) <= upper:
            raise ValueError('prospective source is not a fresh identified container')
        if item['created_at'] != docker_clock(item['created_at_source']):
            raise ValueError('prospective container creation clock conversion differs')
        if service in BUSINESS_ROUTES and item['image'] != context['images'][service]:
            raise ValueError('prospective business image identity differs')
    volume = context['database_volume']
    if not volume['name'].startswith(session['compose_project'] + '_') or not lower <= utc_time(volume['created_at']) <= upper:
        raise ValueError('prospective database volume is not fresh/owned')
    if volume['created_at'] != docker_clock(volume['created_at_source']):
        raise ValueError('prospective volume creation clock conversion differs')


def validate_chronology(intervals):
    ordered = sorted(interval for values in intervals.values() for interval in values)
    if any(a[1] > b[0] for a,b in zip(ordered,ordered[1:])):
        raise ValueError('single-testbed prospective runs overlap')
    for index, earlier in enumerate(ROLES[:-1]):
        for later in ROLES[index+1:-1]:
            if intervals.get(earlier) and intervals.get(later) and max(b for _,b in intervals[earlier]) > min(a for a,_ in intervals[later]):
                raise ValueError('prospective whole-group chronological split order violated')
    return ordered


def audit_local_corpus(store, *, plan=None, project=PROJECT, now=None):
    """Audit all prospectively registered nonfinal groups; emit exact deficits.

    Pending/failed slots never supply rows. Corrupt completed evidence blocks
    admission and is retained in a separate quarantine inventory. Only train and
    calibration rows are returned to numerical fitting; development is audited
    for independent support and never passed to estimator fitting/preprocessing.
    """
    plan = load_plan() if plan is None else plan
    policy = load_training_policy()
    if plan['policy_sha256'] != policy_hash(policy):
        raise ValueError('corpus differs from the current approved training contract')
    created = now or datetime.now(timezone.utc)
    cases, pending, failed, quarantine, sessions = [], [], [], [], []
    all_rows, intervals, owners = [], defaultdict(list), set()
    source_contexts = set()
    for path in sorted((store.root / 'sessions').glob('*/registration.json')):
        _, session = read_binding(store, path)
        if session.get('role') == 'sealed-final':
            # Registration metadata can be accounted for without following a
            # single raw/result/label reference for the sealed role.
            continue
        _session_identity(session, plan)
        if path.parent.name != session['group_id'] or session['group_id'] in owners:
            raise ValueError('session is duplicated or relabelled under another group')
        owners.add(session['group_id'])
        context_ids = {v['container_id'] for v in session['context']['containers'].values()}
        if source_contexts.intersection(context_ids):
            raise ValueError('acquisition groups reuse a source container epoch')
        source_contexts.update(context_ids)
        sessions.append(session)
        for kind in KINDS:
            slot = session['group_id'] + '-' + kind
            root = store.root / 'runs' / slot
            if not (root / 'result.json').exists():
                run_files = list((root / 'captures').glob('run-*/run.json'))
                if run_files:
                    data = json.loads(run_files[0].read_bytes())
                    if data.get('status') == 'failed':
                        failed.append({'slot':slot,'reason':data.get('failure_reason'),'source_preserved':True})
                        continue
                pending.append({'slot':slot,'registered':(root/'registration.json').exists(),
                                'status':'CAPTURING_OR_INCOMPLETE' if root.exists() else 'NOT_CAPTURED'})
                continue
            if not (root / 'end-context.json').exists():
                pending.append({'slot':slot,'registered':True,'status':'AWAITING_SOURCE_CONTEXT_OBSERVATION'})
                continue
            try:
                case = verify_capture(store, plan, session, kind, project=project, as_of=created)
            except (ValueError, FileNotFoundError, KeyError, TypeError) as exc:
                quarantine.append({'slot':slot,'reason':str(exc),'error_type':type(exc).__name__,'source_preserved':True})
                continue
            cases.append(case)
            all_rows.extend(case['rows'])
            intervals[session['role']].append((utc_time(case['started_at']),utc_time(case['ended_at'])))
    ordered = validate_chronology(intervals)
    counts = {role:{service:{'healthy_windows':0,'risk_positive_windows':0,'risk_negative_windows':0,'risk_unknown_windows':0,
                            'healthy_groups':0,'precursor_groups':0} for service in BUSINESS_ROUTES} for role in ROLES[:-1]}
    healthy_groups, precursor_groups = defaultdict(set), defaultdict(set)
    for row in all_rows:
        target = counts[row['split']][row['service']]
        target['healthy_windows'] += int(row['healthy'])
        if row['healthy']: healthy_groups[row['split'],row['service']].add(row['group_id'])
        target['risk_positive_windows'] += int(row['risk_label'] == 1)
        target['risk_negative_windows'] += int(row['risk_label'] == 0)
        target['risk_unknown_windows'] += int(row['risk_label'] is None)
        if row['risk_label'] == 1: precursor_groups[row['split'],row['service']].add(row['group_id'])
    conditions = []
    for role in ('train','calibration'):
        for service, values in counts[role].items():
            values['healthy_groups'] = len(healthy_groups[role,service])
            values['precursor_groups'] = len(precursor_groups[role,service])
            for key, floor in (('healthy_windows',policy['healthy_windows_per_service'][role]),
                               ('healthy_groups',policy['minimum_healthy_groups_per_service'][role]),
                               ('precursor_groups',policy['minimum_precursor_groups_per_service'][role]),
                               ('risk_positive_windows',1),('risk_negative_windows',1)):
                if values[key] < floor:
                    conditions.append({'condition':role+'/'+service+'/'+key,'available':values[key],'required':floor})
    for service, values in counts['development-validation'].items():
        values['healthy_groups'] = len(healthy_groups['development-validation',service])
        values['precursor_groups'] = len(precursor_groups['development-validation',service])
        for key, floor in (('healthy_groups',5),('precursor_groups',1),('risk_positive_windows',1),('risk_negative_windows',1)):
            if values[key] < floor:
                conditions.append({'condition':'development-validation/'+service+'/'+key,'available':values[key],'required':floor})
    development_precursor_groups = {row['group_id'] for row in all_rows
        if row['split'] == 'development-validation' and row['risk_label'] == 1}
    if len(development_precursor_groups) < 5:
        conditions.append({'condition':'development-validation/verified-gradual-groups',
                           'available':len(development_precursor_groups),'required':5})
    if pending: conditions.append({'condition':'pending-registered-slots','available':len(pending),'required':0})
    if quarantine: conditions.append({'condition':'corrupt-completed-source','available':len(quarantine),'required':0})
    if ordered and created < max(end for _,end in ordered):
        raise ValueError('corpus cannot be audited before its captures end')
    report = {'dataset_version':'local-http-admission/v1','source':'local_http','training_data_policy_hash':policy_hash(policy),
        'feature_set_version':FEATURE_SET,'ordered_features':list(FEATURE_KEYS),'source_profile':plan['source_profile'],
        'services':sorted(BUSINESS_ROUTES),'plan_sha256':stable_id(plan),'created_at':timestamp(created),
        'status':'BLOCKED' if conditions else 'READY_FOR_FITTING','training_ready':not conditions,
        'counts':counts,'failed_conditions':conditions,'pending_slots':pending,'failed_slots':failed,'quarantined_slots':quarantine,
        'registered_groups':len(sessions),'verified_completed_runs':len(cases),'verified_feature_rows':len(all_rows),
        'source_result_refs':[c['source_result_ref'] for c in cases],
        'oracle_certificate_refs':[c['oracle_ref'] for c in cases],
        'source_inventory_sha256':stable_id([{'group':s['group_id'],'ref':'artifact://manifests/'+stable_id(s)+'.json'} for s in sessions]),
        'rows_sha256':stable_id(all_rows),'activation_allowed':False,'holdout_opened':False,
        'limitations':['Whole groups are the denominator; overlapping windows are correlated.',
            'Fresh context/source integrity is not a general proof of statistical IID independence.',
            'Current source registration alone cannot admit a model or a healthy interval.',
            'Development outcomes are audited for readiness; only train/calibration rows may enter fitting.']}
    return report, all_rows, cases


def admitted_local_rows(store, report):
    if report.get('dataset_version') != 'local-http-admission/v1':
        raise DatasetNotReady('not an approved prospective local corpus version')
    if report.get('training_ready') is not True:
        raise DatasetNotReady('prospective corpus/G2 is blocked; no estimator constructed')
    if not isinstance(report.get('created_at'), str):
        raise DatasetNotReady('prospective admission lacks a source audit cutoff')
    fresh, rows, _ = audit_local_corpus(store, now=utc_time(report['created_at']))
    if fresh != report or not fresh['training_ready']:
        raise DatasetNotReady('prospective raw-source/G2 admission is incomplete, changed or self-attested')
    return [row for row in rows if row['split'] in ('train','calibration')]


def freeze_local_corpus(store, report):
    from sentinelops.experiments.local_corpus import acquisition_lock
    with acquisition_lock(store):
        return _freeze_local_corpus(store, report)


def _freeze_local_corpus(store, report):
    """Bind one fully recomputed ready dataset; never freeze an incomplete gate."""
    from sentinelops.experiments.local_corpus import bind
    rows = admitted_local_rows(store, report)
    if not rows:
        raise DatasetNotReady('no source-admitted prospective training rows')
    from sentinelops.experiments.local_corpus import clean_source
    clean_source(PROJECT)
    path = store.root/'dataset-freeze.json'
    if path.exists():
        reference, value = read_binding(store, path)
        if value != report:
            raise ValueError('prospective dataset is already frozen to another inventory')
        return reference
    return bind(store,path,report)
