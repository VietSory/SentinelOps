"""Seal one completed holdout capture without selecting by its outcome."""
import json
from pathlib import Path
import re

from sentinelops.experiments.local_corpus import acquisition_lock
from sentinelops.experiments.local_labels import oracle_from_requests
from sentinelops.experiments.verification import verify_source_replay
from sentinelops.ml.model_benchmark_capture_context import load_capture_design, seal_slot_context, verify_slot_context
from sentinelops.ml.model_benchmark_holdout import verify_capture_group, verify_capture_order, verify_capture_source
from sentinelops.ml.model_benchmark_protocol import _bytes, publish_freeze
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore


def seal_slot(root, group_id, slot_id, run_id, registration_sha256, *, project):
    """Verify and seal a completed slot; never start, retry or select a workload."""
    root, project = Path(root).resolve(), Path(project).resolve()
    registration, matrix, _ = load_capture_design(root, registration_sha256, project=project)
    groups = [group for group in matrix['groups'] if group['group_id'] == group_id]
    slots = [slot for group in groups for slot in group['slots'] if slot['slot_id'] == slot_id]
    if len(groups) != 1 or len(slots) != 1 or not re.fullmatch('run-[0-9a-f]{32}', run_id):
        raise ValueError('run is not part of the registered holdout slot')
    seal_slot_context(root, group_id, slot_id, run_id, registration_sha256, project=project)
    store = ArtifactStore(root)
    with acquisition_lock(store):
        current, current_matrix, _ = load_capture_design(root, registration_sha256, project=project)
        if current != registration or current_matrix != matrix:
            raise ValueError('holdout design changed during slot sealing')
        verify_capture_order(root, matrix, group_id, slot_id, matrix_sha256=registration['matrix_sha256'],
                             candidates_sha256=registration['candidates_sha256'])
        slot = slots[0]
        directory = root / 'capture/groups' / group_id / 'slots' / slot_id
        run_dir = directory / 'runs' / run_id
        if list((directory / 'runs').iterdir()) != [run_dir]:
            raise ValueError('holdout slot must contain exactly one run')
        run_path = run_dir / 'run.json'
        run = json.loads(run_path.read_bytes())
        verify_slot_context(root, group_id, slot, run_id, registration_sha256,
            capture_commit=registration['capture_commit'], run_path=run_path,
            source_profile=matrix['source_profile'], registered_at=registration['registered_at'])
        path = directory / 'receipt.json'
        if path.exists():
            receipt = json.loads(path.read_bytes())
        else:
            serving, truth = ArtifactStore(run_dir / 'serving'), ArtifactStore(run_dir / 'truth')
            manifest_ref = run['source_manifest_ref']
            manifest = serving.read(manifest_ref)
            if manifest.get('source_profile') != matrix['source_profile']:
                raise ValueError('holdout serving source profile differs')
            verify_source_replay(serving, manifest, run)
            full_ref, logs_ref, ledger_ref = (run['parameters']['full_logs_ref'], run['logs_ref'],
                                              run['request_outcomes_ref'])
            full, logs, ledger = truth.read(full_ref), serving.read(logs_ref), truth.read(ledger_ref)
            if full.get('kind') != 'full-experiment-logs' or logs.get('kind') != 'serving-logs':
                raise ValueError('holdout oracle requires complete request sources')
            oracle = oracle_from_requests(full['records'], logs['records'], ledger['outcomes'],
                                          started_at=run['started_at'], ended_at=run['ended_at'])
            result_ref = store.write('manifests', {'capture_result_version': 'benchmark-holdout-result/v1',
                'run_id': run_id, 'run_sha256': stable_id(run), 'status': run['status'],
                'registration_sha256': registration_sha256})
            oracle_ref = store.write('manifests', {'certificate_version': 'local-http-oracle/v1',
                'source_result_ref': result_ref, 'source_logs_ref': logs_ref, 'source_full_logs_ref': full_ref,
                'source_client_ledger_ref': ledger_ref, 'oracle': oracle})
            receipt = {'slot_id': slot_id, 'run_id': run_id, 'status': 'completed',
                'source_manifest_sha256': manifest_ref.removeprefix('artifact://manifests/').removesuffix('.json'),
                'oracle_certificate_sha256': oracle_ref.removeprefix('artifact://manifests/').removesuffix('.json'),
                'raw_refs': manifest['raw_refs']}
        envelope = {'schema_version': 'benchmark-holdout-group/v1', 'role': 'sealed-final', 'group_id': group_id,
            'matrix_sha256': registration['matrix_sha256'], 'candidates_sha256': registration['candidates_sha256'],
            'source_profile': matrix['source_profile'], 'clock': 'arrival-aware', 'slots': [receipt]}
        verify_capture_group({'group_id': group_id, 'slots': [slot]}, envelope,
            matrix_sha256=registration['matrix_sha256'], candidates_sha256=registration['candidates_sha256'],
            source_profile=matrix['source_profile'])
        verify_capture_source(run_path, receipt, slot, group_id=group_id, source_profile=matrix['source_profile'],
            source_commit=registration['capture_commit'], certificate_store=store,
            registered_at=registration['registered_at'])
        publish_freeze(path, _bytes(receipt), conflict='different holdout slot receipt already exists')
        return receipt
