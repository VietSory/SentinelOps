"""Acquire one complete registered holdout group and retain every source run."""
import asyncio
import hashlib
import json
from pathlib import Path

from sentinelops.experiments.local_campaign import campaign_lock
from sentinelops.experiments.local_corpus import configure_compose, docker
from sentinelops.experiments.runner import ExperimentConfig, execute
from sentinelops.ml.model_benchmark_capture_context import (
    inspect_group_continuity, load_capture_design, prepare_group, register_slot, verify_slot_context,
)
from sentinelops.ml.model_benchmark_capture_receipt import seal_slot
from sentinelops.ml.model_benchmark_holdout import (
    holdout_compose_project,
    verify_capture_group, verify_capture_inventory, verify_capture_source, verify_frozen_capture_sources,
)
from sentinelops.ml.model_benchmark_protocol import _bytes, publish_freeze
from sentinelops.telemetry.store import ArtifactStore


def verify_group(root, group_id, registration_sha256, *, project):
    """Replay the completed group and its source receipts without running workloads."""
    root, project = Path(root).resolve(), Path(project).resolve()
    registration, matrix, _ = load_capture_design(root, registration_sha256, project=project)
    groups = [item for item in matrix['groups'] if item['group_id'] == group_id]
    if len(groups) != 1:
        raise ValueError('group is not part of the registered holdout matrix')
    group = groups[0]
    directory = root / 'capture/groups' / group_id
    receipt = json.loads((directory / 'receipt.json').read_bytes())
    rows = verify_capture_group(group, receipt, matrix_sha256=registration['matrix_sha256'],
        candidates_sha256=registration['candidates_sha256'], source_profile=matrix['source_profile'])
    for slot, row in zip(group['slots'], rows):
        slot_dir = directory / 'slots' / slot['slot_id']
        if json.loads((slot_dir / 'receipt.json').read_bytes()) != row:
            raise ValueError('group receipt differs from its slot receipt')
        run_path = slot_dir / 'runs' / row['run_id'] / 'run.json'
        verify_slot_context(root, group_id, slot, row['run_id'], registration_sha256,
            capture_commit=registration['capture_commit'], run_path=run_path, source_profile=matrix['source_profile'],
            registered_at=registration['registered_at'])
        verify_capture_source(run_path, row, slot, group_id=group_id, source_profile=matrix['source_profile'],
            source_commit=registration['capture_commit'], certificate_store=ArtifactStore(root),
            registered_at=registration['registered_at'])
    return receipt


def close_group(root, group_id, registration_sha256, *, project):
    """Stop only verified group containers; preserve source files and volumes."""
    root, project = Path(root).resolve(), Path(project).resolve()
    receipt = verify_group(root, group_id, registration_sha256, project=project)
    name = holdout_compose_project(registration_sha256, group_id)
    if not docker('ps', '-q', '--filter', 'label=com.docker.compose.project=' + name).strip():
        return receipt
    group = json.loads((root / 'capture/groups' / group_id / 'registration.json').read_bytes())
    configure_compose(project, root / 'capture', group_id, name)
    inspect_group_continuity(group)
    docker('stop', *[item['container_id'] for item in group['context']['containers'].values()])
    if docker('ps', '-q', '--filter', 'label=com.docker.compose.project=' + name).strip():
        raise ValueError('holdout group still has running source containers')
    return receipt


def capture_group(root, group_id, registration_sha256, *, project):
    """Capture the registered slots in order, with no automatic workload retry."""
    root, project = Path(root).resolve(), Path(project).resolve()
    store = ArtifactStore(root)
    with campaign_lock(store):
        registration, matrix, _ = load_capture_design(root, registration_sha256, project=project)
        groups = [item for item in matrix['groups'] if item['group_id'] == group_id]
        if len(groups) != 1:
            raise ValueError('group is not part of the registered holdout matrix')
        group = groups[0]
        directory = root / 'capture/groups' / group_id
        if (directory / 'receipt.json').exists():
            return close_group(root, group_id, registration_sha256, project=project)
        prepared = prepare_group(root, group_id, registration_sha256, project=project)
        if prepared['status'] != 'ready':
            raise ValueError('holdout group is not ready for capture')
        rows = []
        for slot in group['slots']:
            slot_id = slot['slot_id']
            slot_dir = directory / 'slots' / slot_id
            runs = slot_dir / 'runs'
            if runs.exists():
                captures = list(runs.iterdir())
                if len(captures) != 1 or not (captures[0] / 'run.json').is_file():
                    raise ValueError('incomplete holdout slot requires investigation; no automatic retry')
                run_id = captures[0].name
            else:
                registered = register_slot(root, group_id, slot_id, registration_sha256, project=project)
                authorization = {'root': str(root), 'candidates_sha256': registration['candidates_sha256'],
                    'matrix_sha256': registration['matrix_sha256'], 'registration_sha256': registration_sha256,
                    'group_registration_sha256': registered['group_registration_sha256'],
                    'slot_registration_sha256': registered['slot_registration_sha256'], 'slot_id': slot_id}
                result = asyncio.run(execute(ExperimentConfig(**slot['recipe']), project=project, output=runs,
                    session_id=group_id, source_profile=matrix['source_profile'], holdout_authorization=authorization))
                run_id = result.name
            rows.append(seal_slot(root, group_id, slot_id, run_id, registration_sha256, project=project))
        receipt = {'schema_version': 'benchmark-holdout-group/v1', 'role': 'sealed-final', 'group_id': group_id,
            'matrix_sha256': registration['matrix_sha256'], 'candidates_sha256': registration['candidates_sha256'],
            'source_profile': matrix['source_profile'], 'clock': 'arrival-aware', 'slots': rows}
        verify_capture_group(group, receipt, matrix_sha256=registration['matrix_sha256'],
            candidates_sha256=registration['candidates_sha256'], source_profile=matrix['source_profile'])
        publish_freeze(directory / 'receipt.json', _bytes(receipt), conflict='different holdout group receipt exists')
        return close_group(root, group_id, registration_sha256, project=project)


def seal_capture(root, registration_sha256, *, project):
    """Verify the entire frozen holdout inventory before publishing its seal."""
    root, project = Path(root).resolve(), Path(project).resolve()
    store = ArtifactStore(root)
    with campaign_lock(store):
        registration, matrix, _ = load_capture_design(root, registration_sha256, project=project)
        receipts, pins = {}, []
        for group in matrix['groups']:
            group_id = group['group_id']
            path = root / 'capture/groups' / group_id / 'receipt.json'
            data = path.read_bytes()
            receipts[group_id] = json.loads(data)
            pins.append({'group_id': group_id, 'receipt_sha256': hashlib.sha256(data).hexdigest()})
        inventory = verify_capture_inventory(matrix, receipts, matrix_sha256=registration['matrix_sha256'],
                                             candidates_sha256=registration['candidates_sha256'])
        for group_id, rows in inventory.items():
            for row in rows:
                slot_path = root / 'capture/groups' / group_id / 'slots' / row['slot_id'] / 'receipt.json'
                if json.loads(slot_path.read_bytes()) != row:
                    raise ValueError('group receipt differs from its slot receipt')
        for group in matrix['groups']:
            name = holdout_compose_project(registration_sha256, group['group_id'])
            if docker('ps', '-q', '--filter', 'label=com.docker.compose.project=' + name).strip():
                raise ValueError('cannot seal while a holdout source context is running')
        run_paths = {row['slot_id']: root / 'capture/groups' / group_id / 'slots' / row['slot_id'] /
                     'runs' / row['run_id'] / 'run.json' for group_id, rows in inventory.items() for row in rows}
        verified = verify_frozen_capture_sources(root, matrix, receipts, run_paths,
            matrix_sha256=registration['matrix_sha256'], candidates_sha256=registration['candidates_sha256'],
            capture_commit=registration['capture_commit'], registration_sha256=registration_sha256,
            certificate_store=store)
        if len(verified) != matrix['slot_count']:
            raise ValueError('verified holdout run count differs from its registered matrix')
        receipt = {'schema_version': 'benchmark-holdout-seal/v1', 'registration_sha256': registration_sha256,
            'matrix_sha256': registration['matrix_sha256'], 'candidates_sha256': registration['candidates_sha256'],
            'capture_commit': registration['capture_commit'], 'groups': pins, 'slot_count': len(verified),
            'capture_sealed': True, 'activation_allowed': False}
        publish_freeze(root / 'capture/sealed.json', _bytes(receipt), conflict='different holdout seal already exists')
        return receipt
