"""Host-release integrity fixtures, never model/source/action approval."""
import json
import os
from pathlib import Path
import sys

import pytest

from scripts import host_runtime, lane_b_host as host, lane_b


def fixture(tmp_path):
    root = tmp_path / 'release';root.mkdir()
    site = tmp_path / 'dependencies';site.mkdir();(site / 'dependency.py').write_text('version=1\n')
    (root / 'app.py').write_text('VERSION=1\n')
    record = {'release_version': 'lane-b-host/v1', 'release_id': 'a' * 32, 'root': str(root.resolve()),
        'interpreter': str(Path(sys.executable).resolve()), 'interpreter_sha256': host_runtime.digest(Path(sys.executable).resolve()),
        'python_version': sys.version, 'site_packages': str(site), 'runtime_files': host_runtime.inventory(site),
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


def test_units_pin_interpreter_session_and_do_not_contain_credentials(tmp_path):
    root, _, _ = fixture(tmp_path)
    session = tmp_path / 'session.json';host.write_private(session, {'token': 'SECRET_FIXTURE'})
    text = host.unit_text('api', root, session)
    assert '-I' in text and '-S' in text and host_runtime.digest(session) in text
    assert 'SECRET_FIXTURE' not in text and 'Restart=on-failure' in text
    assert 'KillMode=control-group' in text and 'ExecStart=:' in text
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
