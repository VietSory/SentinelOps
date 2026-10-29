"""Continuously consume an explicit accepted health stream in the local control DB."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
from urllib.parse import urlparse

import asyncpg
import yaml

from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.evidence import NativeEvidenceSource
from sentinelops.workers.incidents import IncidentWorker


async def run(args):
    dsn = os.environ.get("CONTROL_DATABASE_URL", "")
    parsed = urlparse(dsn)
    if (parsed.scheme not in ("postgres", "postgresql") or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username != "control_writer" or parsed.path != "/sentinelops_control"):
        raise ValueError("incident worker requires dedicated local control_writer database")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    pool = await asyncpg.create_pool(dsn, min_size=0, max_size=4, command_timeout=10, timeout=5)
    try:
        evidence = NativeEvidenceSource(args.native_project, directory=Path.cwd()) if args.native_project else None
        worker = IncidentWorker(pool, ArtifactStore(args.store), args.stream_id, yaml.safe_load(args.topology.read_text()),
            json.loads(args.health_policy.read_text()), json.loads(args.rca_policy.read_text()), evidence_source=evidence)
        initialized, committed, failures = False, 0, 0
        while not stop.is_set() and (args.max_buckets is None or committed < args.max_buckets):
            try:
                if not initialized:
                    await worker.check_ready()
                    initialized = True
                    print(json.dumps({"status": "READY", "consumer_id": worker.consumer_id}), flush=True)
                if args.status:
                    print(json.dumps(await worker.status()), flush=True)
                    return
                result = await worker.tick()
                failures = 0
                if result["status"] != "WAIT":
                    print(json.dumps(result), flush=True)
                committed += result["status"] == "COMMITTED"
            except (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, ConnectionError, TimeoutError) as exc:
                failures += 1
                print(json.dumps({"status": "DATABASE_UNAVAILABLE", "availability": "UNKNOWN", "error_type": type(exc).__name__}), flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(30, 2**min(failures, 5)) if failures else args.poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--stream-id", required=True)
    parser.add_argument("--topology", type=Path, default=Path("configs/topology.yaml"))
    parser.add_argument("--health-policy", type=Path, default=Path("configs/slo_health_policy.json"))
    parser.add_argument("--rca-policy", type=Path, default=Path("configs/rca_policy.json"))
    parser.add_argument("--native-project", help="Explicit Compose project for operational logs/traces")
    parser.add_argument("--poll-seconds", type=float, default=1.)
    parser.add_argument("--max-buckets", type=int)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if not .25 <= args.poll_seconds <= 5 or args.max_buckets is not None and not 1 <= args.max_buckets <= 100000:
        parser.error("invalid processing bounds")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
