"""Poll verified RCA_READY incidents; dispatch requires --policy-enabled.

Uses the dedicated action database role and original immutable health/incident
stores. It never initializes or writes the upstream incident consumer journal.
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
import yaml

from sentinelops.ml.registry import ModelRegistry
from sentinelops.remediation.authority import NativeH4Authority
from sentinelops.remediation.controller import AutoRunbookController
from sentinelops.remediation.domain import Policy, Target
from sentinelops.remediation.executor import LedgerRecovery, RollbackExecutor
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.kubernetes import LocalKubernetes, configured_server
from sentinelops.remediation.registry import KnownGoodRegistry
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, decoded, validate_stream
from sentinelops.workers.incident_journal import IncidentJournal


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--health-stream', required=True)
    for name in ('source-store', 'target', 'token-file', 'ca-file'):
        result.add_argument('--' + name, type=Path, required=True)
    result.add_argument('--incident-store', type=Path, default=Path('/artifacts/incidents'))
    result.add_argument('--known-good-store', type=Path, default=Path('/artifacts/remediation'))
    result.add_argument('--server', required=True)
    result.add_argument('--known-good-ref', default=os.getenv('SENTINELOPS_KNOWN_GOOD_LEDGER_REF'))
    result.add_argument('--approved-by', default=os.getenv('SENTINELOPS_KNOWN_GOOD_APPROVED_BY'))
    result.add_argument('--store', type=Path, default=Path('/artifacts/remediation'), help='Persistent single-writer decision artifact store')
    result.add_argument('--health-policy', type=Path, default=Path('configs/slo_health_policy.json'))
    result.add_argument('--rca-policy', type=Path, default=Path('configs/rca_policy.json'))
    result.add_argument('--topology', type=Path, default=Path('configs/topology.yaml'))
    result.add_argument('--catalog', type=Path, default=Path('configs/runbook_catalog.json'))
    result.add_argument('--kill-switch', type=Path, required=True, help='Existing file disables new dispatch')
    result.add_argument('--policy-enabled', action='store_true', default=False)
    result.add_argument('--model-store', type=Path)
    result.add_argument('--model-release-ref')
    result.add_argument('--poll-seconds', type=float, default=10.)
    result.add_argument('--max-ticks', type=int)
    return result


def validate_configuration(args, dsn):
    selected = urlsplit(dsn)
    remote = (os.getenv('SENTINELOPS_KUBERNETES_REMOTE_SERVER') == args.server
              and selected.hostname != '127.0.0.1' and selected.query == 'sslmode=verify-full')
    if (selected.scheme not in ('postgres', 'postgresql') or not selected.hostname
        or not selected.port or not selected.username or not selected.password
        or selected.path != '/sentinelops_control' or selected.fragment
        or (not remote and (selected.hostname != '127.0.0.1' or selected.query))):
        raise ValueError('an explicit action runtime database (remote requires verify-full TLS) is required')
    if not args.known_good_ref or not args.approved_by:
        raise ValueError('explicit known-good ledger reference and approver are required')
    if not re.fullmatch(r'[0-9a-f]{64}', args.health_stream):
        raise ValueError('a selected health stream identity is required')
    for path in (args.source_store, args.incident_store, args.known_good_store):
        if not path.is_dir():
            raise ValueError('original evidence stores must already exist')
    configured_server(args.server)
    return Target(**json.loads(args.target.read_text()))


async def run(args):
    from sentinelops.remediation.matching import load_catalog

    dsn = os.getenv('SENTINELOPS_ACTION_DATABASE_URL', '')
    target = validate_configuration(args, dsn)
    catalog = load_catalog(args.catalog)
    health_policy = json.loads(args.health_policy.read_text())
    rca_policy = json.loads(args.rca_policy.read_text())
    pool = kube = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=0, max_size=4, command_timeout=5, timeout=5)
        actions = ActionJournal(pool)
        await actions.check_ready()
        consumer_id = stable_id({'consumer_version': 'continuous-incident/v1', 'health_stream_id': args.health_stream})
        async with pool.acquire() as conn:
            stream = await conn.fetchrow('SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1', args.health_stream)
            identity = await conn.fetchval('SELECT identity FROM sentinel_control.incident_consumers WHERE consumer_id=$1', consumer_id)
        if stream is None or identity is None:
            raise ValueError('selected accepted health stream/incident consumer does not exist')
        health_identity = validate_stream(stream, health_policy)
        if health_identity['artifact_root_hash'] != stable_id({'root': str(args.source_store.resolve())}):
            raise ValueError('selected source root binding differs')
        environment = health_identity['environment']
        source = HealthLedgerReader(pool, ArtifactStore(args.source_store), args.health_stream, health_policy)
        registry = ModelRegistry.load(ArtifactStore(args.model_store), args.model_release_ref) if args.model_store else None
        evidence = IncidentJournal(pool, source, ArtifactStore(args.incident_store),
            yaml.safe_load(args.topology.read_text()), rca_policy, decoded(identity)['evidence_profile'], model_registry=registry)
        await evidence.checkpoint()  # Verify the exact existing consumer configuration; no initialize().
        good = KnownGoodRegistry(ArtifactStore(args.known_good_store), args.known_good_ref,
                                approved_by=args.approved_by, source=source, environment=environment)
        kube = LocalKubernetes(args.server, target, token_file=args.token_file, ca_file=args.ca_file)
        executor = RollbackExecutor(actions, kube, NativeH4Authority(evidence), good,
            LedgerRecovery(source, target, environment=environment), Policy(target, enabled=args.policy_enabled),
            rca_policy, enabled=lambda: not args.kill_switch.exists())
        controller = AutoRunbookController(incidents=evidence.repository, evidence=evidence, executor=executor,
            catalog=catalog, kill_switch=args.kill_switch, store=ArtifactStore(args.store))
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        print(json.dumps({'status': 'READY', 'mode': 'auto-runbook-controller',
                          'policy_enabled': executor.policy.enabled}), flush=True)
        ticks = 0
        while not stop.is_set() and (args.max_ticks is None or ticks < args.max_ticks):
            print(json.dumps(await controller.tick()), flush=True)
            ticks += 1
            if args.max_ticks is not None and ticks >= args.max_ticks:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.poll_seconds)
            except TimeoutError:
                pass
    finally:
        try:
            if kube is not None:
                await kube.close()
        finally:
            if pool is not None:
                await pool.close()


def main():
    cli = parser()
    args = cli.parse_args()
    if (not .25 <= args.poll_seconds <= 10
        or args.max_ticks is not None and not 1 <= args.max_ticks <= 100000
        or bool(args.model_store) != bool(args.model_release_ref)):
        cli.error('invalid poll/tick bounds or model configuration')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    try:
        asyncio.run(run(args))
    except Exception as exc:
        logging.getLogger('sentinelops-remediation-controller').error(json.dumps({
            'event': 'runbook_controller_failed', 'error_type': type(exc).__name__}))
        raise SystemExit('remediation controller failed: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
