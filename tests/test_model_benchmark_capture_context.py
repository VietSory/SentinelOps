from datetime import datetime, timezone
import json

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.ml import model_benchmark_capture_context as capture
from sentinelops.telemetry.core import timestamp


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
    first = capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path)
    assert first['status'] == 'ready' and len(calls) == 1
    assert capture.prepare_group(tmp_path, group, 'c' * 64, project=tmp_path) == first
    assert len(calls) == 1
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
