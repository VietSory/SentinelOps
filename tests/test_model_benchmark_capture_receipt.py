from datetime import timedelta
import json

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.ml import model_benchmark_capture_receipt as capture
from sentinelops.ml import model_benchmark_holdout as holdout
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_local_labels import START, fixture_oracle


def test_slot_receipt_replays_source_and_preserves_run(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    group = matrix['groups'][0]
    slot = group['slots'][0]
    run_id = 'run-' + '1' * 32
    commit, pin = 'a' * 40, 'b' * 64
    registration = {'capture_commit': commit, 'matrix_sha256': draft['matrix_sha256'],
                    'candidates_sha256': pin, 'registered_at': timestamp(START - timedelta(seconds=1))}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {}))
    monkeypatch.setattr(capture, 'seal_slot_context', lambda *args, **kwargs: None)
    monkeypatch.setattr(capture, 'verify_slot_context', lambda *args, **kwargs: None)
    replay = []
    monkeypatch.setattr(capture, 'verify_source_replay', lambda *args: replay.append('seal'))
    monkeypatch.setattr(holdout, 'verify_source_replay', lambda *args: replay.append('verify'))
    directory = tmp_path / 'capture/groups' / group['group_id'] / 'slots' / slot['slot_id']
    run_dir = directory / 'runs' / run_id
    serving, truth = ArtifactStore(run_dir / 'serving'), ArtifactStore(run_dir / 'truth')
    opened, completed, outcomes = fixture_oracle()
    logs_ref = serving.write('raw', {'kind': 'serving-logs', 'records': completed})
    full_ref = truth.write('raw', {'kind': 'full-experiment-logs', 'records': opened})
    outcomes_ref = truth.write('raw', {'outcomes': outcomes})
    raw_ref = serving.write('raw', {'source_profile': matrix['source_profile']})
    manifest_ref = serving.write('manifests', {'source_profile': matrix['source_profile'], 'raw_refs': [raw_ref]})
    run = {'run_id': run_id, 'session_id': group['group_id'], 'status': 'completed', 'declared_split': 'test',
           'dirty_worktree': False, 'environment': 'local', 'source_profile': matrix['source_profile'],
           'source_commit': commit, 'scenario_id': slot['recipe']['scenario'],
           'parameters': {**slot['recipe'], 'full_logs_ref': full_ref}, 'source_manifest_ref': manifest_ref,
           'logs_ref': logs_ref, 'request_outcomes_ref': outcomes_ref, 'started_at': timestamp(START),
           'ended_at': timestamp(START + timedelta(minutes=14))}
    run_path = run_dir / 'run.json'
    run_path.write_text(json.dumps(run))
    result = capture.seal_slot(tmp_path, group['group_id'], slot['slot_id'], run_id, 'c' * 64, project=tmp_path)
    assert result['raw_refs'] == [raw_ref]
    assert capture.seal_slot(tmp_path, group['group_id'], slot['slot_id'], run_id, 'c' * 64, project=tmp_path) == result
    assert replay == ['seal', 'verify', 'verify']
    run['source_commit'] = 'd' * 40
    run_path.write_text(json.dumps(run))
    with pytest.raises(ValueError, match='registered slot'):
        capture.seal_slot(tmp_path, group['group_id'], slot['slot_id'], run_id, 'c' * 64, project=tmp_path)
    assert json.loads((directory / 'receipt.json').read_bytes()) == result


def test_slot_receipt_refuses_more_than_one_run(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    group = matrix['groups'][0]
    slot = group['slots'][0]
    registration = {'capture_commit': 'a' * 40, 'matrix_sha256': draft['matrix_sha256'],
                    'candidates_sha256': 'b' * 64, 'registered_at': timestamp(START)}
    monkeypatch.setattr(capture, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {}))
    monkeypatch.setattr(capture, 'seal_slot_context', lambda *args, **kwargs: None)
    runs = tmp_path / 'capture/groups' / group['group_id'] / 'slots' / slot['slot_id'] / 'runs'
    (runs / ('run-' + '1' * 32)).mkdir(parents=True)
    (runs / ('run-' + '2' * 32)).mkdir()
    with pytest.raises(ValueError, match='exactly one run'):
        capture.seal_slot(tmp_path, group['group_id'], slot['slot_id'], 'run-' + '1' * 32, 'c' * 64, project=tmp_path)
