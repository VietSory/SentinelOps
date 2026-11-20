"""Observe a dedicated OOM demo and execute only its on-call-approved RAM step."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
from urllib.parse import urlsplit

import asyncpg
import httpx

from sentinelops.incidents.repository import IncidentRepository
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.pod_memory import KubernetesMemory, MemoryController, MemoryTarget, ObservationPending
from sentinelops.telemetry.store import ArtifactStore


async def run(args):
    dsn = os.environ.get('SENTINELOPS_DATABASE_URL', '')
    parsed = urlsplit(dsn)
    if (parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname
        or parsed.path != '/sentinelops_control'
        or parsed.hostname != '127.0.0.1' and parsed.query != 'sslmode=verify-full'):
        raise ValueError('explicit control runtime database with remote verified TLS is required')
    target = MemoryTarget(**json.loads(args.target.read_text()))
    pool = await asyncpg.create_pool(dsn, min_size=0, max_size=2, command_timeout=5, timeout=5)
    kube = KubernetesMemory(target, server=args.server, token_file=args.token_file, ca_file=args.ca_file)
    try:
        incidents = IncidentRepository(pool)
        await incidents.check_ready()
        controller = MemoryController(target=target, kube=kube, store=ArtifactStore(args.store),
            incidents=incidents, kill_switch=args.kill_switch, enabled=args.policy_enabled)
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        print(json.dumps({'status': 'READY', 'runbook': 'pod-memory-limit-demo/v1',
                          'policy_enabled': args.policy_enabled}), flush=True)
        while not stop.is_set():
            try:
                result = await controller.tick()
                print(json.dumps(result), flush=True)
            except (Blocked, ObservationPending) as exc:
                print(json.dumps({'phase': 'observing', 'reason': str(exc)}), flush=True)
            except (httpx.HTTPError, asyncpg.PostgresError, OSError, ValueError, KeyError, TypeError) as exc:
                print(json.dumps({'phase': 'unavailable', 'error_type': type(exc).__name__}), flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
    finally:
        await kube.close()
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('target', 'store', 'token-file', 'ca-file', 'kill-switch'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--policy-enabled', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as exc:
        raise SystemExit('pod memory demo stopped: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
