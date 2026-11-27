import hashlib
import json
import subprocess

import pytest

from scripts import continue_benchmark_capture as sequence


def setup(tmp_path):
    root, project = tmp_path / 'data', tmp_path / 'project'
    (root / 'capture').mkdir(parents=True)
    project.mkdir()
    registration = root / 'capture/registration.json'
    registration.write_text(json.dumps({'capture_commit': 'a' * 40}))
    return root, project, hashlib.sha256(registration.read_bytes()).hexdigest()


def test_sequence_waits_then_verifies_and_runs_remaining_groups(tmp_path, monkeypatch):
    root, project, pin = setup(tmp_path)
    alive = iter([True, False])
    monkeypatch.setattr(sequence, 'process_active', lambda *args: next(alive))
    monkeypatch.setattr(sequence.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(sequence.subprocess, 'check_output',
                        lambda args, **kwargs: 'a' * 40 + '\n' if args[0] == 'git' else '')
    calls = []
    monkeypatch.setattr(sequence.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    sequence.continue_groups(root, project, registration_sha256=pin, source_commit='a' * 40,
                             first_pid=42, first_start_ticks='123')
    stages = [(args[args.index('--group') - 1], args[args.index('--group') + 1])
              for args in calls if '--group' in args]
    assert stages == [('verify-group', 'bench-final-01'),
        *[(stage, f'bench-final-{ordinal:02d}') for ordinal in range(2, 6) for stage in ('capture', 'verify-group')]]
    assert 'seal-capture' in calls[-1]


def test_sequence_stops_on_incomplete_group_without_starting_next(tmp_path, monkeypatch):
    root, project, pin = setup(tmp_path)
    monkeypatch.setattr(sequence, 'process_active', lambda *args: False)
    monkeypatch.setattr(sequence.subprocess, 'check_output', lambda *args, **kwargs: 'a' * 40 + '\n')
    calls = []
    def failed(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(sequence.subprocess, 'run', failed)
    with pytest.raises(subprocess.CalledProcessError):
        sequence.continue_groups(root, project, registration_sha256=pin, source_commit='a' * 40,
                                 first_pid=42, first_start_ticks='123')
    assert len(calls) == 1 and 'verify-group' in calls[0] and 'capture' not in calls[0]


def test_sequence_rejects_changed_registration_before_start(tmp_path, monkeypatch):
    root, project, _ = setup(tmp_path)
    monkeypatch.setattr(sequence.subprocess, 'check_output', lambda *args, **kwargs: pytest.fail('must not start'))
    with pytest.raises(ValueError, match='registration differs'):
        sequence.continue_groups(root, project, registration_sha256='b' * 64, source_commit='a' * 40,
                                 first_pid=42, first_start_ticks='123')
