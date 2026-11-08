"""Explicit invalid-client-clock accounting; preserve a whole group, never repair it."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.experiments.labels import SLOConfig
from sentinelops.experiments.local_corpus import (
    KINDS, ROLES, PROJECT, is_detector_plan, acquisition_lock, bind, clean_source, docker, docker_clock,
    group_name, read_binding, recipe, registered_sessions, registration_path, validate_end_context,
)
from sentinelops.experiments.local_recovery import _inventory
from sentinelops.ml.local_data import _inside, _session_identity, _source_files, verify_capture
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore, KINDS as ARTIFACT_KINDS

VERSION = 'local-http-clock-discontinuity/v1'
CODE_PATH = 'src/sentinelops/experiments/clock_recovery.py'
CLOCK_ERROR = 'client oracle lacks prospective elapsed-time/source bounds'


def _code_hash(project, commit):
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('clock observer requires committed source')
    return hashlib.sha256(subprocess.check_output(
        ['git', 'show', commit + ':' + CODE_PATH], cwd=project)).hexdigest()


def client_clock_witness(outcomes, started_at, ended_at):
    """Re-read every original attempt; no filtering, timestamp repair or model input."""
    begin, finish = utc_time(started_at), utc_time(ended_at)
    if not begin < finish or not isinstance(outcomes, list) or not 1 <= len(outcomes) <= 500_000:
        raise ValueError('clock witness lacks a bounded original client interval')
    seen, invalid = set(), []
    for index, row in enumerate(outcomes):
        start, end = utc_time(row['started_at']), utc_time(row['completed_at'])
        duration, status = row.get('client_duration_ms'), row.get('status')
        trace, correlation = row.get('trace_id'), row.get('correlation_id')
        if (row.get('request_entry_service') not in BUSINESS_ROUTES
            or not isinstance(trace, str) or not re.fullmatch('[0-9a-f]{32}', trace)
            or not isinstance(correlation, str) or not correlation or (trace, correlation) in seen
            or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0
            or not (type(status) is int and 100 <= status <= 599 or status == 'transport_error')
            or start > finish or end > finish or end < begin):
            raise ValueError('unrelated invalid client source cannot be hidden by clock accounting')
        seen.add((trace, correlation))
        if start < begin or end < start:
            invalid.append({'index': index, 'service': row['request_entry_service'],
                'started_at': row['started_at'], 'completed_at': row['completed_at'],
                'client_duration_ms': duration})
    return {'attempt_count': len(outcomes), 'invalid_count': len(invalid),
            'invalid_sha256': stable_id(invalid), 'samples': invalid[:16]}


def _check_inventory(entries):
    for item in entries:
        path = Path(item['path'])
        if (path.parent.name in ARTIFACT_KINDS and re.fullmatch('[0-9a-f]{64}\\.json', path.name)
            and item['sha256'] != path.stem):
            raise ValueError('original content-addressed source object is corrupt')


def _slot_proof(store, session, plan, kind, project, decision):
    slot = session['group_id'] + '-' + kind
    root = store.root/'runs'/slot
    if not root.exists():
        return {'slot': slot, 'status': 'NOT_STARTED'}
    registration_ref, registration = read_binding(store, root/'registration.json')
    if (registration.get('session_ref') != 'artifact://manifests/' + stable_id(session) + '.json'
        or registration.get('slot_id') != slot or registration.get('source_commit') != session['source_commit']
        or registration.get('policy_sha256') != plan['policy_sha256']
        or stable_id(registration.get('recipe')) != stable_id(asdict(recipe(plan, session['role'], session['ordinal'], kind)))):
        raise ValueError('clock source registration/recipe differs from prospective session')
    paths = list((root/'captures').glob('run-*/run.json'))
    if len(paths) != 1 or not re.fullmatch('run-[0-9a-f]{32}', paths[0].parent.name):
        raise ValueError('clock accounting refuses generic incomplete or duplicate source attempts')
    run_path = _inside(store.root, paths[0].relative_to(store.root).as_posix())
    run = json.loads(run_path.read_bytes())
    validate_payload('experiment-run.schema.json', run)
    source = ArtifactStore(run_path.parent/'truth')
    run_ref = 'artifact://manifests/' + stable_id(run) + '.json'
    if source.read(run_ref) != run:
        raise ValueError('clock source pointer differs from original immutable truth')
    _source_files(run, project)
    expected_root = None if kind == 'healthy' else registration['recipe']['target_service']
    expected_affected = set() if expected_root is None else {expected_root, 'checkout-service'}
    if (run['root_cause_ground_truth'] != expected_root
        or set(run['affected_services_ground_truth']) != expected_affected
        or run['seed'] != registration['recipe']['seed']
        or run['run_id'] != run_path.parent.name or run['session_id'] != session['group_id']
        or run['source_commit'] != session['source_commit'] or run['dirty_worktree']
        or run['image_digests'] != session['context']['images']
        or run['source_profile'] != plan['source_profile'] or run['environment'] != plan['environment']
        or run['parameters'].get('slo') != asdict(SLOConfig())
        or stable_id({k:run['parameters'].get(k) for k in registration['recipe']}) != stable_id(registration['recipe'])
        or run['scenario_id'] != registration['recipe']['scenario']
        or run['declared_split'] != registration['recipe']['split']
        or not utc_time(session['registered_at']) <= utc_time(registration['registered_at'])
                    <= utc_time(run['started_at']) < utc_time(run['ended_at']) <= decision):
        raise ValueError('clock source identity/recipe/interval differs from original session')
    witness = client_clock_witness(source.read(run['request_outcomes_ref'])['outcomes'],
                                   run['started_at'], run['ended_at'])
    base = {'slot': slot, 'run_ref': run_ref, 'ended_at': run['ended_at']}
    if (root/'result.json').exists():
        result_ref, result = read_binding(store, root/'result.json')
        if (result.get('registration_ref') != registration_ref or result.get('run_sha256') != stable_id(run)
            or result.get('run_directory') != run_path.parent.relative_to(store.root).as_posix()
            or result.get('status') != 'completed' or run['status'] != 'completed'
            or not utc_time(run['ended_at']) <= utc_time(result['completed_at']) <= decision):
            raise ValueError('clock completed-source result differs')
        try:
            verify_capture(store, plan, session, kind, project=project, as_of=decision)
        except ValueError as exc:
            if str(exc) != CLOCK_ERROR or not witness['invalid_count']:
                raise ValueError('unexpected source failure is not a client clock disposition') from exc
        else:
            if witness['invalid_count']:
                raise ValueError('clock witness disagrees with original source verifier')
            return {**base, 'status': 'SOURCE_VERIFIED', 'source_result_ref': result_ref}
        base['source_result_ref'] = result_ref
    else:
        if run['status'] != 'failed' or run['failure_reason'] != 'ClientClockDiscontinuity':
            raise ValueError('generic failed/incomplete capture is not a clock failure')
        loaded = run['parameters']['loaded_source_sha256']
        if not any(p.endswith('/src/sentinelops/experiments/client_clock.py') for p in loaded):
            raise ValueError('failed source lacks original clock-guard implementation provenance')
    if not witness['invalid_count']:
        raise ValueError('clock disposition requires original invalid UTC attempt evidence')
    return {**base, 'status': 'CLOCK_INVALID', 'client_ledger_ref': run['request_outcomes_ref'], 'witness': witness}


def _proof(store, session, plan, project, decision):
    proof = [_slot_proof(store, session, plan, kind, project, decision) for kind in KINDS]
    if not any(p['status'] == 'CLOCK_INVALID' for p in proof):
        raise ValueError('healthy/completed groups cannot be replaced based on outcomes')
    return proof


def _validate_observation(session, observed, proof):
    end = max(utc_time(p['ended_at']) for p in proof if 'ended_at' in p)
    validate_end_context(session, observed, end)
    if any(v['restart_count'] != 0 for v in observed['containers'].values()):
        raise ValueError('clock observer refuses restarted source containers')


def _observe(session, source, project, proof):
    actual = json.loads(docker('inspect', *[v['container_id'] for v in session['context']['containers'].values()]))
    values = {}
    for item in actual:
        labels = item['Config'].get('Labels') or {}
        service = labels.get('com.docker.compose.service')
        if labels.get('com.docker.compose.project') != session['compose_project'] or service in values:
            raise ValueError('clock observer encountered foreign/duplicate source')
        values[service] = {'container_id': item['Id'], 'image': item['Image'],
            'running': item['State']['Running'], 'restart_count': item['RestartCount']}
        for field, raw in (('created_at', item['Created']), ('started_at', item['State']['StartedAt']),
                           ('finished_at', item['State']['FinishedAt'])):
            values[service][field], values[service][field + '_source'] = docker_clock(raw), raw
    observed = {'kind': 'local-corpus-context-inspection/v1', 'compose_project': session['compose_project'],
        'containers': values, 'collected_at': timestamp(datetime.now(timezone.utc)),
        'observer_source_commit': source, 'observer_code_sha256': _code_hash(project, source)}
    _validate_observation(session, observed, proof)
    return observed


def read_clock_abandonment(store, session, plan, closed, reference, *, project=PROJECT, as_of=None):
    _session_identity(session, plan)
    decision = as_of or datetime.now(timezone.utc)
    replacement = group_name(plan, session['role'], closed['replacement_ordinal'])
    if (closed.get('closure_version') != VERSION or closed.get('group_id') != session['group_id']
        or closed.get('session_ref') != 'artifact://manifests/' + stable_id(session) + '.json'
        or closed.get('plan_sha256') != stable_id(plan) or closed.get('reason') != 'invalid-client-utc-bounds'
        or closed.get('excluded_whole_group') is not True or closed.get('source_preserved') is not True
        or closed['replacement_ordinal'] <= plan['minimum_groups'][session['role']]
        or replacement == session['group_id'] or closed.get('replacement_group_id') != replacement
        or not utc_time(session['registered_at']) <= utc_time(closed['closed_at']) <= decision):
        raise ValueError('clock group disposition differs from frozen source/role/budget')
    inventory = store.read(closed['inventory_ref'])
    entries = _inventory(store, session['group_id']); _check_inventory(entries)
    if inventory != {'inventory_version': VERSION, 'group_id': session['group_id'], 'files': entries}:
        raise ValueError('clock-invalid original source changed after disposition')
    proof = _proof(store, session, plan, project, utc_time(closed['closed_at']))
    observed = store.read(closed['observation_ref'])
    if (closed.get('source_proof') != proof or observed['collected_at'] != closed['closed_at']
        or observed['observer_code_sha256'] != _code_hash(project, observed['observer_source_commit'])):
        raise ValueError('clock source proof or observer identity changed')
    _validate_observation(session, observed, proof)
    return reference, closed


def abandon_clock_invalid_group(store, plan, *, role, ordinal, replacement_ordinal, project=PROJECT):
    if is_detector_plan(plan):
        raise ValueError("detector-event epoch has no whole-group replacement protocol")
    """Operator-only, whole-group exclusion; never retry, repair UTC or choose a budget."""
    from sentinelops.experiments.local_campaign import campaign_lock
    from sentinelops.experiments.local_recovery import read_abandonment
    group, replacement = group_name(plan, role, ordinal), group_name(plan, role, replacement_ordinal)
    if replacement_ordinal <= plan['minimum_groups'][role] or group == replacement:
        raise ValueError('replacement needs an unused ordinal outside the reserved minimum')
    with campaign_lock(store), acquisition_lock(store):
        if (store.root/'dataset-freeze.json').exists():
            raise ValueError('cannot close a frozen corpus group')
        source = clean_source(project)
        _, session = read_binding(store, registration_path(store, group)); _session_identity(session, plan)
        closed_path = store.root/'sessions'/group/'closed.json'
        if session['role'] != role or session['ordinal'] != ordinal or session['group_id'] != group:
            raise ValueError('clock source session differs from requested role/group')
        if closed_path.exists() or registration_path(store, replacement).exists():
            raise ValueError('group is closed or replacement has already begun')
        for other in registered_sessions(store):
            if ROLES.index(other['role']) > ROLES.index(role):
                raise ValueError('cannot repair an earlier role after a later role has begun')
            prior = read_abandonment(store, other, plan, project=project)
            if prior and prior[1]['replacement_group_id'] == replacement:
                raise ValueError('replacement is already reserved by another group')
        before = _inventory(store, group); _check_inventory(before)
        proof = _proof(store, session, plan, project, datetime.now(timezone.utc))
        observed = _observe(session, source, project, proof)
        if _inventory(store, group) != before or clean_source(project) != source:
            raise ValueError('clock source changed during observation')
        closed = {'closure_version': VERSION, 'group_id': group,
            'session_ref': 'artifact://manifests/' + stable_id(session) + '.json',
            'plan_sha256': stable_id(plan), 'closed_at': observed['collected_at'],
            'reason': 'invalid-client-utc-bounds', 'source_proof': proof,
            'observation_ref': store.write('raw', observed),
            'inventory_ref': store.write('manifests', {'inventory_version': VERSION, 'group_id': group, 'files': before}),
            'excluded_whole_group': True, 'source_preserved': True,
            'replacement_ordinal': replacement_ordinal, 'replacement_group_id': replacement}
        ref = bind(store, closed_path, closed)
        return {'closure_ref': ref, **closed, 'models_fitted': 0, 'activation_allowed': False}
