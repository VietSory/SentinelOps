from dataclasses import replace
import asyncio
import hashlib
import json

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.experiments import local_corpus
from sentinelops.experiments.runner import ExperimentConfig
from sentinelops.experiments import runner
from sentinelops.ml import model_benchmark_holdout as holdout
from sentinelops.ml import model_benchmark_capture_context as capture_context
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.prometheus import SOURCE_PROFILE


def test_holdout_execution_requires_bound_slot_and_live_context(tmp_path, monkeypatch):
    matrix = draft_matrix()['matrix']
    group, slot = matrix['groups'][0], matrix['groups'][0]['slots'][0]
    commit, capture_pin = 'a' * 40, 'b' * 64
    monkeypatch.setattr(local_corpus, 'clean_source', lambda project: commit)
    monkeypatch.setattr(local_corpus, 'context_snapshot', lambda project, name: {'context': 'intact'})
    monkeypatch.setattr(capture_context, 'inspect_group_continuity', lambda group: None)
    monkeypatch.setattr(capture_context, 'ensure_previous_contexts_stopped', lambda *args: None)
    monkeypatch.setattr(holdout, 'verify_candidate_freeze', lambda *args: {'protocol_sha256': 'c' * 64})
    pilot_path = tmp_path / 'pilot.json'
    pilot_path.write_text(json.dumps({'group_id': 'ale-02'}))
    pilot_ref = {'path': 'pilot.json', 'sha256': hashlib.sha256(pilot_path.read_bytes()).hexdigest()}
    monkeypatch.setattr(holdout, 'verify_capture_registration',
                        lambda *args, **kwargs: {'registered_at': '2026-01-01T00:00:00Z', 'pilot_receipt': pilot_ref})
    group_path = tmp_path / 'capture/groups' / group['group_id'] / 'registration.json'
    slot_path = group_path.parent / 'slots' / slot['slot_id'] / 'registration.json'
    slot_path.parent.mkdir(parents=True)
    (tmp_path / 'capture/matrix.json').write_text(json.dumps(matrix))
    group_doc = {'schema_version': 'benchmark-holdout-group-registration/v1', 'group_id': group['group_id'],
                 'capture_registration_sha256': capture_pin, 'capture_commit': commit,
                 'registered_at': '2026-01-01T00:01:00Z', 'compose_project': holdout.holdout_compose_project(capture_pin, group['group_id']),
                 'context': {'context': 'intact'}, 'initial_payment_rows': 0}
    group_path.write_text(json.dumps(group_doc))
    group_sha = hashlib.sha256(group_path.read_bytes()).hexdigest()
    slot_doc = {'schema_version': 'benchmark-holdout-slot-registration/v1', 'group_id': group['group_id'],
                'slot_id': slot['slot_id'], 'group_registration_sha256': group_sha, 'capture_commit': commit,
                'registered_at': '2026-01-01T00:02:00Z', 'recipe': slot['recipe']}
    slot_path.write_text(json.dumps(slot_doc))
    auth = {'root': str(tmp_path), 'candidates_sha256': 'd' * 64, 'matrix_sha256': draft_matrix()['matrix_sha256'],
            'registration_sha256': capture_pin, 'group_registration_sha256': group_sha,
            'slot_registration_sha256': hashlib.sha256(slot_path.read_bytes()).hexdigest(), 'slot_id': slot['slot_id']}
    config = ExperimentConfig(**slot['recipe'])
    target = slot_path.parent / 'runs'
    assert holdout.verify_holdout_execution(tmp_path, config, group['group_id'], target, auth)['slot_id'] == slot['slot_id']
    with pytest.raises(ValueError, match='recipe differs'):
        holdout.verify_holdout_execution(tmp_path, replace(config, seed=config.seed + 1), group['group_id'], target, auth)
    with pytest.raises(ValueError, match='output escapes'):
        holdout.verify_holdout_execution(tmp_path, config, group['group_id'], tmp_path / 'other', auth)
    target.mkdir()
    with pytest.raises(ValueError, match='no automatic restart'):
        holdout.verify_holdout_execution(tmp_path, config, group['group_id'], target, auth)
    target.rmdir()
    monkeypatch.setattr(local_corpus, 'context_snapshot', lambda project, name: {'context': 'changed'})
    with pytest.raises(ValueError, match='context changed'):
        holdout.verify_holdout_execution(tmp_path, config, group['group_id'], target, auth)


def test_v2_test_runner_refuses_unregistered_workloads(tmp_path, monkeypatch):
    slot = draft_matrix()['matrix']['groups'][0]['slots'][0]
    config = ExperimentConfig(**slot['recipe'])
    calls = []
    async def execute_locked(*args, **kwargs):
        calls.append('execute')
        return tmp_path / 'result'
    monkeypatch.setattr(runner, '_execute_locked', execute_locked)
    with pytest.raises(ValueError, match='ADR-016'):
        asyncio.run(runner.execute(config, project=tmp_path, output=tmp_path, source_profile=SOURCE_PROFILE_V2))
    assert calls == []
    def reject(*args):
        raise ValueError('unregistered slot')
    monkeypatch.setattr(holdout, 'verify_holdout_execution', reject)
    with pytest.raises(ValueError, match='unregistered slot'):
        asyncio.run(runner.execute(config, project=tmp_path, output=tmp_path, source_profile=SOURCE_PROFILE_V2,
                                   session_id='bench-final-01', holdout_authorization={}))
    assert calls == []
    monkeypatch.setattr(holdout, 'verify_holdout_execution', lambda *args: calls.append('verified'))
    target = tmp_path / 'runs'
    result = asyncio.run(runner.execute(config, project=tmp_path, output=target, source_profile=SOURCE_PROFILE_V2,
                                        session_id='bench-final-01', holdout_authorization={}))
    assert result == tmp_path / 'result' and calls == ['verified', 'execute']
    with pytest.raises(FileExistsError):
        asyncio.run(runner.execute(config, project=tmp_path, output=target, source_profile=SOURCE_PROFILE_V2,
                                   session_id='bench-final-01', holdout_authorization={}))
    assert calls == ['verified', 'execute', 'verified']


def test_execution_refuses_out_of_order_even_with_a_slot_authorization(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    (tmp_path / 'capture').mkdir()
    (tmp_path / 'capture/matrix.json').write_text(json.dumps(matrix))
    monkeypatch.setattr(local_corpus, 'clean_source', lambda project: 'a' * 40)
    monkeypatch.setattr(holdout, 'verify_candidate_freeze', lambda *args: {})
    monkeypatch.setattr(holdout, 'verify_capture_registration', lambda *args, **kwargs: {})
    auth = {'root': str(tmp_path), 'candidates_sha256': 'b' * 64, 'matrix_sha256': draft['matrix_sha256'],
            'registration_sha256': 'c' * 64, 'group_registration_sha256': 'd' * 64,
            'slot_registration_sha256': 'e' * 64, 'slot_id': ''}
    group = matrix['groups'][0]
    slot = group['slots'][1]
    auth['slot_id'] = slot['slot_id']
    with pytest.raises(ValueError, match='earlier holdout slot is not complete'):
        holdout.verify_holdout_execution(tmp_path, ExperimentConfig(**slot['recipe']), group['group_id'], tmp_path, auth)
    group = matrix['groups'][1]
    slot = group['slots'][0]
    auth['slot_id'] = slot['slot_id']
    with pytest.raises(ValueError, match='earlier holdout group is not complete'):
        holdout.verify_holdout_execution(tmp_path, ExperimentConfig(**slot['recipe']), group['group_id'], tmp_path, auth)


def test_unexpected_healthy_breach_is_retained_only_for_v2_holdout():
    breach = {'payment-service': {'first_violation_at': '2026-01-01T00:00:00Z'}}
    holdout_config = ExperimentConfig('S0', 'test')
    development_config = ExperimentConfig('S0', 'validation')
    assert not runner._reject_healthy_breach(holdout_config, SOURCE_PROFILE_V2, breach)
    assert runner._reject_healthy_breach(development_config, SOURCE_PROFILE_V2, breach)
    assert runner._reject_healthy_breach(holdout_config, SOURCE_PROFILE, breach)
    assert not runner._unexpected_workload(holdout_config, SOURCE_PROFILE_V2, {'200': 1, '503': 1, '500': 1}, [])
    assert runner._unexpected_workload(holdout_config, SOURCE_PROFILE_V2, {'transport_error': 1}, [])
    assert runner._unexpected_workload(development_config, SOURCE_PROFILE_V2, {'500': 1}, [])
