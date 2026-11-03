"""Actual B systemd/API/PostgreSQL lifecycle acceptance; no model/action claims.

Requires the owned B stack already up and no managed role already installed.
Uses existing stores/checkpoints, never replaces a source or writes an IER fixture.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import asyncpg
import httpx

from scripts import lane_b, lane_b_host as host
from scripts.host_runtime import digest
from sentinelops.telemetry.store import ArtifactStore


def wait(check, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = check()
        if result: return result
        time.sleep(.5)
    raise RuntimeError('host lifecycle observation timed out')


def snapshot(dsn, stream):
    async def read():
        conn = await asyncpg.connect(dsn)
        try:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                health = await conn.fetchrow('SELECT last_bucket_end,last_batch_ref FROM sentinel_control.health_streams WHERE stream_id=$1', stream)
                counts = await conn.fetchrow('''SELECT count(*) AS batches,count(DISTINCT bucket_end) AS unique_buckets,
                    (SELECT count(*) FROM sentinel_control.health_signals WHERE stream_id=$1) AS signals
                    FROM sentinel_control.health_batches WHERE stream_id=$1''', stream)
                consumer = await conn.fetchrow('''SELECT c.consumer_id,p.last_state_ref FROM sentinel_control.incident_consumers c
                    JOIN sentinel_control.incident_consumer_checkpoints p USING(consumer_id) WHERE c.health_stream_id=$1''', stream)
            return {'health': dict(health) if health else None, 'counts': dict(counts),
                    'consumer': dict(consumer) if consumer else None}
        finally: await conn.close()
    return asyncio.run(read())


def ready(role, pid):
    log = subprocess.run(['journalctl', '--user', '--unit=' + host.unit_name(role),
        '--no-pager', '--output=cat', '_PID=' + str(pid)], capture_output=True, text=True, check=True)
    for line in log.stdout.splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        if isinstance(event, dict) and event.get('status') == 'READY': return event
    return None


def exercise(identifier, output):
    if output.exists(): raise ValueError('acceptance output must be new')
    if not output.resolve().is_relative_to(lane_b.ROOT.resolve()): raise ValueError('owned B output required')
    capture = subprocess.run(['pgrep', '-f', '[p]ython.*capture_local_(campaign|corpus)'], stdout=subprocess.DEVNULL)
    if capture.returncode != 1: raise RuntimeError('active/unknown A capture prevents workload')
    lane_b.require_owned_containers()
    for role in host.ROLES:
        if host.state(role)['state'] != 'NOT_INSTALLED':
            raise ValueError('acceptance never adopts/stops an existing managed role')
    root = host.release(identifier)
    manifest_hash = digest(root / 'release.json')
    environment = lane_b.runtime_environment()
    headers = {'Authorization': 'Bearer ' + environment['SENTINELOPS_API_TOKEN']}
    started = []
    result = {'status': 'RUNNING', 'scope': 'actual-local-host-lifecycle', 'release_id': identifier,
              'release_manifest_sha256': manifest_hash, 'checks': [], 'model_activated': False,
              'H4_accepted': False, 'G6_accepted': False, 'training_evidence': False}
    def new_pid(role, old):
        value = host.state(role)
        return value if value.get('state') == 'active' and value.get('pid') not in (None, 0, old) else None
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            def api_live():
                try: return client.get('http://127.0.0.1:18000/health').status_code == 200
                except httpx.HTTPError: return False
            started.append('api');host.start('api', identifier);wait(api_live)
            initial = host.state('api')
            assert host.start('api', identifier)['pid'] == initial['pid']
            assert client.get('http://127.0.0.1:18000/v1/incidents').status_code == 401
            assert client.get('http://127.0.0.1:18000/ready').status_code == 503
            listed = client.get('http://127.0.0.1:18000/v1/incidents', headers=headers);listed.raise_for_status()
            pinned = []
            for item in listed.json()['items']:
                incident_id, revision = item['incident_id'], item['revision']
                response = client.get('http://127.0.0.1:18000/v1/incidents/' + incident_id,
                                      params={'revision': revision}, headers=headers)
                response.raise_for_status();pinned.append((incident_id, revision, hashlib.sha256(response.content).hexdigest()))
            result['checks'].append('actual installed-wheel API auth/store/model-unavailable; repeated start keeps same PID')
            # Corrupt only this newly selected release copy, never repository policy.
            target = root / 'configs/topology.yaml';original = target.read_bytes()
            try:
                target.write_bytes(original + b'\n# acceptance corruption\n')
                try:
                    host.restart('api');raise AssertionError('corrupt release restarted')
                except ValueError: pass
                assert host.state('api')['pid'] == initial['pid'] and api_live()
            finally: target.write_bytes(original)
            host.restart('api');wait(lambda: new_pid('api', initial['pid']));wait(api_live)
            result['checks'].append('checksum corruption refuses restart before stopping running API; restored release restarts')
            for incident_id, revision, old_hash in pinned:
                response = client.get('http://127.0.0.1:18000/v1/incidents/' + incident_id,
                                      params={'revision': revision}, headers=headers)
                response.raise_for_status();assert hashlib.sha256(response.content).hexdigest() == old_hash
            result['pinned_incident_revisions'] = len(pinned)
            started.append('health');host.start('health', identifier)
            stream = host.status('health')['health_stream_id'];result['health_stream_id'] = stream
            assert wait(lambda: ready('health', host.state('health')['pid']))['stream_id'] == stream
            def health_committed():
                value = snapshot(environment['CONTROL_DATABASE_URL'], stream)
                return value if value['health'] and value['health']['last_batch_ref'] else None
            before = wait(health_committed)
            started.append('incidents');host.start('incidents', identifier, stream)
            assert wait(lambda: ready('incidents', host.state('incidents')['pid']))['health_stream_id'] == stream
            def consumer_committed():
                value = snapshot(environment['CONTROL_DATABASE_URL'], stream)
                return value if value['consumer'] and value['consumer']['last_state_ref'] else None
            wait(consumer_committed)
            before = snapshot(environment['CONTROL_DATABASE_URL'], stream)
            old = host.state('health')
            host.systemctl('kill', '--signal=KILL', '--kill-whom=main', old['unit'])
            recovered = wait(lambda: new_pid('health', old['pid']))
            assert recovered['automatic_restarts'] >= 1
            assert wait(lambda: ready('health', recovered['pid']))['stream_id'] == stream
            def health_resumed():
                value = snapshot(environment['CONTROL_DATABASE_URL'], stream)
                return value if value['health']['last_bucket_end'] > before['health']['last_bucket_end'] else None
            after = wait(health_resumed, seconds=90)
            assert host.status('health')['health_stream_id'] == stream
            counts = after['counts'];assert counts['signals'] == 3 * counts['batches'] and counts['unique_buckets'] == counts['batches']
            ArtifactStore(lane_b.ROOT / 'health').read(before['health']['last_batch_ref'])
            result['checks'].append('actual SIGKILL triggers systemd restart; same source/root and durable checkpoint survive without duplicate buckets')
            old_consumer = host.state('incidents');host.restart('incidents')
            resumed = wait(lambda: new_pid('incidents', old_consumer['pid']))
            assert wait(lambda: ready('incidents', resumed['pid']))['consumer_id'] == before['consumer']['consumer_id']
            wait(consumer_committed)
            latest = snapshot(environment['CONTROL_DATABASE_URL'], stream)
            assert latest['consumer']['consumer_id'] == before['consumer']['consumer_id']
            ArtifactStore(lane_b.ROOT / 'incidents').read(before['consumer']['last_state_ref'])
            result['checks'].append('actual incident process restarts with same consumer and resolvable prior immutable checkpoint')
            result['checkpoint_before'] = {k: str(v) if isinstance(v, datetime) else v for k, v in before['health'].items()}
            result['checkpoint_after'] = {k: str(v) if isinstance(v, datetime) else v for k, v in latest['health'].items()}
            result['health_counts'] = latest['counts']
            result['consumer_id'] = latest['consumer']['consumer_id']
            result['loaded_observer_sha256'] = {p: digest(lane_b.PROJECT / p) for p in
                ('scripts/check_host_processes.py', 'scripts/lane_b_host.py', 'scripts/host_runtime.py')}
            result['status'] = 'PASS'
    except Exception as exc:
        result['status'] = 'FAIL';result['error_type'] = type(exc).__name__
        raise
    finally:
        errors = []
        for role in reversed(started):
            try: host.stop(role)
            except Exception as exc: errors.append(type(exc).__name__)
        result['stopped_owned_roles'] = started
        if errors: result['status'] = 'FAIL';result['cleanup_errors'] = errors
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x') as stream: json.dump(result, stream, indent=2)
        if errors: raise RuntimeError('owned unit cleanup failed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    exercise(args.release_id, args.output)
    print('HOST PROCESS LIFECYCLE ACCEPTANCE: PASS ' + str(args.output))


if __name__ == '__main__': main()
