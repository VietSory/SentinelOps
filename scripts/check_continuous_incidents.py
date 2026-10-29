"""Actual local native workload -> accepted health -> continuous IER/HTTP check.

Runs for about 21 minutes. Only explicitly named, ownership-checked containers
are interrupted. Original fault settings are restored; no volume is removed.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx
import yaml

from scripts.check_health_worker_live import target, require_port
from scripts.check_health_worker_live import ledger_snapshot
from sentinelops.contracts import validate_payload
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.incidents import IncidentWorker


async def run(args):
    dsn = os.environ["CONTROL_DATABASE_URL"]
    parsed = urlparse(dsn)
    if parsed.hostname != "127.0.0.1" or parsed.username != "control_writer" or parsed.path != "/sentinelops_control":
        raise ValueError("dedicated local database required")
    require_port(target(args.control_container, args.control_project, "control-postgres"), "5432/tcp", parsed.port)
    require_port(target(args.prometheus_container, args.native_project, "prometheus"), "9090/tcp", 9090)
    args.output.mkdir(parents=True, exist_ok=False)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ValueError("commit verified software before native acceptance")
    store = ArtifactStore(args.output / "worker")
    pool = await asyncpg.create_pool(dsn, min_size=0, max_size=8, command_timeout=10)
    children, handles, interventions, outcomes = [], [], [], {}
    stopped_containers = set()
    started_at = timestamp(datetime.now(timezone.utc))
    def start(name, command, env=None):
        handle = (args.output / (name + ".log")).open("x")
        process = subprocess.Popen([sys.executable, *command], stdout=handle, stderr=subprocess.STDOUT, env=env)
        children.append(process); handles.append(handle)
        return process
    def event(kind):
        interventions.append({"event": kind, "at": timestamp(datetime.now(timezone.utc))})
        print(json.dumps(interventions[-1]), flush=True)
    async def period(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for process in children:
                if process.poll() is not None and process.returncode != -9:
                    raise RuntimeError("worker/API stopped unexpectedly; inspect bounded local logs")
            await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
    stop_traffic = asyncio.Event()
    async def traffic(service):
        endpoint = {"checkout-service": ("POST", "http://127.0.0.1:8001/v1/checkout"),
                    "payment-service": ("POST", "http://127.0.0.1:8002/v1/payments/charge"),
                    "inventory-service": ("GET", "http://127.0.0.1:8003/v1/inventory/sku-001")}[service]
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            tasks = set()
            async def request():
                body = {"sku": "sku-001", "quantity": 1, "amount": 10} if service == "checkout-service" else {"order_id": "native-" + uuid4().hex, "amount": 10}
                try:
                    response = await client.request(*endpoint, **({"json": body} if endpoint[0] == "POST" else {}))
                    key = service + "/" + str(response.status_code)
                except httpx.HTTPError as exc:
                    key = service + "/" + type(exc).__name__
                outcomes[key] = outcomes.get(key, 0) + 1
            while not stop_traffic.is_set():
                task = asyncio.create_task(request()); tasks.add(task); task.add_done_callback(tasks.discard)
                await asyncio.sleep(1 / 3)
            await asyncio.gather(*tasks)
    native = args.native_project
    token = secrets.token_hex(24)
    health_cmd = ["-m", "scripts.run_health_worker", "--store", str(store.root), "--environment", "local"]
    original, traffic_tasks = {}, []
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            for service, port in (("payment", 8002), ("inventory", 8003)):
                response = await client.get(f"http://127.0.0.1:{port}/internal/faults"); response.raise_for_status()
                original[port] = {k: v for k, v in response.json().items() if k != "service"}
                if original[port]["mode"] != "healthy":
                    raise ValueError("native acceptance requires initially clear faults")
            traffic_tasks = [asyncio.create_task(traffic(service)) for service in ("checkout-service", "payment-service", "inventory-service")]
            health = start("health", health_cmd)
            stream_id = None
            for _ in range(30):
                await period(1)
                for line in (args.output / "health.log").read_text().splitlines():
                    item = json.loads(line)
                    if item.get("status") == "READY": stream_id = item["stream_id"]
                if stream_id: break
            if stream_id is None: raise RuntimeError("health worker not ready")
            incident_cmd = ["-m", "scripts.run_incident_worker", "--store", str(store.root), "--stream-id", stream_id, "--native-project", native]
            consumer = start("incidents", incident_cmd)
            env = {**os.environ, "SENTINELOPS_API_TOKEN": token}
            start("api", ["-m", "uvicorn", "sentinelops.main:app", "--host", "127.0.0.1", "--port", str(args.api_port)], env)
            event("healthy-start")
            await period(120)
            worker = IncidentWorker(pool, store, stream_id, yaml.safe_load(Path("configs/topology.yaml").read_text()),
                json.loads(Path("configs/slo_health_policy.json").read_text()), json.loads(Path("configs/rca_policy.json").read_text()),
                evidence_source=__import__("sentinelops.workers.evidence", fromlist=["NativeEvidenceSource"]).NativeEvidenceSource(native, directory=Path.cwd()))
            async with pool.acquire() as conn:
                refs = await conn.fetch("SELECT commit_ref FROM sentinel_control.incident_batches WHERE consumer_id=$1", worker.consumer_id)
            assert refs and all(not store.read(r["commit_ref"])["results"] for r in refs)
            response = await client.post("http://127.0.0.1:8002/internal/faults", json={"mode": "latency", "delay_ms": 1200})
            response.raise_for_status(); event("payment-latency-start")
            await period(160)
            consumer.kill(); consumer.wait(timeout=10); event("consumer-sigkill")
            consumer = start("incidents-resumed", incident_cmd)
            start("incidents-competing", incident_cmd)
            await period(140)
            response = await client.post("http://127.0.0.1:8002/internal/faults", json=original[8002]); response.raise_for_status()
            event("payment-clear")
            await period(240)
            for port in (8002, 8003):
                response = await client.post(f"http://127.0.0.1:{port}/internal/faults", json={"mode": "error", "delay_ms": 0})
                response.raise_for_status()
            event("independent-payment-inventory-errors")
            await period(300)
            for port in (8002, 8003):
                response = await client.post(f"http://127.0.0.1:{port}/internal/faults", json=original[port]); response.raise_for_status()
            event("independent-errors-clear")
            subprocess.run(["docker", "stop", args.prometheus_container], check=True, stdout=subprocess.DEVNULL)
            stopped_containers.add(args.prometheus_container); event("prometheus-outage")
            await period(70)
            subprocess.run(["docker", "start", args.prometheus_container], check=True, stdout=subprocess.DEVNULL)
            stopped_containers.remove(args.prometheus_container)
            subprocess.run(["docker", "stop", args.control_container], check=True, stdout=subprocess.DEVNULL)
            stopped_containers.add(args.control_container); event("database-outage")
            await period(30)
            subprocess.run(["docker", "start", args.control_container], check=True, stdout=subprocess.DEVNULL)
            stopped_containers.remove(args.control_container); event("database-restarted")
            await period(150)
            # Actual protected HTTP readback of IDs produced by the live consumer.
            async with pool.acquire() as conn:
                batches = [dict(r) for r in await conn.fetch("SELECT * FROM sentinel_control.incident_batches WHERE consumer_id=$1 ORDER BY bucket_end", worker.consumer_id)]
                signals = [json.loads(r["payload"]) for r in await conn.fetch("SELECT payload FROM sentinel_control.health_signals WHERE stream_id=$1", stream_id)]
            commits = [store.read(r["commit_ref"]) for r in batches]
            commands = [command for commit in commits for command in commit["commands"]]
            ids = sorted({c["payload"]["incident_id"] for c in commands})
            affected = [g["affected_services"] for c in commits for item in c["closed_clusters"] for g in item["correlation"]["groups"]]
            assert ["checkout-service", "payment-service"] in affected, affected
            assert ["payment-service"] in affected and ["inventory-service"] in affected and ["checkout-service"] in affected, affected
            assert not any({"payment-service", "inventory-service"} <= set(g) for g in affected)
            assert any(s["availability"] == "UNKNOWN" for s in signals)
            snapshots = []
            for identifier in ids:
                response = await client.get(f"http://127.0.0.1:{args.api_port}/v1/incidents/{identifier}/context", headers={"Authorization": "Bearer " + token})
                response.raise_for_status(); snapshot = response.json(); validate_payload("incident-envelope.schema.json", snapshot)
                assert snapshot["revision"] == 3 and snapshot["context"]["lifecycle_stage"] == "ESCALATED"
                assert len(snapshot["timeline"]["events"]) == 3
                assert snapshot["payload"]["service_signals"] == []
                snapshots.append(snapshot)
            response = await client.post(f"http://127.0.0.1:{args.api_port}/v1/detect", json={"schema_version": "1.0", "service": "payment-service",
                "window_start": "2026-10-29T00:00:00Z", "window_end": "2026-10-29T00:05:00Z", "features": {"latency": 1}})
            assert response.status_code == 503
            report = {"status": "PASS", "source_commit": commit, "mode": "actual-continuous-local-runtime", "started_at": started_at,
                "completed_at": timestamp(datetime.now(timezone.utc)), "stream_id": stream_id, "consumer_id": worker.consumer_id,
                "incidents": snapshots, "accepted_consumer_buckets": len(batches), "health_signals": len(signals),
                "unknown_decisions": sum(s["availability"] == "UNKNOWN" for s in signals), "affected_groups": affected,
                "request_outcomes": outcomes, "interventions": interventions, "commit_refs": [r["commit_ref"] for r in batches],
                "model_outputs_available": False, "actions_allowed": False, "holdout_opened": False}
            snapshot = await ledger_snapshot(pool, stream_id)
            snapshot["consumer_identity"] = worker.identity
            snapshot["consumer_commit_refs"] = report["commit_refs"]
            (args.output / "database-snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
            (args.output / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({k: v for k, v in report.items() if k not in ("incidents", "commit_refs")}), flush=True)
    except BaseException as exc:
        (args.output / "failure.json").write_text(json.dumps({"status": "FAIL", "error_type": type(exc).__name__,
            "source_commit": commit, "interventions": interventions, "request_outcomes": outcomes}, indent=2) + "\n")
        raise
    finally:
        for container in stopped_containers:
            subprocess.run(["docker", "start", container], check=True, stdout=subprocess.DEVNULL)
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            for port, settings in original.items():
                response = await client.post(f"http://127.0.0.1:{port}/internal/faults", json=settings)
                response.raise_for_status()
        stop_traffic.set()
        await asyncio.gather(*traffic_tasks)
        for process in children:
            if process.poll() is None: process.terminate()
        for process in children:
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        for handle in handles: handle.close()
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control-container", "control-project", "native-project", "prometheus-container"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-port", type=int, default=8008)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
