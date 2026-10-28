"""Run a local continuous SLO producer; this never activates a model or an action."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
from urllib.parse import urlparse

import asyncpg
import httpx

from sentinelops.telemetry.prometheus import PrometheusSource
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker, local_endpoint


async def run(args):
    parsed = urlparse(os.environ.get("CONTROL_DATABASE_URL", ""))
    if (parsed.scheme not in ("postgres", "postgresql") or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username != "control_writer" or parsed.path != "/sentinelops_control"):
        raise ValueError("health worker requires the dedicated loopback control_writer database")
    endpoint = local_endpoint(args.prometheus)
    source = PrometheusSource(endpoint)
    source.client.timeout = httpx.Timeout(1.)
    try:
        configuration = await asyncio.to_thread(source.verify_configuration)
    finally:
        source.close()
    pool = await asyncpg.create_pool(os.environ["CONTROL_DATABASE_URL"], min_size=1, max_size=4, command_timeout=10)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    committed = 0
    try:
        worker = HealthWorker(pool, ArtifactStore(args.store), json.loads(args.policy.read_text()),
                              endpoint=endpoint, environment=args.environment,
                              configuration_hash=configuration["configuration_hash"])
        await worker.check_ready()
        if args.status:
            print(json.dumps(await worker.status()), flush=True)
            return
        print(json.dumps({"status": "READY", "stream_id": worker.stream_id, "identity": worker.identity}), flush=True)
        while not stop.is_set() and (args.max_buckets is None or committed < args.max_buckets):
            try:
                result = await worker.tick()
                if result["status"] in ("COMMITTED", "BUSY"):
                    print(json.dumps(result), flush=True)
                committed += result["status"] == "COMMITTED"
            except (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, ConnectionError, asyncio.TimeoutError) as exc:
                print(json.dumps({"status": "DATABASE_UNAVAILABLE", "error_type": type(exc).__name__}), flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.poll_seconds)
            except asyncio.TimeoutError:
                pass
        try:
            durable = await worker.status()
        except (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, OSError, asyncio.TimeoutError):
            durable = {"availability": "UNKNOWN", "reason": "database-unavailable-at-shutdown"}
        print(json.dumps({"status": "STOPPED", "committed_by_process": committed,
                          "durable_status": durable}), flush=True)
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("configs/slo_health_policy.json"))
    parser.add_argument("--environment", default="local")
    parser.add_argument("--prometheus", default="http://127.0.0.1:9090")
    parser.add_argument("--max-buckets", type=int)
    parser.add_argument("--status", action="store_true", help="Read the matching durable stream without processing")
    parser.add_argument("--poll-seconds", type=float, default=1.)
    args = parser.parse_args()
    if not .25 <= args.poll_seconds <= 5 or (args.max_buckets is not None and not 1 <= args.max_buckets <= 100000):
        parser.error("poll interval or bounded bucket count is outside supported limits")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        raise SystemExit("health worker failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
