"""Early-warning risk worker: score every accepted health bucket with the deployed release.

Reads the selected health stream (control DB rows + health store artifacts) and only
exports metrics. It writes nothing to the control database or the health store, and a
warning never opens an incident or authorizes an action (DEMO_UPGRADE_SPEC WP2).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import re
import signal

import asyncpg

from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.remediation.pod_memory_metrics import metrics_endpoint  # local or in-cluster Collector only
from sentinelops.workers.risk import EpisodeTracker, RiskMetrics, RiskScorer, RiskWorker, release_thresholds
from sentinelops.workers.source_policy import SourcePolicy, source_policy_arguments


async def run(args):
    dsn = os.environ.get('CONTROL_DATABASE_URL', '')
    options = SourcePolicy.from_args(args).database_options(dsn, worker='incident')
    if not re.fullmatch(r'[0-9a-f]{64}', args.health_stream):
        raise ValueError('a selected health stream identity is required')
    if args.scratch_store.resolve() == args.source_store.resolve():
        raise ValueError('scratch store must be separate from the health store')
    registry = ModelRegistry.load(ArtifactStore(args.model_store), args.model_release_ref)
    thresholds = release_thresholds(registry)
    metrics = RiskMetrics.otlp(metrics_endpoint(args.metrics_otlp_endpoint), thresholds=thresholds)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, command_timeout=10, **options)
    try:
        scorer = RiskScorer(ArtifactStore(args.source_store), ArtifactStore(args.scratch_store), registry)
        worker = RiskWorker(pool, args.health_stream, scorer, EpisodeTracker(), metrics)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, stop.set)
        logging.getLogger('sentinelops-risk-worker').info('risk worker ready stream=%s release=%s',
                                                          args.health_stream, registry.identity)
        await worker.run(stop, poll_seconds=args.poll_seconds)
    finally:
        await pool.close()
        metrics.provider.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source_policy_arguments(parser)
    parser.add_argument('--health-stream', required=True)
    parser.add_argument('--source-store', type=Path, required=True, help='Health worker artifact store (read-only)')
    parser.add_argument('--scratch-store', type=Path, required=True, help='Separate writable store for derived artifacts')
    parser.add_argument('--model-store', type=Path, required=True)
    parser.add_argument('--model-release-ref', required=True)
    parser.add_argument('--metrics-otlp-endpoint', required=True)
    parser.add_argument('--poll-seconds', type=float, default=5.)
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 30:
        parser.error('poll interval must be between 1 and 30 seconds')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    try:
        asyncio.run(run(args))
    except Exception as exc:
        raise SystemExit('risk worker failed: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
