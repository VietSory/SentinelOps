"""Isolated, stdlib-only entry point copied into a host-bound release.

Run with -I -S: no editable-install .pth, checkout, PYTHONPATH or user site.
Checksums detect changed trusted bytes; they do not authenticate a hostile owner.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import sys


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): result.update(block)
    return result.hexdigest()


def inventory(root):
    if not root.is_dir(): raise ValueError('runtime directory unavailable')
    result = {}
    for path in sorted(root.rglob('*')):
        if '__pycache__' in path.parts: continue
        if path.is_symlink():
            if not path.is_file(): raise ValueError('directory/dangling runtime symlink unsupported')
            result[path.relative_to(root).as_posix()] = {'link': os.readlink(path), 'sha256': digest(path)}
        elif path.is_file(): result[path.relative_to(root).as_posix()] = digest(path)
    return result


def verify(root):
    if (root / 'no-bytecode').exists() or any('__pycache__' in p.parts or p.suffix == '.pyc' for p in root.rglob('*')):
        raise ValueError('release bytecode cache is not allowed')
    record = json.loads((root / 'release.json').read_text())
    if (record['release_version'] != 'lane-b-host/v1' or record['root'] != str(root.resolve())
        or record['interpreter'] != str(Path(sys.executable).resolve())
        or digest(Path(sys.executable).resolve()) != record['interpreter_sha256']
        or record['python_version'] != sys.version
        or any(record[k] is not False for k in ('model_activation_allowed', 'remediation_approval', 'portable'))
        or set(record['runtime_environment']) - {'PATH', 'LD_LIBRARY_PATH', 'SSL_CERT_FILE'}):
        raise ValueError('host release interpreter identity changed')
    actual = inventory(root)
    actual.pop('release.json', None)
    if actual != record['files']:
        raise ValueError('host release files changed')
    if inventory(Path(record['site_packages'])) != record['runtime_files']:
        raise ValueError('host release installed dependencies changed')
    return record


def private_json(path):
    meta = path.lstat()
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077):
        raise ValueError('private owned session file required')
    return json.loads(path.read_text())


def health_identity(root, session):
    """Use existing producer/source validation; no DB write or new checkpoint."""
    import asyncio
    from sentinelops.telemetry.prometheus import PrometheusSource
    from sentinelops.telemetry.store import ArtifactStore
    from sentinelops.workers.health import HealthWorker
    from sentinelops.workers.incident_evidence import NativeIncidentEvidence
    endpoint = 'http://127.0.0.1:19090'
    source = PrometheusSource(endpoint)
    try: configuration = source.verify_configuration()
    finally: source.close()
    worker = HealthWorker(None, ArtifactStore(session['health_store']),
        json.loads((root / 'configs/slo_health_policy.json').read_text()), endpoint=endpoint,
        environment='local', configuration_hash=configuration['configuration_hash'])
    async def check():
        collector = NativeIncidentEvidence('sentinelops-b', jaeger_endpoint='http://127.0.0.1:26686',
            prometheus_endpoint=endpoint, commerce_dsn=os.environ.get('COMMERCE_EVIDENCE_DATABASE_URL'))
        try: await collector.verify_source(worker.identity)
        finally: await collector.close()
    asyncio.run(check())
    return {'health_stream_id': worker.stream_id, 'prometheus_configuration_hash': configuration['configuration_hash']}


def main():
    root = Path(__file__).resolve().parent
    try:
        record = verify(root)
        if sys.argv[1:] == ['verify']:
            print(json.dumps({'status': 'VERIFIED', 'release_id': record['release_id']}));return
        if len(sys.argv) != 4 or sys.argv[1] not in ('api', 'health', 'incidents', 'probe'):
            raise ValueError('unsupported host process')
        role, session_path, expected_hash = sys.argv[1:]
        session = private_json(Path(session_path))
        if (digest(Path(session_path)) != expected_hash or session['release_manifest_sha256'] != digest(root / 'release.json')
            or (role != 'probe' and session['role'] != role) or session['release_id'] != record['release_id']):
            raise ValueError('session release/role mismatch')
        allowed = {'CONTROL_DATABASE_URL', 'COMMERCE_EVIDENCE_DATABASE_URL', 'SENTINELOPS_API_TOKEN',
                   'SENTINELOPS_ENV', 'SENTINELOPS_ENABLE_DEMO', 'SENTINELOPS_MODEL_STORE', 'SENTINELOPS_MODEL_RELEASE_REF',
                   'SENTINELOPS_ACTION_READ_DATABASE_URL'}
        if set(session['environment']) - allowed:
            raise ValueError('unexpected runtime environment')
        # Manager-provided loader/PATH values are pinned separately in the unit.
        for key in tuple(os.environ):
            if key.startswith(('SENTINELOPS_', 'COMPOSE_')) or key in ('CONTROL_DATABASE_URL', 'DATABASE_URL', 'COMMERCE_EVIDENCE_DATABASE_URL'):
                os.environ.pop(key)
        os.environ.update(session['environment'])
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        sys.dont_write_bytecode = True
        sys.pycache_prefix = str(root / 'no-bytecode')
        sys.path[:0] = [str(root / 'site'), str(root / 'cli'), record['site_packages']]
        import sentinelops
        if not Path(sentinelops.__file__).resolve().is_relative_to(root / 'site'):
            raise ValueError('application resolved outside release')
        os.chdir(root)
        if role == 'probe':
            print(json.dumps(health_identity(root, session), sort_keys=True));return
        if role != 'api' and health_identity(root, session) != session['source_identity']:
            raise ValueError('selected health source identity changed')
        if role == 'api':
            import uvicorn
            uvicorn.run('sentinelops.main:app', host='127.0.0.1', port=18000)
        else:
            sys.argv = [role, *session['arguments']]
            runpy.run_module('scripts.run_' + ('health' if role == 'health' else 'incident') + '_worker', run_name='__main__')
    except Exception as exc:
        # Never print private DSNs/token values or arbitrary exception messages.
        raise SystemExit('host runtime failed: ' + type(exc).__name__) from None


if __name__ == '__main__': main()
