"""Validate local holdout capture receipts before reading outcome labels."""
import json
from pathlib import Path
import re
import hashlib

from sentinelops.experiments.local_labels import oracle_from_requests
from sentinelops.experiments.verification import verify_source_replay
from sentinelops.ml.model_benchmark_protocol import _keys, verify_protocol
from sentinelops.telemetry.core import stable_id
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


def verify_capture_source(run_path, receipt, slot, *, group_id, source_profile, source_commit, certificate_store):
    """Rebuild one inventoried slot's serving and truth artifacts from archived inputs."""
    run_path = Path(run_path)
    run = json.loads(run_path.read_bytes())
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


def verify_frozen_capture_sources(root, matrix, receipts, run_paths, *, matrix_sha256, candidates_sha256,
                                  capture_commit, certificate_store):
    """Check freezes and metadata before source reads.

    The caller must independently pin matrix and capture_commit before collection.
    This function does not certify prospective registration or seal the capture.
    """
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
    if not isinstance(capture_commit, str) or not re.fullmatch('[0-9a-f]{40}', capture_commit):
        raise ValueError('holdout capture requires an explicit committed source')
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
                certificate_store=certificate_store))
    return verified
