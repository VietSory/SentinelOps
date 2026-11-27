"""Subprocess checks for honest command availability; no data/model execution."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROJECT = Path(__file__).resolve().parents[1]


def run(tmp_path, *command):
    env = {**os.environ, 'PYTHONPATH': str(PROJECT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.run([sys.executable, '-m', 'scripts.model_benchmark', '--root',
                           str(tmp_path / 'absent-root'), '--protocol', str(tmp_path / 'absent.json'),
                           *command], cwd=PROJECT, env=env, capture_output=True, text=True)


@pytest.mark.parametrize('command', [
    ('fit', '--lane', 'a'), ('fit', '--lane', 'b'), ('calibrate', '--lane', 'a'),
    ('calibrate', '--lane', 'b'), ('freeze-candidates',), ('capture-preflight',),
    ('capture', '--group', '01'), ('verify-group', '--group', '01'), ('seal-capture',),
    ('score', '--lane', 'a'), ('evaluate', '--lane', 'b'), ('report',), ('verify-report',), ('package',)])
def test_unavailable_commands_block_without_reads_or_writes(tmp_path, command):
    result = run(tmp_path, *command)
    assert result.returncode == 2, result.stderr
    output = json.loads(result.stdout)
    assert output['status'] == 'blocked' and output['reasons']
    assert output['stage'] == command[0]
    assert list(tmp_path.iterdir()) == []


def test_help_and_parser_do_not_claim_success(tmp_path):
    result = run(tmp_path, '--help')
    assert result.returncode == 0
    assert 'blocked' in result.stdout and 'freeze-protocol' in result.stdout
    assert run(tmp_path, 'invented').returncode == 2
    assert run(tmp_path, 'audit', '--lane', 'c').returncode == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('options', [
    ('--lane', 'b', '--lane-a-evidence', 'missing.json'),
    ('--lane', 'a', '--lane-a-evidence', 'missing.json', '--lane-b-input', 'missing.json'),
])
def test_lane_a_evidence_cannot_be_used_for_lane_b_or_mixed_input(tmp_path, options):
    result = run(tmp_path, 'fit', *options)
    assert result.returncode == 1
    assert json.loads(result.stdout)['reasons'] == [
        '--lane-a-evidence requires --lane a without --lane-b-input']
    assert list(tmp_path.iterdir()) == []


def test_lane_a_development_evidence_rejects_holdout(tmp_path):
    from scripts.verify_ltfp_development_stage import verify_stage

    evidence = tmp_path / 'evidence.json'
    evidence.write_text(json.dumps({'scope': 'alibaba-proxy-development',
                                    'activation_allowed': False, 'holdout_opened': True}))
    with pytest.raises(ValueError, match='development-only'):
        verify_stage('score', tmp_path, {}, evidence)


def test_holdout_matrix_is_deterministic_and_cannot_start_capture(tmp_path):
    command = ('capture-preflight', '--draft-matrix')
    response = run(tmp_path, *command)
    assert response.returncode == 2, response.stderr
    result = json.loads(response.stdout)
    assert result['capture_allowed'] is False
    assert run(tmp_path, *command).stdout == response.stdout
    matrix = result['matrix']
    assert matrix['slot_count'] == 35 and matrix['budget_seconds'] == 122700
    assert matrix['role'] == 'sealed-final'
    assert [group['ordinal'] for group in matrix['groups']] == [1, 2, 3, 4, 5]
    slots = [slot for group in matrix['groups'] for slot in group['slots']]
    assert len({slot['slot_id'] for slot in slots}) == len({slot['recipe']['seed'] for slot in slots}) == 35
    for group in matrix['groups']:
        assert group['group_id'].startswith('bench-final-')
        assert group['budget_seconds'] == 24540
        for slot in group['slots']:
            recipe = slot['recipe']
            assert recipe['split'] == 'test'
            if slot['kind'].endswith('-low'):
                assert recipe['scenario'] == 'S3' and recipe['maximum_delay_ms'] == 400
            elif slot['kind'].endswith('-abrupt'):
                assert recipe['fault_mode'] == ('latency' if group['ordinal'] % 2 else 'error')
    assert list(tmp_path.iterdir()) == []


def test_capture_commands_require_registration_and_dispatch_group(tmp_path, monkeypatch, capsys):
    from scripts import model_benchmark
    from sentinelops.ml import model_benchmark_holdout_capture as capture

    calls = []
    def action(root, group, pin, *, project):
        calls.append((root, group, pin, project))
        return {'slots': [{}] * 7}
    monkeypatch.setattr(capture, 'capture_group', action)
    monkeypatch.setattr(capture, 'verify_group', action)
    common = ['--root', str(tmp_path), '--protocol', str(tmp_path / 'unused.json')]
    for command in ('capture', 'verify-group'):
        assert model_benchmark.main(common + [command, '--group', '01', '--registration-sha256', 'a' * 64]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['status'] == 'ready' and result['verified_slots'] == 7
        assert result['group'] == 'bench-final-01'
    assert len(calls) == 2 and all(call[2] == 'a' * 64 for call in calls)
    def seal(*args, **kwargs):
        path = tmp_path / 'capture/sealed.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        return {'slot_count': 35}
    monkeypatch.setattr(capture, 'seal_capture', seal)
    assert model_benchmark.main(common + ['seal-capture', '--registration-sha256', 'a' * 64]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['capture_sealed'] is True and result['verified_slots'] == 35


def test_sealed_score_requires_lane_a_and_all_pins(tmp_path, monkeypatch, capsys):
    from scripts import model_benchmark
    from sentinelops.ml import model_benchmark_forecast_export, model_benchmark_forecast_replay

    calls = []
    def score(root, **kwargs):
        calls.append(kwargs)
        return {'scored': True}
    monkeypatch.setattr(model_benchmark_forecast_replay, 'score_sealed', score)
    verify_modes = []
    def write(root, data, *, verify_only=False):
        verify_modes.append(verify_only)
        return {'dataset': 'local-latency-holdout', 'runs': 35, 'row_counts': {'predictions': 10},
                'q07_status': 'INCONCLUSIVE'}
    monkeypatch.setattr(model_benchmark_forecast_export, 'write_forecast_artifacts', write)
    common = ['--root', str(tmp_path), '--protocol', str(tmp_path / 'unused.json')]
    pins = ['--candidates-sha256', 'a' * 64, '--registration-sha256', 'b' * 64, '--seal-sha256', 'c' * 64]
    assert model_benchmark.main(common + ['score', '--lane', 'a', *pins]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'ready' and result['runs'] == 35 and result['activation_allowed'] is False
    assert len(calls) == 1 and calls[0]['seal_sha256'] == 'c' * 64
    assert model_benchmark.main(common + ['score', '--lane', 'b', *pins]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'invalid'
    assert model_benchmark.main(common + ['score', '--lane', 'a', '--seal-sha256', 'c' * 64]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'invalid' and len(calls) == 1
    assert model_benchmark.main(common + ['verify-report', '--lane', 'a', *pins]) == 0
    assert json.loads(capsys.readouterr().out)['verified'] is True
    assert verify_modes == [False, True] and len(calls) == 2


def test_actual_protocol_api_invalid_and_verify_missing(tmp_path):
    # Until integration, resolve the real W2 module without copying its source.
    protocol_module = PROJECT / 'src/sentinelops/ml/model_benchmark_protocol.py'
    if not protocol_module.exists():
        protocol_module = PROJECT.parent / 'worker-2/src/sentinelops/ml/model_benchmark_protocol.py'
    if not protocol_module.exists():
        pytest.fail('W2 protocol API required for launcher handoff verification')
    protocol = tmp_path / 'invalid.json'
    protocol.write_text('{}')
    env = {**os.environ, 'PYTHONPATH': str(PROJECT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    bootstrap = ("import sentinelops.ml,runpy,sys; "
                 "sentinelops.ml.__path__.append(sys.argv.pop(1)); "
                 "runpy.run_module('scripts.model_benchmark',run_name='__main__')")
    for command in (['audit', '--lane', 'a'], ['freeze-protocol'], ['verify']):
        result = subprocess.run([sys.executable, '-c', bootstrap, str(protocol_module.parent),
            '--root', str(tmp_path / 'root'), '--protocol', str(protocol), *command],
            cwd=PROJECT, env=env, capture_output=True, text=True)
        assert result.returncode == 1, result.stderr
        assert json.loads(result.stdout)['status'] == 'invalid'
        assert not (tmp_path / 'root').exists()


def test_actual_protocol_fixture_audit_freeze_verify_and_lane_block(tmp_path):
    import importlib.util
    import sentinelops.ml
    local = PROJECT / 'src/sentinelops/ml/model_benchmark_protocol.py'
    module = local if local.exists() else PROJECT.parent / 'worker-2/src/sentinelops/ml/model_benchmark_protocol.py'
    test_file = module.parents[3] / 'tests/test_model_benchmark_protocol.py'
    original = list(sentinelops.ml.__path__)
    sentinelops.ml.__path__.append(str(module.parent))
    try:
        spec = importlib.util.spec_from_file_location('protocol_fixture', test_file)
        fixture_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture_module)
        protocol, ref = fixture_module.fixture(tmp_path)
    finally:
        sentinelops.ml.__path__[:] = original
    path = tmp_path / 'protocol.json'
    path.write_text(json.dumps(protocol))
    env = {**os.environ, 'PYTHONPATH': str(PROJECT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    bootstrap = ("import sentinelops.ml,runpy,sys; sentinelops.ml.__path__.append(sys.argv.pop(1)); "
                 "runpy.run_module('scripts.model_benchmark',run_name='__main__')")
    def call(*args):
        return subprocess.run([sys.executable, '-c', bootstrap, str(module.parent), '--root', str(tmp_path),
            '--protocol', str(path), *args], cwd=PROJECT, env=env, capture_output=True, text=True)
    for args in (('audit', '--lane', 'a'), ('freeze-protocol',), ('verify',)):
        result = call(*args)
        assert result.returncode == 0, result.stderr + result.stdout
        assert json.loads(result.stdout)['status'] == 'ready'
    before = (tmp_path / 'freeze/protocol.json').read_bytes()
    assert call('freeze-protocol').returncode == 0
    assert (tmp_path / 'freeze/protocol.json').read_bytes() == before
    protocol['checks'][6]['scope'] = 'fixture'
    evidence = dict(test_id='A07',status='passed',scope='fixture',
                    source_manifest_sha256=protocol['code']['source_manifest']['sha256'])
    protocol['checks'][6]['evidence'] = ref('A07.json', evidence)
    path.write_text(json.dumps(protocol))
    result = call('audit', '--lane', 'a')
    assert result.returncode == 2 and json.loads(result.stdout)['lane'] == 'a'
    assert call('audit', '--lane', 'b').returncode == 0
    assert call('freeze-protocol').returncode == 2
    assert (tmp_path / 'freeze/protocol.json').read_bytes() == before
