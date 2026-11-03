"""B-owned host-bound wheel releases and explicitly selected user systemd units."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
from uuid import uuid4
from urllib.parse import urlsplit

from scripts import lane_b
from scripts.host_runtime import digest, inventory, private_json, verify
from sentinelops.telemetry.core import stable_id

ROLES = ('api', 'health', 'incidents')
ENVIRONMENT = ('CONTROL_DATABASE_URL', 'COMMERCE_EVIDENCE_DATABASE_URL', 'SENTINELOPS_API_TOKEN',
               'SENTINELOPS_ENV', 'SENTINELOPS_ENABLE_DEMO', 'SENTINELOPS_MODEL_STORE', 'SENTINELOPS_MODEL_RELEASE_REF')


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True);stream.flush();os.fsync(stream.fileno())


def build():
    lane_b.init_runtime()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=lane_b.PROJECT).strip():
        raise ValueError('commit release inputs before building')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=lane_b.PROJECT, text=True).strip()
    identifier = uuid4().hex
    root = lane_b.ROOT / 'host-releases' / identifier
    root.mkdir(parents=True, mode=0o700)
    wheels = root / 'wheels';wheels.mkdir()
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                    '--wheel-dir', str(wheels), str(lane_b.PROJECT)], check=True, stdout=subprocess.DEVNULL)
    wheel, = wheels.glob('sentinelops-*.whl')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-compile',
                    '--target', str(root / 'site'), str(wheel)], check=True, stdout=subprocess.DEVNULL)
    cli = root / 'cli' / 'scripts';cli.mkdir(parents=True)
    (cli / '__init__.py').write_text('')
    for name in ('run_health_worker.py', 'run_incident_worker.py'):
        shutil.copyfile(lane_b.PROJECT / 'scripts' / name, cli / name)
    config = root / 'configs';config.mkdir()
    for name in ('slo_health_policy.json', 'rca_policy.json', 'topology.yaml'):
        shutil.copyfile(lane_b.PROJECT / 'configs' / name, config / name)
    shutil.copyfile(lane_b.PROJECT / 'scripts/host_runtime.py', root / 'launch.py')
    record = {'release_version': 'lane-b-host/v1', 'release_id': identifier, 'root': str(root.resolve()),
              'source_commit': commit, 'interpreter': str(Path(sys.executable).resolve()),
              'interpreter_sha256': digest(Path(sys.executable).resolve()), 'python_version': sys.version,
              'site_packages': sysconfig.get_paths()['purelib'],
              'runtime_files': inventory(Path(sysconfig.get_paths()['purelib'])), 'files': inventory(root),
              'runtime_environment': {k: os.environ[k] for k in ('PATH', 'LD_LIBRARY_PATH', 'SSL_CERT_FILE') if k in os.environ},
              'model_activation_allowed': False, 'remediation_approval': False, 'portable': False}
    if (subprocess.check_output(['git', 'status', '--porcelain'], cwd=lane_b.PROJECT).strip()
        or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=lane_b.PROJECT, text=True).strip() != commit):
        raise ValueError('release inputs changed during build; incomplete bundle is not selected')
    with (root / 'release.json').open('x') as stream: json.dump(record, stream, sort_keys=True)
    verify(root)
    return {'release_id': identifier, 'release_manifest_sha256': digest(root / 'release.json'),
            'source_commit': commit, 'portable': False, 'model_activation_allowed': False}


def release(identifier):
    if not re.fullmatch('[0-9a-f]{32}', identifier): raise ValueError('invalid release identity')
    root = (lane_b.ROOT / 'host-releases' / identifier).resolve()
    if root.parent != (lane_b.ROOT / 'host-releases').resolve(): raise ValueError('unowned release')
    verify(root)
    return root


def unit_name(role):
    if role not in ROLES: raise ValueError('unsupported role')
    tag = stable_id({'project': str(lane_b.PROJECT.resolve()), 'root': str(lane_b.ROOT.resolve())})[:12]
    return 'sentinelops-b-' + tag + '-' + role + '.service'


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check, capture_output=True, text=True)


def state(role, *, refreshed=False):
    name = unit_name(role)
    result = systemctl('show', name, '--property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus,FragmentPath', check=False)
    if result.returncode:
        raise RuntimeError('user systemd unavailable')
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('LoadState') == 'not-found':
        if not refreshed:
            systemctl('daemon-reload')
            return state(role, refreshed=True)
        return {'role': role, 'state': 'NOT_INSTALLED', 'unit': name}
    path = Path(values.get('FragmentPath', ''))
    home = (lane_b.ROOT / 'host-services').resolve()
    if (not path.is_absolute() or not path.resolve().is_relative_to(home) or path.name != name
        or path.lstat().st_uid != os.getuid() or not path.is_file() or path.is_symlink()):
        raise ValueError('refuse an unowned systemd unit')
    return {'role': role, 'unit': name, 'state': values['ActiveState'], 'substate': values['SubState'],
            'pid': int(values.get('MainPID', 0)), 'exit_status': int(values.get('ExecMainStatus', 0)),
            'unit_file': str(path.resolve())}


@contextmanager
def locked():
    lane_b.init_runtime()
    root = lane_b.ROOT / 'host-services';root.mkdir(mode=0o700, exist_ok=True)
    with (root / 'manager.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield root


def quoted(value):
    # systemd specifiers expand even in quoted arguments; no shell is involved.
    if '\n' in value or '\r' in value or '\x00' in value: raise ValueError('invalid unit value')
    return json.dumps(value.replace('%', '%%'))


def unit_text(role, root, session):
    record = json.loads((root / 'release.json').read_text())
    command = ' '.join(quoted(v) for v in (record['interpreter'], '-I', '-S', str(root / 'launch.py'), role,
                                        str(session), digest(session)))
    env = '\n'.join('Environment=' + quoted(k + '=' + v) for k, v in record['runtime_environment'].items())
    return f'''[Unit]
Description=SentinelOps lane B {role}
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
ExecStart=:{command}
WorkingDirectory={quoted(str(root))}
{env}
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=PYTHONPATH PYTHONHOME LD_PRELOAD LD_AUDIT
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=control-group
UMask=0077
NoNewPrivileges=yes
'''


def arguments(role, root, stream):
    if role == 'api': return []
    shared = ['--prometheus', 'http://127.0.0.1:19090', '--metrics-otlp-endpoint', 'http://127.0.0.1:14317',
              '--health-policy' if role == 'incidents' else '--policy', str(root / 'configs/slo_health_policy.json')]
    if role == 'health': return ['--store', str(lane_b.ROOT / 'health'), *shared]
    if not stream or not re.fullmatch('[0-9a-f]{64}', stream): raise ValueError('explicit accepted health stream required')
    return ['--health-stream', stream, '--source-store', str(lane_b.ROOT / 'health'),
            '--store', str(lane_b.ROOT / 'incidents'), '--project', lane_b.NAME,
            '--jaeger', 'http://127.0.0.1:26686', '--rca-policy', str(root / 'configs/rca_policy.json'),
            '--topology', str(root / 'configs/topology.yaml'), *shared]


def start(role, identifier, stream=None):
    with locked() as home:
        current = state(role)
        root = release(identifier)
        if current['state'] != 'NOT_INSTALLED':
            session = private_json(Path(current['unit_file']).parent / 'session.json')
            if session['release_id'] != identifier or session['arguments'] != arguments(role, root, stream):
                raise ValueError('stop selected unit before choosing a different release/source')
            validate_selected(role, current)
            if current['state'] != 'active': systemctl('start', current['unit'])
            return state(role)
        if role != 'api': lane_b.require_owned_containers()
        environment = lane_b.runtime_environment()
        selected = {key: environment[key] for key in ENVIRONMENT if key in environment}
        if role == 'api' and os.getenv('SENTINELOPS_ACTION_READ_DATABASE_URL'):
            reader = os.environ['SENTINELOPS_ACTION_READ_DATABASE_URL'];parsed = urlsplit(reader)
            if (parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname != '127.0.0.1'
                or parsed.port != 15433 or parsed.path != '/sentinelops_control' or parsed.query or parsed.fragment
                or not parsed.username or not parsed.password):
                raise ValueError('managed action reader must select B control database')
            selected['SENTINELOPS_ACTION_READ_DATABASE_URL'] = reader
        folder = home / uuid4().hex;folder.mkdir(mode=0o700)
        session = folder / 'session.json'
        payload = {'role': role, 'release_id': identifier, 'release_manifest_sha256': digest(root / 'release.json'),
                   'environment': selected, 'arguments': arguments(role, root, stream),
                   'health_store': str(lane_b.ROOT / 'health')}
        if role != 'api':
            probe = folder / 'probe.json';write_private(probe, payload)
            record = json.loads((root / 'release.json').read_text())
            try:
                result = subprocess.run([record['interpreter'], '-I', '-S', str(root / 'launch.py'), 'probe',
                    str(probe), digest(probe)], capture_output=True, text=True, check=True,
                    env=record['runtime_environment'], timeout=60)
            finally: probe.unlink()
            payload['source_identity'] = json.loads(result.stdout)
            if role == 'incidents' and stream != payload['source_identity']['health_stream_id']:
                raise ValueError('incident selection differs from current health identity')
        write_private(session, payload)
        unit = folder / unit_name(role)
        unit.write_text(unit_text(role, root, session))
        systemctl('link', str(unit))
        systemctl('daemon-reload')
        systemctl('start', unit.name)
        return state(role)


def validate_selected(role, current):
    path = Path(current['unit_file'])
    session_path = path.parent / 'session.json';session = private_json(session_path)
    root = release(session['release_id'])
    if (session['role'] != role or session['release_manifest_sha256'] != digest(root / 'release.json')
        or path.read_text() != unit_text(role, root, session_path)):
        raise ValueError('selected unit/session/release changed')
    if role != 'api':
        lane_b.require_owned_containers()
        record = json.loads((root / 'release.json').read_text())
        result = subprocess.run([record['interpreter'], '-I', '-S', str(root / 'launch.py'), 'probe',
            str(session_path), digest(session_path)], capture_output=True, text=True, check=True,
            env=record['runtime_environment'], timeout=60)
        if json.loads(result.stdout) != session['source_identity']:
            raise ValueError('selected health source changed')
    return session, root


def stop(role):
    with locked():
        current = state(role)
        if current['state'] == 'NOT_INSTALLED': return current
        systemctl('stop', current['unit'])
        # disable removes only this reviewed user link; units have no Install section.
        systemctl('disable', current['unit'])
        systemctl('daemon-reload')
        return state(role)


def restart(role):
    with locked():
        current = state(role)
        if current['state'] == 'NOT_INSTALLED': raise ValueError('role is not installed')
        validate_selected(role, current)  # Reject corrupt bytes before stopping a running process.
        systemctl('restart', current['unit'])
        return state(role)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('build', 'verify', 'start', 'status', 'stop', 'restart'))
    parser.add_argument('--role', choices=ROLES)
    parser.add_argument('--release-id')
    parser.add_argument('--health-stream')
    args = parser.parse_args()
    try:
        if args.operation == 'build': result = build()
        elif args.operation == 'verify':
            release(args.release_id or '');result = {'status': 'VERIFIED', 'release_id': args.release_id}
        elif not args.role: parser.error('role is required')
        elif args.operation == 'status': result = state(args.role)
        elif args.operation == 'start': result = start(args.role, args.release_id or '', args.health_stream)
        elif args.operation == 'stop': result = stop(args.role)
        else: result = restart(args.role)
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        raise SystemExit('host process operation failed: ' + type(exc).__name__) from None


if __name__ == '__main__': main()
