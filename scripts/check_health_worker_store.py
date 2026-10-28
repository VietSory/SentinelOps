"""Real isolated PostgreSQL worker acceptance; fixture timing, never live-health evidence."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse

import asyncpg

from sentinelops.incidents.repository import IncidentRepository
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker, latest_bucket


def docker(*args, data=None):
    return subprocess.check_output(["docker", *args], input=data, stderr=subprocess.PIPE, timeout=60)


def sql(container, statement: bytes):
    return docker("exec", "-i", container, "psql", "-v", "ON_ERROR_STOP=1", "-U", "control_bootstrap",
                  "-d", "sentinelops_control", data=statement)


async def check(args):
    if args.output.exists():
        raise ValueError("exclusive worker acceptance output already exists")
    parsed = urlparse(os.environ.get("CONTROL_DATABASE_URL", ""))
    if (parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username != "control_writer"
        or parsed.path != "/sentinelops_control"):
        raise ValueError("worker acceptance requires the dedicated local control_writer database")
    inspected = json.loads(docker("inspect", args.container))[0]
    labels = inspected["Config"]["Labels"]
    ports = inspected["NetworkSettings"]["Ports"].get("5432/tcp") or []
    if (labels.get("com.docker.compose.project") != args.project or labels.get("com.docker.compose.service") != "control-postgres"
        or not any(p["HostIp"] == "127.0.0.1" and int(p["HostPort"]) == parsed.port for p in ports)):
        raise ValueError("acceptance database/container/project/loopback port does not match")
    pool = await asyncpg.create_pool(os.environ["CONTROL_DATABASE_URL"], min_size=1, max_size=12, command_timeout=10)
    checks = []
    try:
        repository = IncidentRepository(pool)
        await repository.check_ready()
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT max(version) FROM sentinel_control.schema_migrations") == 1
            assert await conn.fetchval("SELECT count(*) FROM sentinel_control.incidents") == 0
        store = ArtifactStore(args.output.parent / "objects")
        policy = json.loads(Path("configs/slo_health_policy.json").read_text())
        worker = HealthWorker(pool, store, policy, endpoint="http://127.0.0.1:9090", environment="fixture-atomic",
                              configuration_hash="a" * 64)
        try:
            await worker.check_ready()
            raise AssertionError("worker accepted schema 1 before explicit migration")
        except ValueError as exc:
            assert "migration 2" in str(exc)
        checks.append("v1 incident compatibility and worker migration refusal")
        migration = Path("infra/control/003-health-worker.sql").read_bytes()
        sql(args.container, migration)
        sql(args.container, migration)
        await repository.check_ready()
        await worker.check_ready()
        checks.append("explicit v1-to-v2 upgrade, idempotent migration and v2 readiness")
        async with pool.acquire() as conn:
            for statement in ("UPDATE sentinel_control.health_signals SET payload=payload",
                              "DELETE FROM sentinel_control.health_batches",
                              "UPDATE sentinel_control.health_streams SET identity=identity"):
                try:
                    await conn.execute(statement)
                    raise AssertionError("runtime mutated protected health history/identity")
                except asyncpg.InsufficientPrivilegeError:
                    pass
            # Enough old buckets that all concurrency attempts stay in deadline-missed
            # mode; this storage-only fixture must never query an unrelated local source.
            initial = latest_bucket(datetime.now(timezone.utc)) - timedelta(minutes=20)
            await conn.execute("""INSERT INTO sentinel_control.health_streams(stream_id,identity,initial_bucket_end)
                VALUES($1,$2::jsonb,$3)""", worker.stream_id, json.dumps(worker.identity), initial)
        checks.append("runtime history and source identity are immutable")
        # Fail on the third service after the batch and two signal rows were inserted.
        sql(args.container, b"ALTER TABLE sentinel_control.health_signals ADD CONSTRAINT acceptance_third_signal_failure CHECK (service <> 'payment-service' OR payload->>'environment' <> 'fixture-atomic') NOT VALID;")
        try:
            await worker.tick()
            raise AssertionError("injected third-signal write failure was ignored")
        except asyncpg.CheckViolationError:
            pass
        rolled_back = await worker.status()
        assert rolled_back["batches"] == rolled_back["signals"] == 0 and rolled_back["last_bucket_end"] is None
        sql(args.container, b"ALTER TABLE sentinel_control.health_signals DROP CONSTRAINT acceptance_third_signal_failure;")
        first = await worker.tick()
        assert first["status"] == "COMMITTED"
        assert (await worker.status())["signals"] == 3
        checks.append("third-signal failure rolls back entire batch, signals and checkpoint; retry commits once")
        async with pool.acquire() as holder:
            assert await holder.fetchval("SELECT pg_try_advisory_lock($1)", worker.lock_id)
            assert (await worker.tick())["status"] == "BUSY"
            assert await holder.fetchval("SELECT pg_advisory_unlock($1)", worker.lock_id)
        checks.append("another session cannot process a locked source stream")
        abandoned = await asyncpg.connect(os.environ["CONTROL_DATABASE_URL"])
        assert await abandoned.fetchval("SELECT pg_try_advisory_lock($1)", worker.lock_id)
        await abandoned.close()
        assert (await worker.tick())["status"] == "COMMITTED"
        checks.append("disconnected worker session releases lock and resumes persisted checkpoint")
        concurrent = await asyncio.gather(*(worker.tick() for _ in range(8)))
        assert all(result["status"] in ("COMMITTED", "WAIT", "BUSY") for result in concurrent)
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT bucket_end,count(*) AS n FROM sentinel_control.health_signals WHERE stream_id=$1 GROUP BY bucket_end ORDER BY bucket_end", worker.stream_id)
            assert rows and all(row["n"] == 3 for row in rows)
            assert all(rows[i + 1]["bucket_end"] - rows[i]["bucket_end"] == timedelta(seconds=60) for i in range(len(rows) - 1))
            payloads = await conn.fetch("SELECT payload FROM sentinel_control.health_signals WHERE stream_id=$1", worker.stream_id)
            assert all(json.loads(row["payload"])["availability"] == "UNKNOWN" for row in payloads)
            assert all(json.loads(row["payload"])["health"] is None for row in payloads)
        checks.append("eight concurrent attempts keep one three-service decision per ordered bucket; missing stays UNKNOWN")
        before = await worker.status()
        docker("stop", args.container)
        try:
            await worker.tick()
            raise AssertionError("worker wrote successfully during database outage")
        except (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, OSError):
            pass
        docker("start", args.container)
        for _ in range(90):
            try:
                await worker.check_ready()
                break
            except (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, OSError):
                await asyncio.sleep(.5)
        else:
            raise AssertionError("exclusive database did not become ready after restart")
        assert await worker.status() == before
        checks.append("real database stop rejects writes and restart preserves exact checkpoint/history")
        report = {"status": "PASS", "fixture_only": True, "live_health_verified": False,
                  "database_project": args.project, "container": args.container,
                  "stream_id": worker.stream_id, "durable_status": before, "concurrent_results": concurrent,
                  "checks": checks, "actions_allowed": False, "model_outputs_available": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print("HEALTH WORKER STORAGE ACCEPTANCE: PASS " + str(args.output), flush=True)
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(check(args))
    except Exception as exc:
        raise SystemExit("health worker storage acceptance failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
