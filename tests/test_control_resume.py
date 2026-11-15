"""Recovery guards: reject missing/empty/foreign storage before Compose can initialize it."""
from copy import deepcopy
import json
import subprocess

import pytest

from scripts import lane_b
from test_lane_b import rendered_fixture

IMAGE = 'sha256:' + '1' * 64


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_b, 'ROOT', tmp_path / 'runtime')
    lane_b.init_runtime()
    monkeypatch.setattr(lane_b, 'require_owned_containers', lambda: None)
    config = rendered_fixture()
    config['volumes'] = {'sentinelops_control': {'name': 'sentinelops-b_sentinelops_control'}}
    config['services']['control-postgres'].update(image='postgres:16-alpine', volumes=[{
        'type': 'volume', 'source': 'sentinelops_control', 'target': '/var/lib/postgresql/data'}])
    calls = []
    volume = {'Labels': {'com.docker.compose.project': 'sentinelops-b',
                         'com.docker.compose.volume': 'sentinelops_control'}}
    def read(command, **kwargs):
        calls.append(command)
        if command[:3] == ['docker', 'volume', 'inspect']:
            return json.dumps([volume])
        if command[:2] == ['docker', 'ps']:
            return ''
        if command[:3] == ['docker', 'image', 'inspect']:
            return IMAGE + '\n'
        pytest.fail('unexpected read')
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(lane_b.subprocess, 'check_output', read)
    monkeypatch.setattr(lane_b.subprocess, 'run', run)
    return config, calls, volume


def test_resume_preserves_tokens_and_uses_only_checked_volume_and_immutable_image(recovery):
    config, calls, _ = recovery
    before = {name: (lane_b.ROOT / name).read_bytes() for name in ('operator-token', 'api-token')}
    result = lane_b.resume_control(config)
    probe = next(c for c in calls if c[:2] == ['docker', 'run'])
    assert 'readonly,volume-nocopy' in probe[probe.index('--mount') + 1]
    assert probe[probe.index('--network') + 1] == 'none' and '--read-only' in probe
    assert probe[probe.index('--user') + 1] == 'postgres'
    assert probe[probe.index('--entrypoint') + 2] == IMAGE
    assert 'PG_VERSION' in probe[-1] and 'global/pg_control' in probe[-1]
    command = next(c for c in calls if c[:2] == ['docker', 'compose'])
    assert command[-1] == 'control-postgres' and '--no-deps' in command and '--no-build' in command
    assert command[command.index('--pull') + 1] == 'never'
    assert result['image_id'] == IMAGE and result['incident_readiness'] == 'NOT_OBSERVED'
    assert all((lane_b.ROOT / name).read_bytes() == value for name, value in before.items())


@pytest.mark.parametrize('change', ['unregistered', 'volume-name', 'bind-mount', 'missing-mount', 'foreign-owner'])
def test_invalid_recovery_never_starts_a_probe_or_database(recovery, change):
    config, calls, volume = recovery
    config = deepcopy(config)
    if change == 'unregistered': (lane_b.ROOT / 'runtime.json').unlink()
    if change == 'volume-name': config['volumes']['sentinelops_control']['name'] = 'sentinelops-a_control'
    if change == 'bind-mount': config['services']['control-postgres']['volumes'][0]['type'] = 'bind'
    if change == 'missing-mount': config['services']['control-postgres']['volumes'] = []
    if change == 'foreign-owner': volume['Labels']['com.docker.compose.project'] = 'sentinelops-corpus'
    with pytest.raises(ValueError): lane_b.resume_control(config)
    assert not any(c[:2] in (['docker', 'run'], ['docker', 'compose']) for c in calls)


@pytest.mark.parametrize('stage', ['volume', 'image', 'data-probe'])
def test_missing_storage_image_or_uninitialized_data_never_runs_compose(recovery, monkeypatch, stage):
    config, calls, _ = recovery
    original = lane_b.subprocess.check_output
    def read(command, **kwargs):
        if command[:3] == ['docker', stage, 'inspect']:
            raise subprocess.CalledProcessError(1, command)
        return original(command, **kwargs)
    def run(command, **kwargs):
        calls.append(command)
        if stage == 'data-probe': raise subprocess.CalledProcessError(1, command)
        pytest.fail('missing dependency reached mutation')
    monkeypatch.setattr(lane_b.subprocess, 'check_output', read)
    monkeypatch.setattr(lane_b.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError): lane_b.resume_control(config)
    assert not any(c[:2] == ['docker', 'compose'] for c in calls)
