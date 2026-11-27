from datetime import datetime, timezone
import hashlib
import json

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.ml import model_benchmark_capture_context as capture
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore


def test_group_provisioning_records_intent_before_docker_and_reuses_context(tmp_path, monkeypatch):
    matrix = draft_matrix()['matrix']
    group = matrix['groups'][0]['group_id']
    registration = {'capture_commit': 'a' * 40, 'matrix_sha256': draft_matrix()['matrix_sha256'],
                    'candidates_sha256': 'b' * 64}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {'group_id': 'ale-02'}))
    monkeypatch.setattr(capture, 'clean_source', lambda project: 'a' * 40)
    monkeypatch.setattr(capture, 'configure_compose', lambda *args: None)
    monkeypatch.setattr(capture, 'volume_created_within', lambda *args: True)
    calls, context = [], {}
    def docker(*args):
        if args[:2] == ('compose', 'exec'):
            return '0\n'
        return ''
    def run(command, **kwargs):
        assert (tmp_path / 'capture/groups' / group / 'provisioning.json').is_file()
        calls.append(command)
        created = timestamp(datetime.now(timezone.utc))
        context.update(containers={'payment-service': {'created_at': created}},
                       database_volume={'name': 'owned-volume', 'created_at': created})
    monkeypatch.setattr(capture, 'docker', docker)
    monkeypatch.setattr(capture.subprocess, 'run', run)
    monkeypatch.setattr(capture, 'context_snapshot', lambda *args: context)
    monkeypatch.setattr(capture, 'inspect_group_continuity', lambda *args: None)
    first = capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path)
    assert first['status'] == 'ready' and len(calls) == 1
    assert capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path) == first
    assert len(calls) == 1
    monkeypatch.setattr(capture, 'docker', lambda *args: 'running\n' if args[:2] == ('ps', '-q') else '')
    with pytest.raises(ValueError, match='pilot source context is still running'):
        capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path)
    monkeypatch.setattr(capture, 'docker', docker)
    context['database_volume']['name'] = 'changed-volume'
    with pytest.raises(ValueError, match='context changed'):
        capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path)


def test_group_refuses_running_pilot_and_unregistered_containers(tmp_path, monkeypatch):
    matrix = draft_matrix()['matrix']
    group = matrix['groups'][0]['group_id']
    registration = {'capture_commit': 'a' * 40, 'matrix_sha256': draft_matrix()['matrix_sha256'],
                    'candidates_sha256': 'b' * 64}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {'group_id': 'ale-02'}))
    monkeypatch.setattr(capture, 'docker', lambda *args: 'running\n' if args[:2] == ('ps', '-q') else '')
    with pytest.raises(ValueError, match='pilot source context is still running'):
        capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path)
    monkeypatch.setattr(capture, 'docker', lambda *args: 'foreign\n' if args[:2] == ('ps', '-aq') else '')
    with pytest.raises(ValueError, match='unregistered holdout containers'):
        capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path)
    assert not (tmp_path / 'capture/groups' / group / 'provisioning.json').exists()


def test_group_refuses_to_replace_a_running_previous_context(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    first, second = matrix['groups'][:2]
    registration = {'capture_commit': 'a' * 40, 'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': 'b' * 64}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {'group_id': 'ale-02'}))
    receipt = {'schema_version': 'benchmark-holdout-group/v1', 'role': 'sealed-final', 'group_id': first['group_id'],
               'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': 'b' * 64,
               'source_profile': matrix['source_profile'], 'clock': 'arrival-aware', 'slots': [
                   {'slot_id': slot['slot_id'], 'run_id': f'run-{index:032x}', 'status': 'completed',
                    'source_manifest_sha256': 'c' * 64, 'oracle_certificate_sha256': 'd' * 64,
                    'raw_refs': [f'artifact://raw/{index:064x}.json']}
                   for index, slot in enumerate(first['slots'], 1)]}
    path = tmp_path / 'capture/groups' / first['group_id'] / 'receipt.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(capture, 'docker', lambda *args: 'running\n' if first['group_id'] in args[-1] else '')
    with pytest.raises(ValueError, match='previous holdout source context is still running'):
        capture.prepare_group(tmp_path, second['group_id'], 'c' * 64, project=tmp_path)
    assert not (tmp_path / 'capture/groups' / second['group_id'] / 'provisioning.json').exists()


def test_slot_registration_is_ordered_and_never_restarts_capture(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    group = matrix['groups'][0]
    first, second = group['slots'][:2]
    registration = {'capture_commit': 'a' * 40, 'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': 'b' * 64}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {}))
    monkeypatch.setattr(capture, '_prepare_group', lambda *args, **kwargs: {
        'status': 'ready', 'group_registration_sha256': 'c' * 64})
    group_dir = tmp_path / 'capture/groups' / group['group_id']
    group_dir.mkdir(parents=True)
    (group_dir / 'registration.json').write_text('{}')
    with pytest.raises(ValueError, match='earlier holdout slot is not complete'):
        capture.register_slot(tmp_path, group['group_id'], second['slot_id'], 'd' * 64, project=tmp_path)
    result = capture.register_slot(tmp_path, group['group_id'], first['slot_id'], 'd' * 64, project=tmp_path)
    assert capture.register_slot(tmp_path, group['group_id'], first['slot_id'], 'd' * 64, project=tmp_path) == result
    first_dir = group_dir / 'slots' / first['slot_id']
    (first_dir / 'runs').mkdir()
    with pytest.raises(ValueError, match='no automatic restart'):
        capture.register_slot(tmp_path, group['group_id'], first['slot_id'], 'd' * 64, project=tmp_path)
    receipt = {'slot_id': first['slot_id'], 'run_id': 'run-' + '1' * 32, 'status': 'completed',
               'source_manifest_sha256': 'e' * 64, 'oracle_certificate_sha256': 'f' * 64,
               'raw_refs': ['artifact://raw/' + '2' * 64 + '.json']}
    (first_dir / 'receipt.json').write_text(json.dumps(receipt))
    assert capture.register_slot(tmp_path, group['group_id'], second['slot_id'], 'd' * 64,
                                 project=tmp_path)['status'] == 'ready'


def test_slot_registration_rejects_invalid_prior_receipt_and_foreign_artifacts(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    group = matrix['groups'][0]
    first, second = group['slots'][:2]
    registration = {'capture_commit': 'a' * 40, 'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': 'b' * 64}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {}))
    monkeypatch.setattr(capture, '_prepare_group', lambda *args, **kwargs: {
        'status': 'ready', 'group_registration_sha256': 'c' * 64})
    group_dir = tmp_path / 'capture/groups' / group['group_id']
    first_dir = group_dir / 'slots' / first['slot_id']
    first_dir.mkdir(parents=True)
    (group_dir / 'registration.json').write_text('{}')
    (first_dir / 'receipt.json').write_text(json.dumps({'slot_id': first['slot_id']}))
    with pytest.raises(ValueError):
        capture.register_slot(tmp_path, group['group_id'], second['slot_id'], 'd' * 64, project=tmp_path)
    (first_dir / 'receipt.json').unlink()
    (first_dir / 'unexpected').write_text('preserve')
    with pytest.raises(ValueError, match='unregistered holdout slot artifacts'):
        capture.register_slot(tmp_path, group['group_id'], first['slot_id'], 'd' * 64, project=tmp_path)


def test_group_continuity_rejects_restarted_container(monkeypatch):
    name = 'sentinelops-corpus-bench-final-01'
    old = '2026-01-01T00:00:00Z'
    group = {'compose_project': name, 'registered_at': '2026-01-01T00:01:00Z',
             'context': {'containers': {'payment-service': {
                 'container_id': 'container', 'image': 'image', 'created_at': old}}}}
    item = {'Id': 'container', 'Image': 'image', 'Created': old, 'RestartCount': 0,
            'Config': {'Labels': {'com.docker.compose.project': name,
                                  'com.docker.compose.service': 'payment-service'}},
            'State': {'Running': True, 'StartedAt': old, 'FinishedAt': '0001-01-01T00:00:00Z'}}
    monkeypatch.setattr(capture, 'docker', lambda *args: json.dumps([item]))
    assert capture.inspect_group_continuity(group)['containers']['payment-service']['running']
    item['State']['StartedAt'] = '2026-01-01T00:02:00Z'
    item['RestartCount'] = 1
    with pytest.raises(ValueError, match='restarted after prospective registration'):
        capture.inspect_group_continuity(group)


def test_slot_context_seal_binds_run_and_cannot_be_replaced(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    selected = matrix['groups'][0]
    spec = selected['slots'][0]
    group_id, slot_id = selected['group_id'], spec['slot_id']
    name, old = 'sentinelops-corpus-' + group_id, '2026-01-01T00:00:00Z'
    commit = 'a' * 40
    group = {'schema_version': 'benchmark-holdout-group-registration/v1', 'group_id': group_id,
             'capture_registration_sha256': 'd' * 64, 'capture_commit': commit, 'initial_payment_rows': 0,
             'compose_project': name, 'registered_at': '2026-01-01T00:01:00Z',
             'context': {'images': {'payment-service': 'image'}, 'containers': {'payment-service': {
                 'container_id': 'container', 'image': 'image', 'created_at': old}}}}
    registration = {'capture_commit': commit, 'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': 'b' * 64}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {}))
    monkeypatch.setattr(capture, '_prepare_group', lambda *args, **kwargs: {
        'status': 'ready', 'group_registration_sha256': group_pin})
    group_dir = tmp_path / 'capture/groups' / group_id
    slot_dir = group_dir / 'slots' / slot_id
    run_id = 'run-' + '1' * 32
    run_path = slot_dir / 'runs' / run_id / 'run.json'
    run_path.parent.mkdir(parents=True)
    (group_dir / 'registration.json').write_text(json.dumps(group))
    group_pin = hashlib.sha256((group_dir / 'registration.json').read_bytes()).hexdigest()
    (slot_dir / 'registration.json').write_text(json.dumps({
        'schema_version': 'benchmark-holdout-slot-registration/v1', 'group_id': group_id, 'slot_id': slot_id,
        'group_registration_sha256': group_pin, 'capture_commit': commit,
        'registered_at': '2026-01-01T00:02:00Z', 'recipe': spec['recipe']}))
    run = {'run_id': run_id, 'session_id': group_id, 'status': 'completed', 'source_commit': commit,
           'source_profile': matrix['source_profile'], 'image_digests': group['context']['images'],
           'started_at': '2026-01-01T00:03:00Z', 'ended_at': '2026-01-01T00:04:00Z'}
    run_path.write_text(json.dumps(run))
    item = {'container_id': 'container', 'image': 'image', 'created_at': old, 'created_at_source': old,
            'started_at': old, 'started_at_source': old, 'finished_at': old, 'finished_at_source': old,
            'running': True, 'restart_count': 0}
    observation = {'kind': 'local-corpus-context-inspection/v1', 'compose_project': name,
                   'containers': {'payment-service': item}, 'collected_at': '2026-01-01T00:05:00Z'}
    calls = []
    def inspect(*args, **kwargs):
        calls.append(1)
        return observation
    monkeypatch.setattr(capture, 'inspect_group_continuity', inspect)
    result = capture.seal_slot_context(tmp_path, group_id, slot_id, run_id, 'd' * 64, project=tmp_path)
    assert capture.seal_slot_context(tmp_path, group_id, slot_id, run_id, 'd' * 64, project=tmp_path) == result
    assert capture.verify_slot_context(tmp_path, group_id, spec, run_id, 'd' * 64,
        capture_commit=commit, run_path=run_path, source_profile=matrix['source_profile'], registered_at=old)['run_id'] == run_id
    assert calls == [1]
    receipt_path = slot_dir / 'end-context.json'
    original_receipt = receipt_path.read_bytes()
    forged = json.loads(original_receipt)
    item['started_at'] = item['started_at_source'] = '2026-01-01T00:03:00Z'
    forged['inspection_ref'] = ArtifactStore(tmp_path).write('raw', observation)
    receipt_path.write_text(json.dumps(forged))
    with pytest.raises(ValueError, match='restarted after prospective registration'):
        capture.verify_slot_context(tmp_path, group_id, spec, run_id, 'd' * 64,
            capture_commit=commit, run_path=run_path, source_profile=matrix['source_profile'], registered_at=old)
    receipt_path.write_bytes(original_receipt)
    run['ended_at'] = '2026-01-01T00:04:01Z'
    run_path.write_text(json.dumps(run))
    with pytest.raises(ValueError, match='another run or registration'):
        capture.seal_slot_context(tmp_path, group_id, slot_id, run_id, 'd' * 64, project=tmp_path)
    with pytest.raises(ValueError, match='identity or timing differs'):
        capture.verify_slot_context(tmp_path, group_id, spec, run_id, 'd' * 64,
            capture_commit=commit, run_path=run_path, source_profile=matrix['source_profile'], registered_at=old)
