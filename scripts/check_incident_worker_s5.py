"""Real independent-root S5 development with the continuous incident consumer."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import httpx

from scripts.check_health_worker_live import target, require_port, ledger_snapshot
from scripts.incident_worker_evidence import IncidentWorkerEvidence
from sentinelops.experiments.runner import ExperimentConfig, execute, image_identity
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore


async def healthy_observation(seconds: int, rps: float) -> dict:
    """Declared post-scenario traffic, never an RCA input or health substitute."""
    counts, failures, active = Counter(), [], set()
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=12., trust_env=False) as client:
        async def request(entry):
            if entry == "checkout-service":
                response = await client.post("http://127.0.0.1:8001/v1/checkout", json={"sku": "sku-001", "quantity": 1, "amount": 9.9})
            elif entry == "payment-service":
                response = await client.post("http://127.0.0.1:8002/v1/payments/charge", json={"order_id": uuid4().hex, "amount": 9.9})
            else:
                response = await client.get("http://127.0.0.1:8003/v1/inventory/sku-001")
            if response.status_code != 200:
                raise AssertionError("post-scenario business response was not healthy HTTP 200")
            counts[entry] += 1
        def complete(task):
            active.discard(task)
            if not task.cancelled() and task.exception(): failures.append(type(task.exception()).__name__)
        try:
            for port in (8002, 8003):
                response = await client.get(f"http://127.0.0.1:{port}/internal/faults")
                response.raise_for_status()
                assert response.json()["mode"] == "healthy"
            index = 0
            while time.monotonic() < started + seconds:
                if failures or len(active) > 48:
                    raise AssertionError("post-scenario traffic failed or exceeded its bounded queue")
                for entry in ("checkout-service", "payment-service", "inventory-service"):
                    task = asyncio.create_task(request(entry)); active.add(task); task.add_done_callback(complete)
                index += 1
                await asyncio.sleep(max(0., started + index/rps - time.monotonic()))
            if active: await asyncio.gather(*tuple(active))
            assert not failures and set(counts) == {"checkout-service", "payment-service", "inventory-service"}
            return {"counts": dict(counts), "seconds": seconds, "configured_rps_at_each_entry": rps}
        finally:
            for task in active: task.cancel()
            if active: await asyncio.gather(*tuple(active), return_exceptions=True)


async def check(args):
    root = Path.cwd()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ValueError("freeze committed consumer/protocol before native acceptance")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    protocol_path = Path("configs/incident_worker_acceptance.json")
    protocol_bytes = protocol_path.read_bytes(); protocol = json.loads(protocol_bytes)
    s5_bytes = Path(protocol["s5_protocol"]).read_bytes()
    candidates = [c for c in json.loads(s5_bytes)["cases"] if c["name"] == args.case]
    if len(candidates) != 1 or args.case not in protocol["s5_cases"]:
        raise ValueError("select an original declared S5 development case")
    config = ExperimentConfig(**{k: v for k, v in candidates[0].items() if k != "name"})
    config.validate()
    parsed = urlsplit(os.environ.get("CONTROL_DATABASE_URL", ""))
    if (parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username != "control_writer"
        or parsed.path != "/sentinelops_control" or os.environ.get("COMPOSE_PROJECT_NAME") != args.native_project):
        raise ValueError("native S5 acceptance requires explicitly matching local writer/project scope")
    require_port(target(args.control_container, args.control_project, "control-postgres"), "5432/tcp", parsed.port)
    require_port(target(args.prometheus_container, args.native_project, "prometheus"), "9090/tcp", 9090)
    for service, port in (("checkout-service", 8001), ("payment-service", 8002), ("inventory-service", 8003)):
        identifier = subprocess.check_output(["docker", "compose", "ps", "-q", service], text=True).strip()
        require_port(target(identifier, args.native_project, service), "8000/tcp", port)
    images = image_identity(root)
    paths = [*root.glob("src/sentinelops/**/*.py"), *root.glob("contracts/*.schema.json"),
             *[root/p for p in ("scripts/check_incident_worker_s5.py", "scripts/incident_worker_evidence.py",
                "scripts/run_incident_worker.py", "scripts/run_health_worker.py", "configs/rca_policy.json",
                "configs/slo_health_policy.json", "configs/topology.yaml", "infra/control/004-incident-consumer.sql",
                "configs/incident_worker_acceptance.json", "configs/s5_development_protocol.json")]]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    args.output.mkdir(parents=True, exist_ok=False)
    pool = await asyncpg.create_pool(os.environ["CONTROL_DATABASE_URL"], min_size=1, max_size=4, command_timeout=10)
    log = (args.output/"worker-0.log").open("x")
    process = subprocess.Popen([sys.executable, "-m", "scripts.run_health_worker", "--store", str(args.output/"worker"),
        "--max-buckets", "100"], stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy())
    observer, work, post = None, None, None
    stream_id = None
    try:
        for _ in range(60):
            for line in (args.output/"worker-0.log").read_text().splitlines():
                if line.startswith("{") and (row := json.loads(line)).get("status") == "READY": stream_id = row["stream_id"]
            if stream_id: break
            if process.poll() is not None: raise AssertionError("native health process failed startup")
            await asyncio.sleep(.5)
        assert stream_id
        (args.output/"running.json").write_text(json.dumps({"source_commit": commit, "stream_id": stream_id,
            "health_pid": process.pid, "native_project": args.native_project, "case": args.case,
            "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest()}, indent=2)+"\n")
        observer = IncidentWorkerEvidence(args.output/"incident-consumer", ArtifactStore(args.output/"worker"), stream_id, args.native_project)
        observer.spawn()
        work = asyncio.create_task(execute(config, project=root, output=args.output/"experiments",
            session_id="continuous-s5-development-"+uuid4().hex))
        deadline = time.monotonic()+config.warmup_seconds+config.active_seconds+config.recovery_seconds+240
        while not work.done():
            if process.poll() is not None or time.monotonic() > deadline:
                raise AssertionError("native S5 health/workload exited or exceeded its predeclared bound")
            await observer.observe(pool)
            await asyncio.sleep(1.)
        run_path = work.result()
        post = asyncio.create_task(healthy_observation(protocol["s5_post_scenario_healthy_observation_seconds"],
                                                      protocol["s5_post_scenario_rps_at_each_entry"]))
        while not post.done():
            if process.poll() is not None: raise AssertionError("native S5 health process exited during recovery observation")
            await observer.observe(pool)
            await asyncio.sleep(1.)
        post_result = post.result()
        await observer.require_closed(pool)
        process.terminate(); assert await asyncio.to_thread(process.wait, timeout=20) == 0
        await observer.catch_up(pool); await observer.stop()
        snapshot = await ledger_snapshot(pool, stream_id)
        (args.output/"database-snapshot.json").write_text(json.dumps(snapshot, indent=2)+"\n")
        result = await observer.verify(pool, scenario="S5")
        run = json.loads((run_path/"run.json").read_text())
        assert run["status"] == "completed" and run["dirty_worktree"] is False
        assert protocol_path.read_bytes() == protocol_bytes and Path(protocol["s5_protocol"]).read_bytes() == s5_bytes
        assert all(hashlib.sha256((root/p).read_bytes()).hexdigest() == digest for p, digest in hashes.items())
        report = {"status": "PASS", "mode": "actual-clock-S5-continuous-incident-development", "case": args.case,
            "source_commit": commit, "stream_id": stream_id, "image_digests": images, "experiment_run": str(run_path),
            "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(), "original_s5_protocol_sha256": hashlib.sha256(s5_bytes).hexdigest(),
            "post_scenario_healthy_observation": post_result, "continuous_incident_verification": result,
            "implementation_sha256": hashes, "actions_allowed": False, "training_corpus": False,
            "full_G4_accepted": False, "heldout_evaluation": False, "independent_trials": False}
        (args.output/"acceptance.json").write_text(json.dumps(report, indent=2)+"\n")
        print("NATIVE S5 INCIDENT CONSUMER ACCEPTANCE: PASS "+str(args.output), flush=True)
    except Exception as exc:
        import traceback
        (args.output/"failure.json").write_text(json.dumps({"error_type": type(exc).__name__,
            "frames": traceback.format_tb(exc.__traceback__), "source_commit": commit, "stream_id": stream_id}, indent=2)+"\n")
        raise
    finally:
        for task in (post, work):
            if task and not task.done():
                task.cancel()
                try: await task
                except (Exception, asyncio.CancelledError): pass
        if process.poll() is None:
            process.terminate(); await asyncio.to_thread(process.wait, timeout=20)
        log.close()
        if observer: await observer.stop()
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("independent-latency", "independent-blocking-errors"), required=True)
    parser.add_argument("--control-container", required=True)
    parser.add_argument("--control-project", required=True)
    parser.add_argument("--native-project", required=True)
    parser.add_argument("--prometheus-container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try: asyncio.run(check(args))
    except Exception as exc: raise SystemExit("native S5 incident acceptance failed: "+type(exc).__name__) from None


if __name__ == "__main__":
    main()
