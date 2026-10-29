"""Run the local accepted-health → correlation/RCA → durable-IER consumer.

Requires explicit source identity, original source store and an owned Compose
project. Does not train/activate a model, read experiment truth or execute actions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
from urllib.parse import urlsplit

import asyncpg
import yaml

from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream
from sentinelops.workers.incident import IncidentWorker
from sentinelops.workers.incident_evidence import NativeIncidentEvidence
from sentinelops.workers.incident_journal import IncidentJournal


async def run(args):
    dsn = os.environ.get("CONTROL_DATABASE_URL", "")
    selected = urlsplit(dsn)
    if (selected.scheme not in ("postgres", "postgresql") or selected.hostname not in ("127.0.0.1", "localhost")
        or selected.username != "control_writer" or selected.path != "/sentinelops_control"
        or not selected.port or selected.query or selected.fragment):
        raise ValueError("continuous incident worker requires the explicit loopback control_writer database")
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=4, command_timeout=10)
    collector = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    try:
        health_policy = json.loads(args.health_policy.read_text())
        source = HealthLedgerReader(pool, ArtifactStore(args.source_store), args.health_stream, health_policy)
        collector = NativeIncidentEvidence(args.project, jaeger_endpoint=args.jaeger,
            prometheus_endpoint=args.prometheus, commerce_dsn=os.environ.get("COMMERCE_EVIDENCE_DATABASE_URL"))
        journal = IncidentJournal(pool, source, ArtifactStore(args.store), yaml.safe_load(args.topology.read_text()),
                                  json.loads(args.rca_policy.read_text()), collector.profile)
        if args.status:
            await journal.repository.check_ready()
            reference, state = await journal.checkpoint()
            print(json.dumps({"status": "DURABLE_STATUS", "consumer_id": journal.consumer_id,
                              "state_ref": reference, "last_bucket_end": state["last_bucket_end"] if state else None}), flush=True)
            return
        async with pool.acquire() as conn:
            stream = await conn.fetchrow("SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1", source.stream_id)
        if stream is None:
            raise ValueError("start the explicitly selected accepted health stream before its incident consumer")
        await collector.verify_source(validate_stream(stream, health_policy))
        await journal.initialize()
        worker = IncidentWorker(journal, collector, connect=lambda: asyncpg.connect(dsn, timeout=5., command_timeout=10))
        print(json.dumps({"status": "READY", "consumer_id": journal.consumer_id,
                          "health_stream_id": source.stream_id, "project": args.project, "actions_allowed": False}), flush=True)
        ticks = 0
        while not stop.is_set() and (args.max_ticks is None or ticks < args.max_ticks):
            try:
                result = await worker.tick()
                if result["status"] != "WAIT":
                    print(json.dumps(result), flush=True)
            except (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, ConnectionError, TimeoutError) as exc:
                print(json.dumps({"status": "DEPENDENCY_UNAVAILABLE", "error_type": type(exc).__name__}), flush=True)
            ticks += 1
            if args.max_ticks is None or ticks < args.max_ticks:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=args.poll_seconds)
                except TimeoutError:
                    pass
        print(json.dumps({"status": "STOPPED", "consumer_id": journal.consumer_id, "ticks_by_process": ticks}), flush=True)
    finally:
        if collector is not None:
            await collector.close()
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-stream", required=True)
    parser.add_argument("--source-store", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--health-policy", type=Path, default=Path("configs/slo_health_policy.json"))
    parser.add_argument("--rca-policy", type=Path, default=Path("configs/rca_policy.json"))
    parser.add_argument("--topology", type=Path, default=Path("configs/topology.yaml"))
    parser.add_argument("--prometheus", default="http://127.0.0.1:9090")
    parser.add_argument("--jaeger", default="http://127.0.0.1:16686")
    parser.add_argument("--poll-seconds", type=float, default=1.)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--status", action="store_true", help="Read the matching checkpoint without initialization/capture/publication")
    args = parser.parse_args()
    if not .25 <= args.poll_seconds <= 5 or args.max_ticks is not None and not 1 <= args.max_ticks <= 100000:
        parser.error("poll interval or tick bound is outside supported limits")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        raise SystemExit("incident worker failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
