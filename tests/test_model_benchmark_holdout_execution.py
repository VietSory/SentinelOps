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
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2


def test_holdout_execution_requires_bound_slot_and_live_context(tmp_path, monkeypatch):
    matrix = draft_matrix()['matrix']
    group, slot = matrix['groups'][0], matrix['groups'][0]['slots'][0]
    commit, capture_pin = 'a' * 40, 'b' * 64
    monkeypatch.setattr(local_corpus, 'clean_source', lambda project: commit)
    monkeypatch.setattr(local_corpus, 'context_snapshot', lambda project, name: {'context': 'intact'})
    monkeypatch.setattr(holdout, 'verify_candidate_freeze', lambda *args: {'protocol_sha256': 'c' * 64})
    monkeypatch.setattr(holdout, 'verify_capture_registration',
                        lambda *args, **kwargs: {'registered_at': '2026-01-01T00:00:00Z'})
    group_path = tmp_path / 'capture/groups' / group['group_id'] / 'registration.json'
    slot_path = group_path.parent / 'slots' / slot['slot_id'] / 'registration.json'
    slot_path.parent.mkdir(parents=True)
    (tmp_path / 'capture/matrix.json').write_text(json.dumps(matrix))
    group_doc = {'schema_version': 'benchmark-holdout-group-registration/v1', 'group_id': group['group_id'],
                 'capture_registration_sha256': capture_pin, 'capture_commit': commit,
                 'registered_at': '2026-01-01T00:01:00Z', 'compose_project': 'sentinelops-corpus-' + group['group_id'],
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
    result = asyncio.run(runner.execute(config, project=tmp_path, output=tmp_path, source_profile=SOURCE_PROFILE_V2,
                                        session_id='bench-final-01', holdout_authorization={}))
    assert result == tmp_path / 'result' and calls == ['verified', 'execute']
