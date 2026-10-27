"""Exercise real 60s metric buckets and 300s feature windows in the sandbox.

Runs for about ten minutes. Do not run alongside another fault experiment.
No model, RCA quality or AWS integration is claimed by this check.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import PrometheusSource
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.telemetry.scheduling import capture_ready_bucket,wait_until_utc
from scripts.step3_smoke_test import (
    CHECKOUT, PAYMENT, INVENTORY, JAEGER, validate_trace, validate_logs, read_logs, wait_for,
)
from sentinelops.trace_query import decode_v3_trace

ROOT = Path(__file__).resolve().parents[1]


async def exercise(store: ArtifactStore, rps: float) -> tuple[list[dict], dict]:
    counters = {"200": 0, "502": 0, "other": 0}
    captures = []
    source = PrometheusSource()
    probe = None
    async with httpx.AsyncClient(timeout=10., trust_env=False) as client:
        for url in (f"{CHECKOUT}/ready", f"{PAYMENT}/ready", f"{INVENTORY}/ready", "http://127.0.0.1:9090/-/ready"):
            (await client.get(url)).raise_for_status()
        originals = {}
        for url in (PAYMENT, INVENTORY):
            response = await client.get(url + "/internal/faults")
            response.raise_for_status()
            originals[url] = response.json()

        async def fault(url, mode, delay_ms=0):
            response = await client.post(url+"/internal/faults", json={"mode": mode, "delay_ms": delay_ms})
            response.raise_for_status()

        started = time.time()
        phases = [("healthy", 0, 120), ("latency", 1200, 120), ("error", 0, 120), ("healthy", 0, 180)]
        final_bucket = int((started+sum(phase[2] for phase in phases))//60)*60
        semaphore = asyncio.Semaphore(16)
        tasks = set()

        async def request():
            nonlocal probe
            async with semaphore:
                trace_id, correlation = uuid4().hex, "step4-"+uuid4().hex
                response = await client.post(CHECKOUT+"/v1/checkout", json={"sku":"sku-001", "quantity":1, "amount":9.9},
                    headers={"x-correlation-id": correlation, "traceparent":f"00-{trace_id}-{uuid4().hex[:16]}-01"})
                code = str(response.status_code)
                counters[code if code in counters else "other"] += 1
                if response.status_code == 200:
                    probe = {"trace_id": trace_id, "correlation_id": correlation}

        async def capture_loop():
            bucket = int(started//60)*60 + 60
            while bucket <= final_bucket:
                await wait_until_utc(bucket+30)
                end = datetime.fromtimestamp(bucket, timezone.utc)
                for service in BUSINESS_ROUTES:
                    raw = await capture_ready_bucket(source,service,end,store=store)
                    captures.append(raw)
                print(f"CAPTURE {end.isoformat()} services=3", flush=True)
                bucket += 60

        collector = None
        try:
            await fault(INVENTORY, "healthy")
            await fault(PAYMENT, "healthy")
            collector = asyncio.create_task(capture_loop())
            for mode, delay, duration in phases:
                await fault(PAYMENT, mode, delay)
                print(f"LOAD phase={mode} duration={duration}s rps={rps}", flush=True)
                deadline = time.monotonic()+duration
                next_request = time.monotonic()
                while time.monotonic() < deadline:
                    task = asyncio.create_task(request())
                    tasks.add(task)
                    # Keep exceptions in completed tasks observable until gather.
                    next_request += 1/rps
                    await asyncio.sleep(max(0., next_request-time.monotonic()))
                await asyncio.gather(*tasks)
                tasks.clear()
            await collector
        finally:
            if collector and not collector.done():
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            restore_errors = []
            for url, original in originals.items():
                try:
                    await fault(url, original["mode"], original["delay_ms"])
                except Exception as exc:
                    restore_errors.append(str(exc))
            source.close()
            if restore_errors:
                raise RuntimeError("failed to restore faults: "+"; ".join(restore_errors))
    if counters["other"] or not counters["200"] or not counters["502"] or probe is None:
        raise RuntimeError(f"unexpected workload outcomes: {counters}")
    return captures, {"counters": counters, "probe": probe, "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/step4/"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--rps", type=float, default=3.)
    args = parser.parse_args()
    if not 1 <= args.rps <= 10:
        raise SystemExit("--rps must be 1…10")
    store = ArtifactStore(args.output)
    source_commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    source_dirty = bool(subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True).strip())
    captures, details = asyncio.run(exercise(store, args.rps))
    manifest = process_captures(captures, store)
    if manifest["quarantine_refs"] or manifest["eligible_feature_count"] < 3:
        raise RuntimeError(f"live features did not meet smoke coverage: {manifest}")
    observations = [store.read(ref) for ref in manifest["observation_refs"]]
    for service in BUSINESS_ROUTES:
        valid = [item for item in observations if item["service"]==service and item["payload"] is not None]
        if len(valid) < 4:
            raise RuntimeError(f"insufficient valid buckets for {service}")
    payments = [item["payload"] for item in observations if item["service"]=="payment-service" and item["payload"] is not None]
    if max(item["p95_latency_ms"] for item in payments) < 750 or max(item["error_rate"] for item in payments) < .1:
        raise RuntimeError("injected latency/error was not visible in canonical Payment telemetry")
    replayed = process_captures([store.read(ref) for ref in manifest["raw_refs"]], store)
    if manifest != replayed:
        raise RuntimeError("captured-source replay differs")

    probe = details["probe"]
    statuses = {service:200 for service in BUSINESS_ROUTES}
    with httpx.Client(timeout=10., trust_env=False) as client:
        def complete_trace(response):
            validate_trace(decode_v3_trace(response.text, probe["trace_id"]), probe["trace_id"], probe["correlation_id"], statuses, require_timing=True)
        response = wait_for(client, JAEGER+"/api/v3/traces/"+probe["trace_id"], complete_trace)
        servers = validate_trace(decode_v3_trace(response.text, probe["trace_id"]), probe["trace_id"], probe["correlation_id"], statuses, require_timing=True)
    trace_collected_at = timestamp(datetime.now(timezone.utc))
    logs = read_logs(details["started_at"])
    logs_collected_at = timestamp(datetime.now(timezone.utc))
    validate_logs(logs, probe["trace_id"], probe["correlation_id"], servers, statuses)
    evidence_refs = [store.write("raw", {"kind":"trace", "trace_id":probe["trace_id"], "body":response.text,"collected_at":trace_collected_at}),
                     store.write("raw", {"kind":"logs", "records":logs,"collected_at":logs_collected_at})]
    evidence_manifest = {key:value for key,value in manifest.items() if key != "manifest_ref"}
    evidence_manifest["normalization_manifest_ref"] = manifest["manifest_ref"]
    evidence_manifest["evidence_refs"] = {"traces":[evidence_refs[0]],"logs":evidence_refs[1],"infrastructure":[]}
    evidence_manifest_ref = store.write("manifests",evidence_manifest)
    report = {"status":"PASS", "scope":"local RED telemetry and features only", "manifest":manifest,
              "evidence_refs":evidence_refs, "details":details,
              "evidence_manifest_ref":evidence_manifest_ref,
              "commit_at_start":source_commit, "dirty_worktree_at_start":source_dirty,
              "commit_at_report":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
              "dirty_worktree_at_report":bool(subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True).strip())}
    path = Path(args.output)/"smoke-report.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"STEP 4 SMOKE TEST: PASS; observations={manifest['observation_count']}; eligible_features={manifest['eligible_feature_count']}; report={path}",flush=True)


if __name__ == "__main__":
    main()
