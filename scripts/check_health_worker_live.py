"""Real bounded native worker acceptance; controls/truth stay outside the worker store."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx

from sentinelops.contracts import utc_time
from sentinelops.experiments.runner import ExperimentConfig, execute, image_identity
from sentinelops.incidents.health import advance_health
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp, stable_id
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import local_endpoint
from scripts.worker_metrics_evidence import WorkerMetricEvidence


def docker(*args):
    return subprocess.check_output(["docker", *args], stderr=subprocess.PIPE, timeout=60)


def target(container: str, project: str, service: str):
    info = json.loads(docker("inspect", container))[0]
    labels = info["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != service:
        raise ValueError("live acceptance Docker target/project/service differs")
    return info


def require_port(info: dict, internal: str, expected: int):
    ports = info["NetworkSettings"]["Ports"].get(internal) or []
    if not info["State"]["Running"] or not any(p["HostIp"] == "127.0.0.1" and int(p["HostPort"]) == expected for p in ports):
        raise ValueError("native acceptance endpoint is not owned by the selected running container")


async def ledger_snapshot(pool, stream_id: str) -> dict:
    async with pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        stream = await conn.fetchrow("SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1", stream_id)
        batches = await conn.fetch("SELECT * FROM sentinel_control.health_batches WHERE stream_id=$1 ORDER BY bucket_end", stream_id)
        signals = await conn.fetch("SELECT * FROM sentinel_control.health_signals WHERE stream_id=$1 ORDER BY bucket_end,service", stream_id)
    if stream is None:
        raise ValueError("health stream snapshot has no durable source")
    def encoded(row):
        result = {}
        for key, value in dict(row).items():
            if isinstance(value, datetime): value = timestamp(value)
            elif key in ("identity", "payload"): value = json.loads(value)
            result[key] = value
        return result
    return {"snapshot_version": "health-ledger-snapshot/v1", "stream": encoded(stream),
            "batches": [encoded(row) for row in batches], "signals": [encoded(row) for row in signals]}


async def verify(pool, store: ArtifactStore, stream_id: str, worker_logs: list[Path], interventions: list[dict]) -> dict:
    async with pool.acquire() as conn:
        batches = await conn.fetch("SELECT * FROM sentinel_control.health_batches WHERE stream_id=$1 ORDER BY bucket_end", stream_id)
        records = await conn.fetch("SELECT * FROM sentinel_control.health_signals WHERE stream_id=$1 ORDER BY bucket_end,service", stream_id)
    assert len(records) == len(batches) * 3 and len(batches) >= 12
    by_bucket = {}
    for record in records:
        by_bucket.setdefault(timestamp(record["bucket_end"]), {})[record["service"]] = record
    previous, previous_ref = {}, None
    events, availability, unknown_active = Counter(), Counter(), Counter()
    eligible_features = 0
    deadline_misses = 0
    source_unknown = Counter()
    source_stop = utc_time(next(item["at"] for item in interventions if item["kind"] == "prometheus-stopped"))
    source_start = utc_time(next(item["at"] for item in interventions if item["kind"] == "prometheus-start"))
    for index, row in enumerate(batches):
        if index:
            assert row["bucket_end"] - batches[index - 1]["bucket_end"] == timedelta(seconds=60)
        batch = store.read(row["manifest_ref"])
        assert batch["previous_batch_ref"] == previous_ref and batch["stream_id"] == stream_id
        assert batch["bucket_end"] == timestamp(row["bucket_end"]) and batch["evaluated_at"] == timestamp(row["evaluated_at"])
        assert row["recorded_at"] >= row["evaluated_at"]
        normalized = store.read(batch["normalization_manifest_ref"])
        raw = [store.read(ref) for ref in normalized["raw_refs"]]
        with TemporaryDirectory(prefix="sentinelops-worker-replay-") as directory:
            replayed = process_captures(raw, ArtifactStore(directory), environment=batch["identity"]["environment"])
        assert replayed["manifest_ref"] == batch["normalization_manifest_ref"]
        eligible_features += sum(item["window_end"] == batch["bucket_end"] and item["quality"]["eligible_for_inference"]
                                 for ref in normalized["feature_refs"] if (item := store.read(ref)))
        observations = {item["service"]: item for ref in normalized["observation_refs"]
                        if (item := store.read(ref))["bucket_end"] == batch["bucket_end"]}
        if batch["source_status"] == "missed-decision-deadline":
            assert not batch["current_raw_refs"]
            deadline_misses += 1
        services = by_bucket[batch["bucket_end"]]
        assert set(services) == set(BUSINESS_ROUTES)
        for service in sorted(BUSINESS_ROUTES):
            payload = json.loads(services[service]["payload"])
            computed = advance_health(previous.get(service), observations.get(service), service=service,
                environment=batch["identity"]["environment"], source_profile=batch["identity"]["source_profile"],
                bucket_end=row["bucket_end"], evaluated_at=row["evaluated_at"],
                policy=json.loads(Path("configs/slo_health_policy.json").read_text()))
            assert payload == computed == store.read(services[service]["signal_ref"])
            assert services[service]["signal_ref"] in batch["signal_refs"]
            assert payload["model_outputs_available"] is False
            events[service + "/" + payload["event"]] += 1
            availability[service + "/" + payload["availability"]] += 1
            if payload["availability"] == "UNKNOWN":
                assert payload["health"] is None
                unknown_active[service] += bool(payload["incident_active"])
                if source_stop <= row["evaluated_at"] <= source_start:
                    source_unknown[service] += 1
            previous[service] = payload
        previous_ref = row["manifest_ref"]
    for service in ("checkout-service", "payment-service"):
        assert events[service + "/incident_opened"] == 1 and events[service + "/recovery_confirmed"] == 1
        assert unknown_active[service] >= 1 and not previous[service]["incident_active"]
    assert not events["inventory-service/incident_opened"]
    assert deadline_misses >= 1 and eligible_features >= 1
    assert set(source_unknown) == set(BUSINESS_ROUTES) and all(n >= 1 for n in source_unknown.values())
    commits = []
    for log in worker_logs:
        for line in log.read_text().splitlines():
            if line.startswith("{"):
                item = json.loads(line)
                if item.get("status") == "COMMITTED": commits.append(item)
    assert len(commits) == len(batches) and {item["manifest_ref"] for item in commits} == {row["manifest_ref"] for row in batches}
    assert commits and max(item["processing_ms"] for item in commits) <= 10000
    return {"batches": len(batches), "signals": len(records), "event_counts": dict(events),
            "availability_counts": dict(availability), "unknown_active_counts": dict(unknown_active),
            "missed_deadline_buckets": deadline_misses, "eligible_feature_outputs": eligible_features,
            "source_outage_unknown_counts": dict(source_unknown),
            "timed_batches": len(commits), "timing_coverage": "all-accepted-batches",
            "maximum_processing_ms": max(item["processing_ms"] for item in commits),
            "raw_to_feature_and_health_replay": True, "final_batch_ref": batches[-1]["manifest_ref"]}


async def check(args):
    root = Path.cwd()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ValueError("freeze committed worker/protocol before native acceptance")
    protocol_path = root / "configs/health_worker_acceptance.json"
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    if protocol["protocol_version"] != "continuous-health-acceptance/v1" or protocol["actions_allowed"] is not False:
        raise ValueError("unsupported native worker protocol")
    parsed = urlparse(os.environ.get("CONTROL_DATABASE_URL", ""))
    if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username != "control_writer" or parsed.path != "/sentinelops_control":
        raise ValueError("native acceptance requires the local control_writer sandbox")
    control = target(args.control_container, args.control_project, "control-postgres")
    require_port(control, "5432/tcp", parsed.port)
    if os.environ.get("COMPOSE_PROJECT_NAME") != args.native_project:
        raise ValueError("native workload project differs from the explicit acceptance scope")
    require_port(target(args.prometheus_container, args.native_project, "prometheus"), "9090/tcp", 9090)
    metrics_endpoint = getattr(args, "metrics_otlp_endpoint", None)
    if metrics_endpoint:
        if local_endpoint(metrics_endpoint) != "http://127.0.0.1:4317":
            raise ValueError("native acceptance metrics must target the owned loopback collector")
        collector = docker("compose", "ps", "-q", "otel-collector").decode().strip()
        require_port(target(collector, args.native_project, "otel-collector"), "4317/tcp", 4317)
    for service, port in (("checkout-service", 8001), ("payment-service", 8002), ("inventory-service", 8003)):
        container = docker("compose", "ps", "-q", service).decode().strip()
        require_port(target(container, args.native_project, service), "8000/tcp", port)
    images = image_identity(root)
    source_paths = [*root.glob("src/sentinelops/**/*.py"), *root.glob("contracts/*.schema.json"),
                    root / "scripts/run_health_worker.py", Path(__file__).resolve(),
                    root / "scripts/worker_metrics_evidence.py",
                    root / "infra/control/003-health-worker.sql", root / "configs/slo_health_policy.json"]
    source_hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    args.output.mkdir(parents=True, exist_ok=False)
    metrics = WorkerMetricEvidence(args.output / "own-metrics") if metrics_endpoint else None
    store = ArtifactStore(args.output / "worker")
    children, logs, streams, interventions = [], [], [], []
    pool = await asyncpg.create_pool(os.environ["CONTROL_DATABASE_URL"], min_size=1, max_size=4, command_timeout=10)
    workload = None
    db_task = None
    db_down, source_down = False, False
    stream_id = None
    extra = None
    phase = "startup"
    started = time.monotonic()

    def save_running():
        (args.output / "running.json").write_text(json.dumps({"source_commit": commit,
            "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
            "child_pids": [p.pid for p in children], "interventions": interventions,
            "stream_id": stream_id, "native_project": args.native_project, "control_project": args.control_project}, indent=2) + "\n")

    def spawn():
        path = args.output / ("worker-" + str(len(children)) + ".log")
        stream = path.open("x")
        command = [sys.executable, "-m", "scripts.run_health_worker", "--store", str(store.root), "--max-buckets", "100"]
        if metrics_endpoint:
            command += ["--metrics-otlp-endpoint", metrics_endpoint,
                        "--metrics-instance", "health-0" if len(children) < 2 else "health-1"]
        process = subprocess.Popen(command,
            stdout=stream, stderr=subprocess.STDOUT, env=os.environ.copy())
        children.append(process); logs.append(path); streams.append(stream); save_running()
        return process

    async def stop_child(process, *, crash=False):
        if process.poll() is None:
            process.kill() if crash else process.terminate()
        return await asyncio.to_thread(process.wait, timeout=20)

    async def database_outage():
        nonlocal db_down
        db_down = True
        interventions.append({"kind": "database-stop", "at": timestamp(datetime.now(timezone.utc))}); save_running()
        await asyncio.to_thread(docker, "stop", args.control_container)
        stopped = time.monotonic()
        if metrics:
            await metrics.ready("database-unavailable", instances={"health-0", "health-1"}, value=0)
        await asyncio.sleep(max(0, protocol["database_outage_seconds"] - (time.monotonic() - stopped)))
        await asyncio.to_thread(docker, "start", args.control_container)
        for _ in range(90):
            try:
                async with pool.acquire() as conn: await conn.fetchval("SELECT 1")
                break
            except (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, OSError): await asyncio.sleep(.5)
        else: raise AssertionError("control database did not recover")
        db_down = False
        interventions.append({"kind": "database-start", "at": timestamp(datetime.now(timezone.utc))}); save_running()
        if metrics:
            await metrics.ready("database-recovered", instances={"health-0", "health-1"}, value=1)

    try:
        primary = spawn()
        for _ in range(60):
            for line in logs[0].read_text().splitlines():
                if line.startswith('{') and (item := json.loads(line)).get("status") == "READY": stream_id = item["stream_id"]
            if stream_id: break
            if primary.poll() is not None: raise AssertionError("native worker failed startup")
            await asyncio.sleep(.5)
        assert stream_id
        save_running()
        if metrics:
            await metrics.ready("primary-ready", instances={"health-0"}, value=1)
        phase = "native-workload-and-database-outage"
        workload = asyncio.create_task(execute(ExperimentConfig(**protocol["experiment"]), project=root,
            output=args.output / "experiments", session_id="worker-integration-" + uuid4().hex))
        crashed = False
        while not workload.done():
            if time.monotonic() - started > 1200: raise AssertionError("bounded native experiment exceeded protocol")
            if any(p.poll() is not None for p in children[1:] if p.returncode != -9): raise AssertionError("resumed worker exited early")
            if not crashed and primary.poll() is not None: raise AssertionError("primary worker exited early")
            if db_task and db_task.done(): db_task.result()
            if not db_down:
                async with pool.acquire() as conn:
                    payloads = [json.loads(r["payload"]) for r in await conn.fetch("SELECT payload FROM sentinel_control.health_signals WHERE stream_id=$1", stream_id)]
                if (not crashed and time.monotonic() - started >= protocol["crash_after_healthy_seconds"]
                    and any(p["health"] == "healthy" for p in payloads)):
                    code = await stop_child(primary, crash=True)
                    assert code == -9
                    interventions.append({"kind": "worker-SIGKILL", "pid": primary.pid, "at": timestamp(datetime.now(timezone.utc))})
                    spawn(); crashed = True
                    spawn()  # Two real processes share the same stream/store after restart.
                if db_task is None and {p["service"] for p in payloads if p["event"] == "incident_opened"} >= {"checkout-service", "payment-service"}:
                    db_task = asyncio.create_task(database_outage())
            await asyncio.sleep(1)
        run_path = workload.result()
        if db_task: await db_task
        assert crashed and db_task is not None
        phase = "source-outage"
        async with httpx.AsyncClient(timeout=12., trust_env=False) as client:
            async def source_load():
                deadline = time.monotonic() + protocol["source_outage_load_seconds"]
                active = set()
                errors, counts = [], Counter()
                async def request():
                    response = await client.post("http://127.0.0.1:8001/v1/checkout", json={"sku": "sku-001", "quantity": 1, "amount": 9.9})
                    assert response.status_code == 200
                    counts["200"] += 1
                def completed(task):
                    active.discard(task)
                    if not task.cancelled() and task.exception(): errors.append(type(task.exception()).__name__)
                try:
                    while time.monotonic() < deadline:
                        if errors: raise AssertionError("source-outage business request failed")
                        if len(active) >= 16: raise AssertionError("source-outage traffic queue saturated")
                        task = asyncio.create_task(request()); active.add(task); task.add_done_callback(completed)
                        await asyncio.sleep(1 / protocol["experiment"]["rps"])
                    if active: await asyncio.gather(*tuple(active))
                    assert not errors
                    return dict(counts)
                finally:
                    for task in active: task.cancel()
                    if active: await asyncio.gather(*tuple(active), return_exceptions=True)
            extra = asyncio.create_task(source_load())
            now = datetime.now(timezone.utc)
            stop_at = now.replace(second=protocol["source_stop_second"], microsecond=0)
            if stop_at <= now: stop_at += timedelta(minutes=1)
            await asyncio.sleep((stop_at - now).total_seconds())
            source_down = True
            interventions.append({"kind": "prometheus-stop", "at": timestamp(datetime.now(timezone.utc))}); save_running()
            await asyncio.to_thread(docker, "stop", args.prometheus_container)
            interventions.append({"kind": "prometheus-stopped", "at": timestamp(datetime.now(timezone.utc))}); save_running()
            await asyncio.sleep(protocol["source_outage_seconds"])
            await asyncio.to_thread(docker, "start", args.prometheus_container)
            source_down = False
            interventions.append({"kind": "prometheus-start", "at": timestamp(datetime.now(timezone.utc))}); save_running()
            source_outcomes = await extra
        if metrics:
            await metrics.ready("both-running", instances={"health-0", "health-1"}, value=1)
        for process in children[1:]: assert await stop_child(process) == 0
        snapshot = await ledger_snapshot(pool, stream_id)
        (args.output / "database-snapshot.json").write_text(json.dumps(snapshot, indent=2, allow_nan=False) + "\n")
        phase = "metrics-verification"
        metrics_result = await metrics.final(logs) if metrics else None
        phase = "verification"
        result = await verify(pool, store, stream_id, logs, interventions)
        run = json.loads((run_path / "run.json").read_text())
        assert run["status"] == "completed" and run["dirty_worktree"] is False
        assert protocol_path.read_bytes() == protocol_bytes
        assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == digest for path, digest in source_hashes.items())
        report = {"status": "PASS", "mode": "actual-clock-local-health-worker-integration",
                  "source_commit": commit, "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
                  "stream_id": stream_id, "image_digests": images, "experiment_run": str(run_path),
                  "experiment_truth": run["run_id"], "interventions": interventions, "verification": result,
                  "source_outage_traffic": source_outcomes, "implementation_sha256": source_hashes,
                  "independent_trials": False, "training_corpus": False, "model_outputs_available": False,
                  "actions_allowed": False, "full_G4_accepted": False}
        if metrics_result is not None:
            report["own_metrics_verification"] = metrics_result
        (args.output / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        print("NATIVE HEALTH WORKER ACCEPTANCE: PASS " + str(args.output), flush=True)
    except Exception as exc:
        import traceback
        (args.output / "failure.json").write_text(json.dumps({"phase": phase, "error_type": type(exc).__name__,
            "frames": traceback.format_tb(exc.__traceback__), "source_commit": commit}, indent=2) + "\n")
        raise
    finally:
        if extra and not extra.done():
            extra.cancel()
            try: await extra
            except (asyncio.CancelledError, Exception): pass
        if workload and not workload.done():
            workload.cancel()
            try: await workload
            except (asyncio.CancelledError, Exception): pass
        if db_task and not db_task.done():
            db_task.cancel()
            try: await db_task
            except (asyncio.CancelledError, Exception): pass
        if db_down: await asyncio.to_thread(docker, "start", args.control_container)
        if source_down: await asyncio.to_thread(docker, "start", args.prometheus_container)
        for process in children:
            if process.poll() is None: await stop_child(process)
        for stream in streams: stream.close()
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-container", required=True)
    parser.add_argument("--control-project", required=True)
    parser.add_argument("--native-project", required=True)
    parser.add_argument("--prometheus-container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metrics-otlp-endpoint", help="Also verify the owned local worker metrics pipeline")
    args = parser.parse_args()
    try: asyncio.run(check(args))
    except Exception as exc: raise SystemExit("native worker acceptance failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
