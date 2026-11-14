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
import socket
import subprocess
import sys
import sysconfig
from uuid import uuid4
from urllib.parse import urlsplit

from scripts import lane_b
from scripts.host_runtime import HostGuard, digest, inventory, private_json, verify
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
        raise HostGuard('commit release inputs before building')
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
    for name in ('slo_health_policy.json', 'rca_policy.json', 'topology.yaml', 'alibaba_relative_features.json'):
        shutil.copyfile(lane_b.PROJECT / 'configs' / name, config / name)
    shutil.copyfile(lane_b.PROJECT / 'scripts/host_runtime.py', root / 'launch.py')
    original_runtime = Path(sysconfig.get_paths()['purelib'])
    before_dependencies = inventory(original_runtime)
    shutil.copytree(original_runtime, root / 'dependencies', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    expected_dependencies = {name: value['sha256'] if isinstance(value, dict) else value
                             for name, value in before_dependencies.items() if not name.endswith('.pyc')}
    if (inventory(original_runtime) != before_dependencies
        or inventory(root / 'dependencies') != expected_dependencies):
        raise HostGuard('installed dependencies changed during snapshot')
    record = {'release_version': 'lane-b-host/v2', 'release_id': identifier, 'root': str(root.resolve()),
              'source_commit': commit, 'interpreter': str(Path(sys.executable).resolve()),
              'interpreter_sha256': digest(Path(sys.executable).resolve()), 'python_version': sys.version,
              'site_packages': str(root.resolve() / 'dependencies'), 'runtime_source': str(original_runtime),
              'files': inventory(root),
              'runtime_environment': {k: os.environ[k] for k in ('PATH', 'LD_LIBRARY_PATH', 'SSL_CERT_FILE') if k in os.environ},
              'model_activation_allowed': False, 'remediation_approval': False, 'portable': False}
    record['credential_profile'] = 'local-operator-read/v1'
    if (subprocess.check_output(['git', 'status', '--porcelain'], cwd=lane_b.PROJECT).strip()
        or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=lane_b.PROJECT, text=True).strip() != commit):
        raise HostGuard('release inputs changed during build; incomplete bundle is not selected')
    with (root / 'release.json').open('x') as stream: json.dump(record, stream, sort_keys=True)
    verify(root)
    return {'release_id': identifier, 'release_manifest_sha256': digest(root / 'release.json'),
            'source_commit': commit, 'portable': False, 'model_activation_allowed': False}


def release(identifier):
    if not re.fullmatch('[0-9a-f]{32}', identifier): raise HostGuard('invalid release identity')
    root = (lane_b.ROOT / 'host-releases' / identifier).resolve()
    if root.parent != (lane_b.ROOT / 'host-releases').resolve(): raise HostGuard('unowned release')
    verify(root)
    return root


def unit_name(role):
    if role not in ROLES: raise HostGuard('unsupported role')
    tag = stable_id({'project': str(lane_b.PROJECT.resolve()), 'root': str(lane_b.ROOT.resolve())})[:12]
    return 'sentinelops-b-' + tag + '-' + role + '.service'


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check, capture_output=True, text=True)


def reset_failed_for_retry(current):
    # An inactive linked unit may be unloaded while release/source validation
    # runs. reset-failed then rejects it, although start/restart can load it by
    # name. Failed/active units still get the reviewed rate-limit reset.
    if current['state'] != 'inactive':
        systemctl('reset-failed', current['unit'])


def state(role, *, refreshed=False):
    name = unit_name(role)
    result = systemctl('show', name, '--property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus,FragmentPath,NRestarts', check=False)
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
        or path.lstat().st_uid != os.getuid() or not path.resolve().is_file()
        or path.resolve().stat().st_uid != os.getuid()):
        raise HostGuard('refuse an unowned systemd unit')
    return {'role': role, 'unit': name, 'state': values['ActiveState'], 'substate': values['SubState'],
            'pid': int(values.get('MainPID', 0)), 'exit_status': int(values.get('ExecMainStatus', 0)),
            'automatic_restarts': int(values.get('NRestarts', 0)),
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
    if '\n' in value or '\r' in value or '\x00' in value: raise HostGuard('invalid unit value')
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


def arguments(role, root, stream, model_store=None, model_ref=None, *, epoch=None, source_profile=None):
    if role == 'api': return []
    health_root, incident_root = lane_b.stream_roots(epoch)
    shared = ['--prometheus', 'http://127.0.0.1:19090', '--metrics-otlp-endpoint', 'http://127.0.0.1:14317',
              '--health-policy' if role == 'incidents' else '--policy', str(root / 'configs/slo_health_policy.json')]
    # v1 sessions keep their exact recorded arguments; only an explicit profile is appended.
    if role == 'health': return ['--store', str(health_root), *shared, *(['--source-profile', source_profile] if source_profile else [])]
    if not stream or not re.fullmatch('[0-9a-f]{64}', stream): raise HostGuard('explicit accepted health stream required')
    models = ['--model-store', str(model_store), '--model-release-ref', model_ref] if model_store and model_ref else []
    return ['--health-stream', stream, '--source-store', str(health_root),
            '--store', str(incident_root), '--project', lane_b.NAME,
            '--jaeger', 'http://127.0.0.1:26686', '--rca-policy', str(root / 'configs/rca_policy.json'),
            '--topology', str(root / 'configs/topology.yaml'), *shared, *models]


def process_environment(role, record, environment):
    selected = {key: environment[key] for key in ENVIRONMENT if key in environment}
    if role != 'api':
        selected.pop('SENTINELOPS_API_TOKEN', None)
    elif record.get('credential_profile') == 'local-operator-read/v1':
        selected['SENTINELOPS_OPERATOR_TOKEN'] = environment['SENTINELOPS_OPERATOR_TOKEN']
    # Legacy v2 bootstraps do not accept the new variable. Keep their environment
    # unchanged rather than modifying immutable bundles or upgrading privileges.
    return selected


def health_source_profile():
    """The profile the running health role selected; None for a v1 session."""
    current = state('health')
    if current['state'] == 'NOT_INSTALLED': return None
    session = private_json(Path(current['unit_file']).parent / 'session.json')
    recorded = session.get('arguments', [])
    if session.get('health_source_profile'): return session['health_source_profile']
    if '--source-profile' in recorded: return recorded[recorded.index('--source-profile') + 1]
    return None


def start(role, identifier, stream=None, model_store=None, model_ref=None, *, epoch=None, source_profile=None):
    health_root, _ = lane_b.stream_roots(epoch)
    with locked() as home:
        if role == 'health' and (model_store or model_ref): raise HostGuard('health does not consume models')
        current = state(role)
        root = release(identifier)
        environment = lane_b.runtime_environment(model_store, model_ref)
        selected = process_environment(role, json.loads((root / 'release.json').read_text()), environment)
        if current['state'] != 'NOT_INSTALLED':
            session = private_json(Path(current['unit_file']).parent / 'session.json')
            if (session['release_id'] != identifier or session['arguments'] != arguments(role, root, stream, model_store, model_ref, epoch=epoch, source_profile=source_profile)
                or any(session['environment'].get(k) != environment.get(k) for k in ('SENTINELOPS_MODEL_STORE', 'SENTINELOPS_MODEL_RELEASE_REF'))
                or role == 'api' and any(session['environment'].get(k) != selected.get(k)
                    for k in ('SENTINELOPS_API_TOKEN', 'SENTINELOPS_OPERATOR_TOKEN'))):
                raise HostGuard('stop selected unit before choosing a different release/source or API credential')
            validate_selected(role, current)
            if current['state'] != 'active':
                reset_failed_for_retry(current)
                systemctl('start', current['unit'])
            return state(role)
        if role != 'api': lane_b.require_owned_containers()
        if role == 'api':
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 18000))  # Never stop/adopt a foreground or foreign listener.
        if role == 'api' and os.getenv('SENTINELOPS_ACTION_READ_DATABASE_URL'):
            reader = os.environ['SENTINELOPS_ACTION_READ_DATABASE_URL'];parsed = urlsplit(reader)
            if (parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname != '127.0.0.1'
                or parsed.port != 15433 or parsed.path != '/sentinelops_control' or parsed.query or parsed.fragment
                or not parsed.username or not parsed.password):
                raise HostGuard('managed action reader must select B control database')
            selected['SENTINELOPS_ACTION_READ_DATABASE_URL'] = reader
        folder = home / uuid4().hex;folder.mkdir(mode=0o700)
        session = folder / 'session.json'
        payload = {'role': role, 'release_id': identifier, 'release_manifest_sha256': digest(root / 'release.json'),
                   'environment': selected, 'arguments': arguments(role, root, stream, model_store, model_ref, epoch=epoch, source_profile=source_profile),
                   'health_store': str(health_root)}
        # run_incident_worker has no --source-profile flag, so the selected health
        # profile is recorded as session metadata for identity derivation instead.
        profile = source_profile if role == 'health' else health_source_profile() if role == 'incidents' else None
        if profile: payload['health_source_profile'] = profile
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
                raise HostGuard('incident selection differs from current health identity')
        write_private(session, payload)
        unit = folder / unit_name(role)
        unit.write_text(unit_text(role, root, session))
        subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], check=True,
                       capture_output=True, text=True)
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
        raise HostGuard('selected unit/session/release changed')
    if role != 'api':
        lane_b.require_owned_containers()
        record = json.loads((root / 'release.json').read_text())
        result = subprocess.run([record['interpreter'], '-I', '-S', str(root / 'launch.py'), 'probe',
            str(session_path), digest(session_path)], capture_output=True, text=True, check=True,
            env=record['runtime_environment'], timeout=60)
        if json.loads(result.stdout) != session['source_identity']:
            raise HostGuard('selected health source changed')
    return session, root


def stop(role):
    with locked():
        current = state(role)
        if current['state'] == 'NOT_INSTALLED': return current
        if current['state'] != 'inactive': systemctl('stop', current['unit'])
        # disable removes only this reviewed user link; units have no Install section.
        systemctl('disable', current['unit'])
        systemctl('daemon-reload')
        return state(role)


def restart(role):
    with locked():
        current = state(role)
        if current['state'] == 'NOT_INSTALLED': raise HostGuard('role is not installed')
        validate_selected(role, current)  # Reject corrupt bytes before stopping a running process.
        reset_failed_for_retry(current)
        systemctl('restart', current['unit'])
        return state(role)


def status(role):
    current = state(role)
    if current['state'] == 'NOT_INSTALLED': return current
    session = private_json(Path(current['unit_file']).parent / 'session.json')
    return {**current, 'release_id': session['release_id'],
            'release_manifest_sha256': session['release_manifest_sha256'],
            'health_stream_id': session.get('source_identity', {}).get('health_stream_id'),
            'readiness': 'NOT_OBSERVED', 'release_integrity': 'NOT_CHECKED',
            'operator_read_token_configured': bool(session['environment'].get('SENTINELOPS_OPERATOR_TOKEN')),
            'model_activation_authorized_by_release': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('build', 'verify', 'start', 'status', 'stop', 'restart'))
    parser.add_argument('--role', choices=ROLES)
    parser.add_argument('--release-id')
    parser.add_argument('--health-stream')
    parser.add_argument('--stream-epoch', help='Explicit B stream and artifact epoch for health and incidents')
    parser.add_argument('--source-profile', choices=('prometheus-increase-60s-5s/v1', 'prometheus-increase-60s-5s/v2'))
    parser.add_argument('--model-store', type=Path)
    parser.add_argument('--model-release-ref')
    args = parser.parse_args()
    if args.health_stream and (args.operation != 'start' or args.role != 'incidents'):
        parser.error('health stream is only selected for incident start')
    if args.stream_epoch is not None and (args.operation != 'start' or args.role not in ('health', 'incidents')):
        parser.error('stream epoch is only selected for health/incident start')
    if args.source_profile and (args.operation != 'start' or args.role != 'health'):
        parser.error('source profile is only selected for health start')
    if (args.model_store or args.model_release_ref) and (args.operation != 'start' or args.role not in ('api', 'incidents')):
        parser.error('explicit H1 selection is only valid for API/incident start')
    if args.release_id and args.operation not in ('verify', 'start'):
        parser.error('release identity is only valid for verify/start')
    try:
        if args.operation == 'build': result = build()
        elif args.operation == 'verify':
            release(args.release_id or '');result = {'status': 'VERIFIED', 'release_id': args.release_id}
        elif not args.role: parser.error('role is required')
        elif args.operation == 'status': result = status(args.role)
        elif args.operation == 'start': result = start(args.role, args.release_id or '', args.health_stream,
                                                     args.model_store, args.model_release_ref, epoch=args.stream_epoch,
                                                     source_profile=args.source_profile)
        elif args.operation == 'stop': result = stop(args.role)
        else: result = restart(args.role)
        print(json.dumps(result, sort_keys=True))
    except HostGuard as exc:
        print(json.dumps({'status': 'BLOCKED', 'operation': args.operation, 'reason': str(exc)}))
        raise SystemExit(2) from None
    except Exception as exc:
        print(json.dumps({'status': 'FAILED', 'operation': args.operation, 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == '__main__': main()
