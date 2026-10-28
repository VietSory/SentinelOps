"""Query actual OTLP→Collector→Prometheus worker metrics outside business evidence."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import time

import httpx

NAMES = {
    "ready": "sentinelops_worker_ready",
    "heartbeat": "sentinelops_worker_heartbeat_unixtime_seconds",
    "age": "sentinelops_worker_bucket_age_seconds",
    "source": "sentinelops_worker_source_available",
    "commits": "sentinelops_worker_committed_windows_total",
    "busy": "sentinelops_worker_busy_polls_total",
    "errors": "sentinelops_worker_errors_total",
    "unknown": "sentinelops_worker_unknown_decisions_total",
    "duration_count": "sentinelops_worker_processing_duration_seconds_count",
    "duration_sum": "sentinelops_worker_processing_duration_seconds_sum",
    "duration_bucket": "sentinelops_worker_processing_duration_seconds_bucket",
}


class WorkerMetricEvidence:
    def __init__(self, output: Path):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=False)

    async def snapshot(self) -> dict:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:9090", timeout=3., trust_env=False) as client:
            async def query(key, name):
                response = await client.get("/api/v1/query", params={"query": name + '{environment="local",worker_instance=~"health-[01]"}'})
                response.raise_for_status()
                body = response.json()
                assert body["status"] == "success" and body["data"]["resultType"] == "vector"
                for point in body["data"]["result"]:
                    assert math.isfinite(float(point["value"][1]))
                    assert not set(point["metric"]) & {"stream_id", "run_id", "incident_id", "trace_id", "pid", "path", "manifest_ref"}
                return key, body["data"]["result"]
            results = await asyncio.gather(*(query(k, n) for k, n in NAMES.items()))
        return {"observed_at_unix": time.time(), "queries": NAMES, "series": dict(results)}

    @staticmethod
    def values(snapshot, kind, *, component=None):
        result = {}
        for point in snapshot["series"][kind]:
            labels = point["metric"]
            if component is None or labels.get("component") == component:
                slot = labels["worker_instance"]
                assert slot not in result, "duplicate worker series for a stable replica slot"
                result[slot] = float(point["value"][1])
        return result

    @classmethod
    def fresh(cls, snapshot, instances):
        heartbeat = cls.values(snapshot, "heartbeat")
        return all(slot in heartbeat and 0 <= snapshot["observed_at_unix"] - heartbeat[slot] <= 15 for slot in instances)

    async def wait(self, name, predicate):
        deadline = time.monotonic() + 35
        last = None
        while time.monotonic() < deadline:
            last = await self.snapshot()
            if predicate(last):
                (self.output / (name + ".json")).write_text(json.dumps(last, indent=2) + "\n")
                return last
            await asyncio.sleep(1)
        (self.output / (name + "-failed.json")).write_text(json.dumps(last, indent=2) + "\n")
        raise AssertionError("worker metrics did not establish " + name)

    async def ready(self, name, *, instances, value):
        return await self.wait(name, lambda s: self.fresh(s, instances)
            and all(self.values(s, "ready").get(slot) == value for slot in instances))

    async def final(self, logs: list[Path]):
        # worker-0 was killed; worker-1 restarts slot0, worker-2 owns slot1.
        counts, durations = Counter(), Counter()
        for slot, path in zip(("health-0", "health-1"), logs[1:], strict=True):
            for line in path.read_text().splitlines():
                if line.startswith('{') and (item := json.loads(line)).get("status") == "COMMITTED":
                    counts[slot] += 1
                    durations[slot] += item["processing_ms"] / 1000
        instances = {"health-0", "health-1"}
        def complete(s):
            return (self.fresh(s, instances)
                and all(self.values(s, "ready").get(slot) == 0 for slot in instances)
                and all(self.values(s, "commits").get(slot, 0) == counts[slot] for slot in instances)
                and all(self.values(s, "duration_count").get(slot, 0) == counts[slot] for slot in instances)
                and all(math.isclose(self.values(s, "duration_sum").get(slot, 0), durations[slot], abs_tol=1e-6)
                        for slot in instances))
        snapshot = await self.wait("final-stopped-and-counts", complete)
        assert sum(counts.values()) > 0
        assert all(self.values(snapshot, "errors", component="database").get(slot, 0) > 0 for slot in instances)
        assert snapshot["series"]["source"] and snapshot["series"]["age"] and snapshot["series"]["unknown"]
        assert all(float(p["value"][1]) in (0, 1) for p in snapshot["series"]["source"])
        assert any(float(p["metric"].get("le", "nan")) == 10. for p in snapshot["series"]["duration_bucket"])
        return {"status": "PASS", "mode": "actual-otlp-collector-prometheus",
                "instances": sorted(instances), "resumed_commit_counts": dict(counts),
                "resumed_processing_seconds_sum": dict(durations), "database_errors_observed": True,
                "shutdown_readiness_zero": True, "mainloop_heartbeat_required": True,
                "metrics_are_business_truth": False, "actions_allowed": False}
