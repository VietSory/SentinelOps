"""Host-release integrity fixtures, never model/source/action approval."""
import json
import os
from pathlib import Path
import sys

import pytest

from scripts import host_runtime, lane_b_host as host, lane_b


def fixture(tmp_path):
    root = tmp_path / 'release';root.mkdir()
    site = root / 'dependencies';site.mkdir();(site / 'dependency.py').write_text('version=1\n')
    (root / 'app.py').write_text('VERSION=1\n')
    record = {'release_version': 'lane-b-host/v2', 'release_id': 'a' * 32, 'root': str(root.resolve()),
        'interpreter': str(Path(sys.executable).resolve()), 'interpreter_sha256': host_runtime.digest(Path(sys.executable).resolve()),
        'python_version': sys.version, 'site_packages': str(site), 'runtime_source': str(tmp_path / 'mutable-dev'),
        'files': host_runtime.inventory(root), 'runtime_environment': {'PATH': '/usr/bin'},
        'model_activation_allowed': False, 'remediation_approval': False, 'portable': False}
    (root / 'release.json').write_text(json.dumps(record))
    return root, site, record


def test_release_verifies_application_and_actual_host_dependency_bytes(tmp_path):
    root, _, record = fixture(tmp_path)
    assert host_runtime.verify(root) == record
    (root / 'app.py').write_text('VERSION=2\n')
    with pytest.raises(ValueError, match='release files'): host_runtime.verify(root)


@pytest.mark.parametrize('problem', ['dependency', 'extra-file', 'symlink', 'interpreter', 'approval', 'loader', 'cache'])
def test_changed_runtime_and_forged_authority_cannot_start_a_release(tmp_path, problem):
    root, site, record = fixture(tmp_path)
    if problem == 'dependency': (site / 'dependency.py').write_text('version=2\n')
    if problem == 'extra-file': (root / 'foreign.py').write_text('foreign=1\n')
    if problem == 'symlink': (root / 'foreign.py').symlink_to(site / 'dependency.py')
    if problem == 'interpreter': record['interpreter_sha256'] = '0' * 64
    if problem == 'approval': record['model_activation_allowed'] = True
    if problem == 'loader': record['runtime_environment']['LD_PRELOAD'] = '/untrusted.so'
    if problem == 'cache':
        (root / '__pycache__').mkdir();(root / '__pycache__/app.pyc').write_bytes(b'forged')
    (root / 'release.json').write_text(json.dumps(record))
    with pytest.raises(ValueError): host_runtime.verify(root)


def test_private_session_never_accepts_public_or_symlink_credentials(tmp_path):
    path = tmp_path / 'session.json';host.write_private(path, {'private': 'fixture-only'})
    assert host_runtime.private_json(path) == {'private': 'fixture-only'}
    path.chmod(0o644)
    with pytest.raises(ValueError): host_runtime.private_json(path)
    path.chmod(0o600);link = tmp_path / 'link';link.symlink_to(path)
    with pytest.raises(ValueError): host_runtime.private_json(link)


def test_later_dev_dependency_changes_do_not_replace_copied_release_bytes(tmp_path):
    root, _, record = fixture(tmp_path)
    dev = Path(record['runtime_source']);dev.mkdir();(dev / 'dependency.py').write_text('version=999\n')
    assert host_runtime.verify(root) == record


def test_operator_credentials_are_selected_only_for_capable_api_releases():
    environment = {'SENTINELOPS_API_TOKEN':'producer-fixture', 'SENTINELOPS_OPERATOR_TOKEN':'operator-fixture',
                   'CONTROL_DATABASE_URL':'fixture-private-dsn', 'FOREIGN':'not-allowed'}
    legacy = host.process_environment('api', {}, environment)
    assert legacy['SENTINELOPS_API_TOKEN'] == 'producer-fixture' and 'SENTINELOPS_OPERATOR_TOKEN' not in legacy
    capable = host.process_environment('api', {'credential_profile':'local-operator-read/v1'}, environment)
    assert capable['SENTINELOPS_OPERATOR_TOKEN'] == 'operator-fixture' and 'FOREIGN' not in capable
    for role in ('health', 'incidents'):
        selected = host.process_environment(role, {'credential_profile':'local-operator-read/v1'}, environment)
        assert 'SENTINELOPS_API_TOKEN' not in selected and 'SENTINELOPS_OPERATOR_TOKEN' not in selected


def test_unknown_release_credential_profile_is_rejected(tmp_path):
    root, _, record = fixture(tmp_path)
    record['credential_profile'] = 'unknown-role-profile/v99'
    (root / 'release.json').write_text(json.dumps(record))
    with pytest.raises(ValueError, match='credential profile'): host_runtime.verify(root)


def test_units_pin_interpreter_session_and_do_not_contain_credentials(tmp_path):
    root, _, _ = fixture(tmp_path)
    session = tmp_path / 'session.json';host.write_private(session, {'token': 'SECRET_FIXTURE'})
    text = host.unit_text('api', root, session)
    assert '-I' in text and '-S' in text and host_runtime.digest(session) in text
    assert 'SECRET_FIXTURE' not in text and 'Restart=on-failure' in text
    assert 'KillMode=control-group' in text and 'ExecStart=:' in text
    assert 'WorkingDirectory=' not in text  # Bootstrap chdir is explicit; this directive does not use argv quoting.
    assert host.quoted('/path with % and $HOME') == '"/path with %% and $HOME"'
    with pytest.raises(ValueError): host.quoted('path\nExecStart=bad')


def test_roles_preserve_b_source_stores_and_explicit_incident_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_b, 'ROOT', tmp_path / 'b')
    assert str(lane_b.ROOT / 'health') in host.arguments('health', tmp_path, None)
    with pytest.raises(ValueError): host.arguments('incidents', tmp_path, None)
    args = host.arguments('incidents', tmp_path, 'a' * 64)
    assert str(lane_b.ROOT / 'health') in args and str(lane_b.ROOT / 'incidents') in args
    assert '--project' in args and 'sentinelops-b' in args


def test_foreign_unit_is_rejected_before_any_lifecycle_mutation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(lane_b, 'ROOT', tmp_path / 'b')
    calls = []
    def systemctl(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='LoadState=loaded\nActiveState=active\nFragmentPath=/foreign/unit.service\n')
    monkeypatch.setattr(host, 'systemctl', systemctl)
    with pytest.raises(ValueError, match='unowned'): host.stop('api')
    assert len(calls) == 1 and calls[0][0] == 'show'


def test_systemd_link_resolves_only_to_an_owned_bundle_unit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(lane_b, 'ROOT', tmp_path / 'b')
    unit = lane_b.ROOT / 'host-services' / 'session' / host.unit_name('api')
    unit.parent.mkdir(parents=True);unit.write_text('fixture-unit')
    link = tmp_path / 'user-config' / unit.name;link.parent.mkdir();link.symlink_to(unit)
    result = SimpleNamespace(returncode=0, stdout='LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123\nFragmentPath='+str(link)+'\n')
    monkeypatch.setattr(host, 'systemctl', lambda *a, **kw: result)
    assert host.state('api')['unit_file'] == str(unit.resolve())


def test_host_health_arguments_append_only_an_explicit_profile(tmp_path):
    v2 = 'prometheus-increase-60s-5s/v2'
    v1_args = host.arguments('health', tmp_path, None)
    assert '--source-profile' not in v1_args
    assert host.arguments('health', tmp_path, None, source_profile=v2) == [*v1_args, '--source-profile', v2]


@pytest.mark.parametrize('operation,initial_state', [
    ('start', 'inactive'), ('restart', 'inactive'),
    ('start', 'failed'), ('restart', 'failed'),
    ('start', 'active'), ('restart', 'active'),
])
def test_selected_role_recovers_when_systemd_unloads_an_inactive_unit(tmp_path, monkeypatch, operation, initial_state):
    from contextlib import nullcontext
    import subprocess

    release_id = 'a' * 32
    unit = tmp_path / 'api.service'
    unit.write_text('fixture-owned-unit')
    environment = {'SENTINELOPS_API_TOKEN': 'producer-fixture',
                   'SENTINELOPS_OPERATOR_TOKEN': 'operator-fixture'}
    host.write_private(tmp_path / 'session.json', {'release_id': release_id,
                       'environment': environment, 'arguments': []})
    (tmp_path / 'release.json').write_text(json.dumps({'credential_profile': 'local-operator-read/v1'}))
    current = {'state': initial_state, 'unit': unit.name, 'unit_file': str(unit)}
    calls = []
    monkeypatch.setattr(host, 'locked', lambda: nullcontext(tmp_path))
    monkeypatch.setattr(host, 'state', lambda role: dict(current))
    monkeypatch.setattr(host, 'release', lambda identifier: tmp_path)
    monkeypatch.setattr(lane_b, 'runtime_environment', lambda *args: environment)
    monkeypatch.setattr(host, 'validate_selected', lambda *args: calls.append('validate'))

    def systemctl(command, name):
        # After show/validation an inactive unit can be garbage-collected. It can
        # still be started by name, but reset-failed rejects an unloaded unit.
        if command == 'reset-failed' and initial_state == 'inactive':
            raise subprocess.CalledProcessError(1, ['systemctl', command, name],
                                               stderr='Unit ' + name + ' not loaded.')
        calls.append(command)
        if command in ('start', 'restart'):
            current['state'] = 'active'
    monkeypatch.setattr(host, 'systemctl', systemctl)
    result = host.start('api', release_id) if operation == 'start' else host.restart('api')
    assert result['state'] == 'active'
    expected = ['validate']
    if operation == 'restart' or initial_state != 'active':
        if initial_state != 'inactive':
            expected.append('reset-failed')
        expected.append(operation)
    assert calls == expected


@pytest.mark.parametrize('operation', ['start', 'restart'])
def test_inactive_recovery_still_checks_selected_bytes_before_systemd(tmp_path, monkeypatch, operation):
    from contextlib import nullcontext

    unit = tmp_path / 'api.service'
    unit.write_text('fixture-owned-unit')
    host.write_private(tmp_path / 'session.json', {'release_id': 'a' * 32,
                       'environment': {}, 'arguments': []})
    (tmp_path / 'release.json').write_text('{}')
    monkeypatch.setattr(host, 'locked', lambda: nullcontext(tmp_path))
    monkeypatch.setattr(host, 'state', lambda role: {'state': 'inactive', 'unit': unit.name, 'unit_file': str(unit)})
    monkeypatch.setattr(host, 'release', lambda identifier: tmp_path)
    monkeypatch.setattr(lane_b, 'runtime_environment', lambda *args: {})
    def reject(*args):
        raise host.HostGuard('selected source or session changed')
    monkeypatch.setattr(host, 'validate_selected', reject)
    mutations = []
    monkeypatch.setattr(host, 'systemctl', lambda *args: mutations.append(args))
    with pytest.raises(host.HostGuard, match='selected source or session changed'):
        host.start('api', 'a' * 32) if operation == 'start' else host.restart('api')
    assert mutations == []
