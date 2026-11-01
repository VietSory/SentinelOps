"""Resume existing local Payment actions; never create or dispatch a rollback.

Requires an explicitly migrated, dedicated action writer, selected original
health ledger/store, exact target UIDs and a limited local Kubernetes credential.
There is intentionally no enable/force/provider/approval or arbitrary-image flag.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import signal
from urllib.parse import urlsplit

import asyncpg

from sentinelops.remediation.domain import Policy, Target
from sentinelops.remediation.executor import LedgerRecovery, RollbackExecutor
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.kubernetes import LocalKubernetes, local_server
from sentinelops.remediation.worker import ReconciliationWorker
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream


def validate_configuration(args, dsn):
    selected = urlsplit(dsn)
    if (selected.scheme not in ('postgres', 'postgresql') or selected.hostname != '127.0.0.1'
        or not selected.port or not selected.username or not selected.password
        or selected.path != '/sentinelops_control' or selected.query or selected.fragment):
        raise ValueError('an explicit loopback action runtime database is required')
    if not re.fullmatch(r'[0-9a-f]{64}', args.health_stream):
        raise ValueError('a selected health stream identity is required')
    if not args.source_store.is_dir():
        raise ValueError('original health source store must already exist')
    local_server(args.server)
    return Target(**json.loads(args.target.read_text()))


async def run(args):
    dsn = os.getenv('SENTINELOPS_ACTION_DATABASE_URL', '')
    target = validate_configuration(args, dsn)
    health_policy = json.loads(args.health_policy.read_text())
    rca_policy = json.loads(args.rca_policy.read_text())
    pool = kube = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=0, max_size=4, command_timeout=5, timeout=5)
        journal = ActionJournal(pool)
        await journal.check_ready()
        async with pool.acquire() as conn:
            stream = await conn.fetchrow('SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1', args.health_stream)
        if stream is None:
            raise ValueError('selected accepted health stream does not exist')
        identity = validate_stream(stream, health_policy)
        if (identity['environment'] != 'local'
            or identity['artifact_root_hash'] != stable_id({'root': str(args.source_store.resolve())})):
            raise ValueError('selected source environment/root binding differs')
        source = HealthLedgerReader(pool, ArtifactStore(args.source_store), args.health_stream, health_policy)
        kube = LocalKubernetes(args.server, target, token_file=args.token_file, ca_file=args.ca_file)
        executor = RollbackExecutor(journal, kube, None, None, LedgerRecovery(source, target),
                                    Policy(target), rca_policy)
        worker = ReconciliationWorker(journal, executor)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        print(json.dumps({'status': 'READY', 'mode': 'reconcile-only', 'new_dispatch_enabled': False}), flush=True)
        print(json.dumps(await worker.run(stop, poll_seconds=args.poll_seconds, max_ticks=args.max_ticks)), flush=True)
    finally:
        try:
            if kube is not None:
                await kube.close()
        finally:
            if pool is not None:
                await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--health-stream', required=True)
    parser.add_argument('--source-store', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True, help='Server-owned JSON object containing exact Target fields')
    parser.add_argument('--server', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--ca-file', type=Path, required=True)
    parser.add_argument('--health-policy', type=Path, default=Path('configs/slo_health_policy.json'))
    parser.add_argument('--rca-policy', type=Path, default=Path('configs/rca_policy.json'))
    parser.add_argument('--poll-seconds', type=float, default=5.)
    parser.add_argument('--max-ticks', type=int)
    args = parser.parse_args()
    if not .25 <= args.poll_seconds <= 10 or args.max_ticks is not None and not 1 <= args.max_ticks <= 100000:
        parser.error('invalid poll/tick bounds')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    try:
        asyncio.run(run(args))
    except Exception as exc:
        logging.getLogger('sentinelops-remediation-worker').error(json.dumps({
            'event':'action_worker_failed','error_type':type(exc).__name__}))
        raise SystemExit('action reconciliation worker failed: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
