"""ECR publishing safety checks using only fake subprocesses and local fixtures."""
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

import pytest

from scripts import push_images_ecr as ecr

ACCOUNT = '123456789012'
REGION = 'ap-southeast-1'
REGISTRY = f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com'
REVISION = 'a' * 40
BASE = 'python:3.11-slim@sha256:' + 'b' * 64
REPOS = ('sentinelops/application', 'sentinelops/control-plane')
DIGESTS = ('sha256:' + 'c' * 64, 'sha256:' + 'd' * 64)
TOKEN = 'fixture-ecr-password-must-never-be-logged\n'


@pytest.fixture(autouse=True)
def forbid_real_subprocess(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('a real subprocess must never be reached')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def cli(tmp_path):
    return ['--account-id', ACCOUNT, '--region', REGION,
            '--application-repo', REPOS[0], '--control-plane-repo', REPOS[1],
            '--tag', 'reviewed-day1', '--source-revision', REVISION,
            '--application-base', BASE, '--digest-file', str(tmp_path / 'digests.json')]


def archive(dockerfile='FROM python:3.11-slim\nRUN echo fixture\n'):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as bundle:
        data = dockerfile.encode()
        member = tarfile.TarInfo('docker/Dockerfile.service')
        member.size = len(data)
        bundle.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class FakeCommands:
    def __init__(self):
        self.calls = []
        self.account = ACCOUNT
        self.mutability = ['IMMUTABLE', 'IMMUTABLE']
        self.uris = [REGISTRY + '/' + repo for repo in REPOS]
        self.digests = list(DIGESTS)
        self.push_outputs = [f'tag: digest: {digest} size: 123\n' for digest in DIGESTS]
        self.bundle = archive()
        self.fail_login = False
        self.snapshots = []
        self.evidence = None

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        assert kwargs['check'] is True
        assert kwargs['cwd'] == ecr.PROJECT
        output = None
        if command[0] == 'aws':
            assert command[1:6] == ['--region', REGION, '--no-cli-pager', '--output', 'json']
            assert command[6] in ('sts', 'ecr')
            action = command[7]
            if action == 'get-caller-identity':
                output = json.dumps({'Account': self.account})
            elif action == 'describe-repositories':
                index = REPOS.index(command[-1])
                output = json.dumps({'repositories': [{'repositoryUri': self.uris[index],
                    'imageTagMutability': self.mutability[index]}]})
            elif action == 'get-login-password':
                output = TOKEN
            elif action == 'describe-images':
                index = REPOS.index(command[command.index('--repository-name') + 1])
                output = json.dumps({'imageDetails': [{'imageDigest': self.digests[index]}]})
            else:
                pytest.fail('unexpected AWS action: ' + action)
            assert kwargs['capture_output'] is True and kwargs['text'] is True
        elif command[0] == 'git':
            assert command == ['git', 'archive', '--format=tar', REVISION]
            assert kwargs['capture_output'] is True and kwargs['text'] is False
            output = self.bundle
        elif command[0] == 'docker':
            assert command[1] == '--config'
            assert Path(command[2]).stat().st_mode & 0o777 == 0o700
            action = command[3]
            if action == 'login':
                assert command[4:] == ['--username', 'AWS', '--password-stdin', REGISTRY]
                assert kwargs['input'] == TOKEN
                if self.fail_login:
                    raise subprocess.CalledProcessError(1, command)
            elif action == 'build':
                self.snapshots.append(json.loads(self.evidence.read_text()))
            elif action == 'push':
                assert kwargs['capture_output'] is True
                index = next(i for i, repo in enumerate(REPOS) if '/' + repo + ':' in command[-1])
                output = self.push_outputs[index]
            else:
                pytest.fail('unexpected Docker action: ' + action)
        else:
            pytest.fail('unexpected executable: ' + command[0])
        return subprocess.CompletedProcess(command, 0, stdout=output)


@pytest.fixture
def commands(monkeypatch, cli):
    fake = FakeCommands()
    fake.evidence = Path(cli[-1])
    monkeypatch.setattr(subprocess, 'run', fake)
    return fake


@pytest.mark.parametrize('mode', [[], ['--dry-run']])
def test_default_and_explicit_dry_run_only_print_commands(cli, commands, capsys, mode):
    ecr.main(cli + mode)
    assert commands.calls == []
    assert not commands.evidence.exists()
    output = capsys.readouterr().out
    assert 'DRY-RUN (no commands executed)' in output
    assert 'FROM ' + BASE in output
    planned = [shlex.split(line[2:]) for line in output.splitlines() if line.startswith('+ ')]
    assert [command[0] for command in planned] == ['aws', 'aws', 'aws', 'git', 'aws',
                                                               'docker', 'docker', 'docker', 'aws',
                                                               'docker', 'docker', 'aws']
    assert planned[3] == ['git', 'archive', '--format=tar', REVISION]
    for repo in REPOS:
        assert any(command[-2:] == ['push', REGISTRY + '/' + repo + ':reviewed-day1']
                   for command in planned)


@pytest.mark.parametrize('missing', ['--account-id', '--region', '--application-repo',
    '--control-plane-repo', '--tag', '--source-revision', '--application-base', '--digest-file'])
def test_missing_required_argument_fails_before_any_command(cli, commands, missing):
    index = cli.index(missing)
    del cli[index:index + 2]
    with pytest.raises(SystemExit) as error:
        ecr.main(cli + ['--execute'])
    assert error.value.code == 2
    assert commands.calls == [] and not commands.evidence.exists()


@pytest.mark.parametrize(('option', 'value'), [
    ('--account-id', '123'), ('--account-id', '12345678901x'),
    ('--region', 'us-east-1'), ('--region', 'ap-southeast-2'),
    ('--application-repo', 'registry.example/app:tag'),
    ('--control-plane-repo', REPOS[0]), ('--tag', 'latest'), ('--tag', 'LATEST'),
    ('--tag', 'bad tag'), ('--source-revision', 'a' * 39),
    ('--source-revision', 'A' * 40), ('--application-base', 'python:3.11-slim'),
    ('--application-base', 'python:3.12-slim@sha256:' + 'b' * 64),
    ('--application-base', 'python:3.11-slim@sha256:' + 'B' * 64),
    ('--application-base', 'python:3.11-slim@sha256:' + 'b' * 63),
])
def test_invalid_inputs_fail_closed(cli, commands, option, value):
    cli[cli.index(option) + 1] = value
    with pytest.raises(SystemExit) as error:
        ecr.main(cli + ['--execute'])
    assert error.value.code == 2
    assert commands.calls == [] and not commands.evidence.exists()


def test_sts_account_mismatch_stops_before_repository_checks(cli, commands):
    commands.account = '999999999999'
    with pytest.raises(ValueError, match='identity does not match'):
        ecr.main(cli + ['--execute'])
    assert len(commands.calls) == 1
    assert commands.calls[0][0][-2:] == ['sts', 'get-caller-identity']
    assert not commands.evidence.exists()


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('guard', ['mutable', 'uri'])
def test_repository_preflight_stops_before_archive_login_or_build(cli, commands, index, guard):
    if guard == 'mutable':
        commands.mutability[index] = 'MUTABLE'
    else:
        commands.uris[index] = 'unexpected/repository'
    with pytest.raises(ValueError, match='immutable tag guard'):
        ecr.main(cli + ['--execute'])
    assert len(commands.calls) == index + 2
    assert all(command[0] == 'aws' for command, _ in commands.calls)
    assert not commands.evidence.exists()


def test_push_compares_digests_and_records_both_pinned_images(cli, commands, capsys, monkeypatch):
    credentials = {'AWS_ACCESS_KEY_ID': 'fixture-access-key',
                   'AWS_SECRET_ACCESS_KEY': 'fixture-secret-key',
                   'AWS_SESSION_TOKEN': 'fixture-session-token'}
    for key, value in credentials.items():
        monkeypatch.setenv(key, value)
    ecr.main(cli + ['--execute'])
    record = json.loads(commands.evidence.read_text())
    assert record == {'source_revision': REVISION, 'account_id': ACCOUNT, 'region': REGION,
        'platform': 'linux/amd64', 'tag': 'reviewed-day1', 'application_base': BASE,
        'status': 'complete', 'images': {kind: {
            'tagged': REGISTRY + '/' + repo + ':reviewed-day1', 'digest': digest,
            'pinned': REGISTRY + '/' + repo + '@' + digest}
            for kind, repo, digest in zip(('application', 'control-plane'), REPOS, DIGESTS)}}
    assert commands.snapshots[0]['status'] == 'partial'
    assert commands.snapshots[0]['images'] == {}
    assert commands.snapshots[1]['images'] == {'application': record['images']['application']}
    builds = [(cmd, options) for cmd, options in commands.calls if cmd[0] == 'docker' and cmd[3] == 'build']
    assert len(builds) == 2
    for command, _ in builds:
        assert command[command.index('--platform') + 1] == 'linux/amd64'
        assert 'org.opencontainers.image.revision=' + REVISION in command
    application, control = builds
    assert application[1]['input'] == 'FROM ' + BASE + '\nRUN echo fixture\n'
    assert application[0][-3] == '--file' and application[0][-2] == '-'
    assert 'SENTINELOPS_SOURCE_REVISION=' + REVISION in control[0]
    assert control[1]['input'] is None
    assert control[0][-2].endswith('/docker/Dockerfile.control-plane')
    assert not Path(application[0][-1]).exists()  # Temporary build/config context removed.
    captured = capsys.readouterr()
    logs = captured.out + captured.err
    for kind, digest in zip(('application', 'control-plane'), DIGESTS):
        assert 'PIN ' + kind + '=' in logs and digest in logs
    for secret in [TOKEN.strip(), *credentials.values()]:
        assert secret not in logs and secret not in commands.evidence.read_text()
        assert all(secret not in shlex.join(cmd) for cmd, _ in commands.calls)


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('failure', ['mismatch', 'invalid-ecr', 'missing-push', 'duplicate-push'])
def test_digest_failure_never_records_unverified_image_and_preserves_partial_evidence(
        cli, commands, index, failure):
    if failure == 'mismatch':
        commands.digests[index] = 'sha256:' + 'e' * 64
    elif failure == 'invalid-ecr':
        commands.digests[index] = 'not-a-digest'
    elif failure == 'missing-push':
        commands.push_outputs[index] = 'push completed without digest\n'
    else:
        commands.push_outputs[index] *= 2
    with pytest.raises(ValueError, match='push digest and ECR digest must match'):
        ecr.main(cli + ['--execute'])
    record = json.loads(commands.evidence.read_text())
    assert record['status'] == 'partial'
    assert set(record['images']) == (set() if index == 0 else {'application'})
    if index == 1:
        assert record['images']['application']['digest'] == DIGESTS[0]
    pushes = [cmd for cmd, _ in commands.calls if cmd[0] == 'docker' and cmd[3] == 'push']
    assert len(pushes) == index + 1


def test_changed_application_base_stops_before_authentication_or_build(cli, commands):
    commands.bundle = archive('FROM python:3.12-slim\n')
    with pytest.raises(ValueError, match='Dockerfile base changed'):
        ecr.main(cli + ['--execute'])
    assert not any(cmd[0] == 'docker' or 'get-login-password' in cmd for cmd, _ in commands.calls)
    assert json.loads(commands.evidence.read_text())['status'] == 'partial'


def test_login_failure_does_not_log_password_or_start_build(cli, commands, capsys):
    commands.fail_login = True
    with pytest.raises(subprocess.CalledProcessError) as error:
        ecr.main(cli + ['--execute'])
    captured = capsys.readouterr()
    assert TOKEN.strip() not in captured.out + captured.err + str(error.value)
    assert not any(cmd[0] == 'docker' and cmd[3] in ('build', 'push') for cmd, _ in commands.calls)
    record = json.loads(commands.evidence.read_text())
    assert record['status'] == 'partial' and record['images'] == {}


@pytest.mark.parametrize('existing', ['file', 'symlink', 'missing-parent'])
def test_evidence_destination_fails_closed_without_overwrite(cli, commands, existing):
    destination = commands.evidence
    if existing == 'file':
        destination.write_text('previous evidence')
    elif existing == 'symlink':
        destination.symlink_to(destination.parent / 'absent.json')
    else:
        cli[-1] = str(destination.parent / 'absent' / 'digest.json')
    with pytest.raises(SystemExit) as error:
        ecr.main(cli + ['--execute'])
    assert error.value.code == 2 and commands.calls == []
    if existing == 'file':
        assert destination.read_text() == 'previous evidence'
    elif existing == 'symlink':
        assert destination.is_symlink() and not destination.exists()
