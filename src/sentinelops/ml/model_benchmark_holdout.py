"""Validate local holdout capture receipts before reading outcome labels."""
import json
from pathlib import Path
import re
import hashlib

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_labels import oracle_from_requests
from sentinelops.experiments.verification import verify_source_replay
from sentinelops.ml.model_benchmark_protocol import _keys, _ref, verify_protocol
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.telemetry.store import ArtifactStore


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def verify_capture_group(group, receipt, *, matrix_sha256, candidates_sha256, source_profile):
    """Check completeness and identity against the registered matrix and candidates."""
    _keys(receipt, 'schema_version role group_id matrix_sha256 candidates_sha256 source_profile clock slots')
    if (not _sha(matrix_sha256) or not _sha(candidates_sha256)
            or not re.fullmatch('bench-final-[0-9]{2}', group['group_id'])
            or receipt['schema_version'] != 'benchmark-holdout-group/v1'
            or receipt['role'] != 'sealed-final'
            or receipt['group_id'] != group['group_id']
            or receipt['matrix_sha256'] != matrix_sha256
            or receipt['candidates_sha256'] != candidates_sha256
            or receipt['source_profile'] != source_profile
            or receipt['clock'] != 'arrival-aware'):
        raise ValueError('holdout source, frozen identity or clock differs')
    slots = {}
    run_ids = set()
    for item in receipt['slots']:
        _keys(item, 'slot_id run_id status source_manifest_sha256 oracle_certificate_sha256 raw_refs')
        slot, run = item['slot_id'], item['run_id']
        if (slot in slots or not isinstance(run, str) or not re.fullmatch('run-[0-9a-f]{32}', run)
                or run in run_ids):
            raise ValueError('duplicate slot or invalid/reused run ID')
        if item['status'] != 'completed':
            raise ValueError('incomplete holdout slot')
        if not all(_sha(item[key]) for key in ('source_manifest_sha256', 'oracle_certificate_sha256')):
            raise ValueError('holdout receipt requires source and oracle pins')
        raw = item['raw_refs']
        if (not isinstance(raw, list) or not raw or any(not isinstance(ref, str)
                or not re.fullmatch(r'artifact://raw/[0-9a-f]{64}\.json', ref) for ref in raw)
                or len(set(raw)) != len(raw)):
            raise ValueError('holdout slot requires unique content-addressed raw references')
        slots[slot] = item
        run_ids.add(run)
    expected = [slot['slot_id'] for slot in group['slots']]
    if set(slots) != set(expected) or len(expected) != len(set(expected)):
        raise ValueError('holdout receipt does not cover the registered slots')
    return [slots[slot] for slot in expected]


def verify_capture_inventory(matrix, receipts, *, matrix_sha256, candidates_sha256):
    """Return group-ordered inventory while rejecting run reuse across the cohort."""
    if stable_id(matrix) != matrix_sha256 or matrix['role'] != 'sealed-final':
        raise ValueError('holdout matrix differs from the trusted pin')
    groups = matrix['groups']
    if set(receipts) != {group['group_id'] for group in groups}:
        raise ValueError('holdout inventory does not cover the registered groups')
    inventory, used_runs = {}, set()
    for group in groups:
        group_id = group['group_id']
        slots = verify_capture_group(group, receipts[group_id], matrix_sha256=matrix_sha256,
                                     candidates_sha256=candidates_sha256, source_profile=matrix['source_profile'])
        runs = {item['run_id'] for item in slots}
        if group_id in inventory or runs & used_runs:
            raise ValueError('holdout inventory reuses a group or run')
        used_runs.update(runs)
        inventory[group_id] = slots
    if len(used_runs) != matrix['slot_count']:
        raise ValueError('holdout run count differs from the registered matrix')
    return inventory


def verify_capture_source(run_path, receipt, slot, *, group_id, source_profile, source_commit, certificate_store,
                          registered_at=None):
    """Rebuild one inventoried slot's serving and truth artifacts from archived inputs."""
    run_path = Path(run_path)
    run = json.loads(run_path.read_bytes())
    if registered_at is not None and utc_time(run['started_at']) < utc_time(registered_at):
        raise ValueError('holdout capture predates its registered design')
    if (receipt['slot_id'] != slot['slot_id']
            or run_path.name != 'run.json' or run_path.parent.name != receipt['run_id']
            or run['run_id'] != receipt['run_id'] or run['session_id'] != group_id
            or run['status'] != 'completed' or run['declared_split'] != 'test'
            or run['dirty_worktree'] is not False or run['environment'] != 'local'
            or run['source_profile'] != source_profile or run['source_commit'] != source_commit
            or run['scenario_id'] != slot['recipe']['scenario']
            or any(run['parameters'].get(key) != value for key, value in slot['recipe'].items())):
        raise ValueError('holdout run differs from the registered slot or capture revision')
    manifest_ref = f"artifact://manifests/{receipt['source_manifest_sha256']}.json"
    if run['source_manifest_ref'] != manifest_ref:
        raise ValueError('holdout run does not match the pinned serving manifest')
    serving = ArtifactStore(run_path.parent / 'serving')
    manifest = serving.read(manifest_ref)
    if (manifest['source_profile'] != source_profile
            or sorted(manifest['raw_refs']) != sorted(receipt['raw_refs'])):
        raise ValueError('holdout raw references differ from the source manifest')
    if any(serving.read(ref).get('source_profile') != source_profile for ref in receipt['raw_refs']):
        raise ValueError('holdout raw profile differs from the registered source profile')
    verify_source_replay(serving, manifest, run)

    oracle_ref = f"artifact://manifests/{receipt['oracle_certificate_sha256']}.json"
    certificate = certificate_store.read(oracle_ref)
    result_ref = certificate['source_result_ref']
    result = certificate_store.read(result_ref)
    full_ref = run['parameters']['full_logs_ref']
    logs_ref, ledger_ref = run['logs_ref'], run['request_outcomes_ref']
    if (certificate['certificate_version'] != 'local-http-oracle/v1'
            or result['status'] != 'completed' or result['run_sha256'] != stable_id(run)
            or certificate['source_logs_ref'] != logs_ref
            or certificate['source_full_logs_ref'] != full_ref
            or certificate['source_client_ledger_ref'] != ledger_ref):
        raise ValueError('holdout oracle certificate differs from the completed run')
    truth = ArtifactStore(run_path.parent / 'truth')
    full, logs, ledger = truth.read(full_ref), serving.read(logs_ref), truth.read(ledger_ref)
    if full['kind'] != 'full-experiment-logs' or logs['kind'] != 'serving-logs':
        raise ValueError('holdout oracle requires complete request sources')
    oracle = oracle_from_requests(full['records'], logs['records'], ledger['outcomes'],
                                  started_at=run['started_at'], ended_at=run['ended_at'])
    if oracle != certificate['oracle']:
        raise ValueError('holdout oracle certificate does not reproduce from requests')
    return {'group_id': group_id, 'run_id': run['run_id'],
            'source_result_ref': result_ref, 'oracle_ref': oracle_ref}


def verify_candidate_freeze(root, candidates_sha256):
    """Verify both candidate and protocol freezes before any capture data is read."""
    root = Path(root).resolve()
    candidate_path = root / 'freeze/candidates.json'
    if not candidate_path.is_file():
        raise ValueError('candidate freeze required before reading holdout sources')
    candidate_bytes = candidate_path.read_bytes()
    if hashlib.sha256(candidate_bytes).hexdigest() != candidates_sha256:
        raise ValueError('candidate freeze differs from the trusted pin')
    candidate = json.loads(candidate_bytes)
    frozen = verify_protocol(root)
    if (frozen['status'] != 'ready'
            or candidate.get('schema_version') != 'benchmark-candidates-freeze/development-v1'
            or candidate.get('protocol_sha256') != frozen['protocol_sha256']
            or candidate.get('holdout_opened') is not False
            or candidate.get('activation_allowed') is not False
            or any(candidate.get('lanes', {}).get(lane, {}).get('status') != 'ready' for lane in ('a', 'b'))):
        raise ValueError('verified protocol and candidates required before reading holdout sources')
    return frozen


def verify_capture_registration(root, *, registration_sha256, matrix_sha256, candidates_sha256,
                                capture_commit, frozen):
    """Check the independently pinned prospective design before any run source is read."""
    root = Path(root).resolve()
    content = _ref(root, {'path': 'capture/registration.json', 'sha256': registration_sha256})
    registration = json.loads(content)
    _keys(registration, 'schema_version protocol_sha256 protocol_file_sha256 candidates_sha256 matrix_sha256 '
                        'capture_commit registered_at pilot_receipt')
    protocol_path = root / 'freeze/protocol.json'
    if (registration['schema_version'] != 'benchmark-holdout-registration/v1'
            or registration['protocol_sha256'] != frozen['protocol_sha256']
            or registration['protocol_file_sha256'] != hashlib.sha256(protocol_path.read_bytes()).hexdigest()
            or registration['candidates_sha256'] != candidates_sha256
            or registration['matrix_sha256'] != matrix_sha256
            or registration['capture_commit'] != capture_commit):
        raise ValueError('holdout registration differs from the frozen identities')
    utc_time(registration['registered_at'])
    pilot = json.loads(_ref(root, registration['pilot_receipt']))
    if (pilot.get('schema_version') != 'benchmark-low-ramp-pilot/development-v1'
            or pilot.get('status') != 'ready' or pilot.get('maximum_delay_ms') != 400
            or pilot.get('checker_source_commit') != capture_commit
            or pilot.get('holdout_opened') is not False or pilot.get('activation_allowed') is not False
            or set(pilot.get('cases', {})) != {'payment-gradual', 'inventory-gradual'}):
        raise ValueError('verified 400 ms development pilot receipt required')
    runs, results, oracles = set(), set(), set()
    for case in pilot['cases'].values():
        run_id = case.get('run_id')
        if (not isinstance(run_id, str) or not re.fullmatch('run-[0-9a-f]{32}', run_id) or run_id in runs
                or type(case.get('control_steps')) is not int or case['control_steps'] < 3
                or any(not isinstance(case.get(key), str) or not re.fullmatch(r'artifact://manifests/[0-9a-f]{64}\.json', case[key])
                       for key in ('source_result_ref', 'oracle_ref'))
                or case.get('source_result_ref') in results or case.get('oracle_ref') in oracles
                or set(case.get('services', {})) != set(BUSINESS_ROUTES)):
            raise ValueError('incomplete low-ramp pilot evidence')
        runs.add(run_id)
        results.add(case['source_result_ref'])
        oracles.add(case['oracle_ref'])
        for item in case['services'].values():
            if (type(item.get('verified_minutes')) is not int or item['verified_minutes'] < 1
                    or type(item.get('unknown_minutes')) is not int or item['unknown_minutes'] != 0
                    or type(item.get('latency_episodes')) is not int or item['latency_episodes'] != 0):
                raise ValueError('low-ramp pilot lacks verified no-breach coverage')
    return registration


def verify_frozen_capture_sources(root, matrix, receipts, run_paths, *, matrix_sha256, candidates_sha256,
                                  capture_commit, registration_sha256, certificate_store):
    """Check freezes, prospective registration and complete inventory before source reads."""
    root = Path(root).resolve()
    frozen = verify_candidate_freeze(root, candidates_sha256)
    if not isinstance(capture_commit, str) or not re.fullmatch('[0-9a-f]{40}', capture_commit):
        raise ValueError('holdout capture requires an explicit committed source')
    registration = verify_capture_registration(root, registration_sha256=registration_sha256,
        matrix_sha256=matrix_sha256, candidates_sha256=candidates_sha256, capture_commit=capture_commit, frozen=frozen)
    inventory = verify_capture_inventory(matrix, receipts, matrix_sha256=matrix_sha256,
                                         candidates_sha256=candidates_sha256)
    slots = {slot['slot_id']: (group['group_id'], slot)
             for group in matrix['groups'] for slot in group['slots']}
    if set(run_paths) != set(slots):
        raise ValueError('holdout source paths do not cover the registered slots')
    verified = []
    for group_id, rows in inventory.items():
        for receipt in rows:
            _, slot = slots[receipt['slot_id']]
            verified.append(verify_capture_source(
                run_paths[receipt['slot_id']], receipt, slot, group_id=group_id,
                source_profile=matrix['source_profile'], source_commit=capture_commit,
                certificate_store=certificate_store, registered_at=registration['registered_at']))
    return verified
