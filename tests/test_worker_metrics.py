import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opentelemetry.metrics import get_meter_provider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from sentinelops.incidents.health import advance_health
from sentinelops.telemetry.core import BUSINESS_ROUTES, BucketMetrics, normalize_bucket, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.metrics import WorkerMetrics

END = datetime(2026, 10, 28, 0, 10, tzinfo=timezone.utc)
POLICY = json.loads(Path("configs/slo_health_policy.json").read_text())


def collect(reader):
    data = reader.get_metrics_data()
    return {m.name: list(m.data.data_points) for resource in data.resource_metrics
            for scope in resource.scope_metrics for m in scope.metrics}


def accepted(store, *, unknown=True):
    refs = []
    for service in sorted(BUSINESS_ROUTES):
        obs = None if unknown or service == "inventory-service" else normalize_bucket(BucketMetrics(
            service, END, 120, 0, ((0, 0), (100, 120), (math.inf, 120)), 1.,
            END + timedelta(seconds=30), ("fixture://metrics",), source_profile="native-fixture"), environment="test")
        signal = advance_health(None, obs, service=service, environment="test", source_profile="native-fixture",
            bucket_end=END, evaluated_at=END + timedelta(seconds=30), policy=POLICY)
        refs.append(store.write("signals", signal))
    batch = {"stream_id": "bounded-fixture-stream", "identity": {"environment": "test"},
             "bucket_end": timestamp(END), "signal_refs": sorted(refs)}
    return {"status": "COMMITTED", "manifest_ref": store.write("manifests", batch),
            "bucket_end": timestamp(END), "stream_id": batch["stream_id"], "processing_ms": 123.}


def test_missing_decisions_stay_absent_and_export_collection_cannot_refresh_loop_heartbeat():
    reader = InMemoryMetricReader()
    now = [END.timestamp() + 30]
    m = WorkerMetrics(reader, environment="test", instance="health-0", clock=lambda: now[0])
    try:
        values = collect(reader)
        assert values["sentinelops_worker_ready"][0].value == 0
        assert "sentinelops_worker_source_available" not in values
        assert "sentinelops_worker_bucket_age" not in values
        assert "sentinelops_worker_heartbeat_unixtime" not in values
        m.heartbeat(ready=True)
        before = collect(reader)["sentinelops_worker_heartbeat_unixtime"][0].value
        now[0] += 60
        assert collect(reader)["sentinelops_worker_heartbeat_unixtime"][0].value == before
        m.error("database")
        assert collect(reader)["sentinelops_worker_ready"][0].value == 0
        m.heartbeat(ready=False)
        assert collect(reader)["sentinelops_worker_ready"][0].value == 0
        m.heartbeat(ready=True)
        assert collect(reader)["sentinelops_worker_ready"][0].value == 1
    finally:
        m.close()


def test_accepted_typed_signals_produce_seconds_histogram_and_bounded_source_labels(tmp_path):
    reader = InMemoryMetricReader()
    now = [END.timestamp() + 30]
    m = WorkerMetrics(reader, environment="test", instance="health-15", clock=lambda: now[0])
    store = ArtifactStore(tmp_path)
    try:
        m.observe_result(accepted(store, unknown=False), store)
        m.observe_result({"status": "BUSY", "stream_id": "do-not-export"}, store)
        values = collect(reader)
        assert values["sentinelops_worker_committed_windows"][0].value == 1
        assert values["sentinelops_worker_busy_polls"][0].value == 1
        histogram = values["sentinelops_worker_processing_duration"][0]
        assert histogram.count == 1 and histogram.sum == pytest.approx(.123)
        assert 10. in histogram.explicit_bounds
        available = {p.attributes["business_service"]: p.value for p in values["sentinelops_worker_source_available"]}
        assert available == {"checkout-service": 1, "payment-service": 1, "inventory-service": 0}
        assert values["sentinelops_worker_unknown_decisions"][0].attributes["business_service"] == "inventory-service"
        assert values["sentinelops_worker_bucket_age"][0].value == 30
        now[0] += 15
        assert collect(reader)["sentinelops_worker_bucket_age"][0].value == 45
        now[0] = END.timestamp() - 1
        assert "sentinelops_worker_bucket_age" not in collect(reader)
        for points in values.values():
            for point in points:
                assert set(point.attributes) <= {"worker_instance", "environment", "business_service", "component"}
                assert point.attributes["worker_instance"] == "health-15"
                assert point.attributes["environment"] == "test"
    finally:
        m.close()


@pytest.mark.parametrize("problem", ["corrupt", "duplicate", "wrong-stream", "invalid-duration"])
def test_bad_artifacts_or_mixed_decisions_cannot_increment_success_metrics(tmp_path, problem):
    reader = InMemoryMetricReader()
    m = WorkerMetrics(reader, environment="test", instance="health-0")
    store = ArtifactStore(tmp_path)
    result = accepted(store)
    if problem == "corrupt":
        ref = result["manifest_ref"].removeprefix("artifact://")
        (store.root / ref).write_text('{}')
    elif problem == "duplicate":
        batch = store.read(result["manifest_ref"])
        batch["signal_refs"][1] = batch["signal_refs"][0]
        result["manifest_ref"] = store.write("manifests", batch)
    elif problem == "wrong-stream":
        result["stream_id"] = "foreign"
    else:
        result["processing_ms"] = float("nan")
    try:
        with pytest.raises(ValueError): m.observe_result(result, store)
        assert "sentinelops_worker_committed_windows" not in collect(reader)
        assert "sentinelops_worker_source_available" not in collect(reader)
    finally:
        m.close()


def test_real_sdk_constructor_ignores_ambient_credentials_resource_and_remote_endpoint(monkeypatch):
    import sentinelops.workers.metrics as module
    for key in ("OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_METRICS_HEADERS"):
        monkeypatch.setenv(key, "authorization=synthetic-test-token")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private.path=synthetic,service.name=wrong")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "https://example.invalid:443")
    captured = []
    def reader(exporter, **kwargs):
        captured.append(exporter)
        assert kwargs == {"export_interval_millis": 2000, "export_timeout_millis": 1000}
        return InMemoryMetricReader()
    monkeypatch.setattr(module, "PeriodicExportingMetricReader", reader)
    global_before = get_meter_provider()
    m = WorkerMetrics.otlp("http://127.0.0.1:4317", environment="test", instance="health-1")
    try:
        assert captured[0]._headers == (("x-sentinelops-component", "health-worker"),)
        assert captured[0]._endpoint == "127.0.0.1:4317" and captured[0]._insecure is True
        assert ("grpc.enable_http_proxy", 0) in captured[0]._channel_options
        assert dict(m.provider._sdk_config.resource.attributes) == {"service.name": "sentinelops-health-worker",
            "service.namespace": "sentinelops-control", "deployment.environment": "test"}
        assert get_meter_provider() is global_before
        for environment, instance in (("run-uuid", "health-0"), ("test", "health-16"), ("test", "pid-312")):
            with pytest.raises(ValueError): WorkerMetrics.otlp("http://localhost:4317", environment=environment, instance=instance)
        with pytest.raises(ValueError): WorkerMetrics.otlp("http://example.com:4317", environment="test", instance="health-0")
    finally:
        m.close()
        captured[0].shutdown()


def test_failed_final_flush_still_shuts_down_with_bounds_and_does_not_leave_ready(monkeypatch):
    reader = InMemoryMetricReader()
    m = WorkerMetrics(reader, environment="test", instance="health-0")
    m.heartbeat(ready=True)
    calls = []
    def flush(**kwargs):
        calls.append(("flush", kwargs))
        assert collect(reader)["sentinelops_worker_ready"][0].value == 0
        raise RuntimeError("synthetic exporter timeout")
    original_shutdown = m.provider.shutdown
    def shutdown(**kwargs):
        calls.append(("shutdown", kwargs))
        original_shutdown(**kwargs)
    monkeypatch.setattr(m.provider, "force_flush", flush)
    monkeypatch.setattr(m.provider, "shutdown", shutdown)
    with pytest.raises(RuntimeError): m.close()
    m.close()
    assert calls == [("flush", {"timeout_millis": 1000}), ("shutdown", {"timeout_millis": 2000})]


def test_cli_records_commit_before_observer_failure_and_closes_resources(monkeypatch, tmp_path, capsys):
    from scripts.run_health_worker import run
    events = []
    class Source:
        def __init__(self, *args): self.client = SimpleNamespace(timeout=None)
        def verify_configuration(self): return {"configuration_hash": "a" * 64}
        def close(self): pass
    class Metrics:
        def heartbeat(self, *, ready): events.append(("heartbeat", ready))
        def error(self, component): events.append(("error", component))
        def observe_result(self, result, store):
            assert '"status": "COMMITTED"' in capsys.readouterr().out
            raise PermissionError("synthetic artifact read denied")
        def close(self): events.append(("close",))
    pool = SimpleNamespace(close=AsyncMock())
    worker = SimpleNamespace(check_ready=AsyncMock(), tick=AsyncMock(return_value={"status": "COMMITTED"}),
        store=ArtifactStore(tmp_path), stream_id="fixture", identity={})
    monkeypatch.setenv("CONTROL_DATABASE_URL", "postgresql://control_writer@127.0.0.1:5433/sentinelops_control")
    monkeypatch.setattr("scripts.run_health_worker.PrometheusSource", Source)
    monkeypatch.setattr("scripts.run_health_worker.asyncpg.create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr("scripts.run_health_worker.HealthWorker", lambda *a, **k: worker)
    monkeypatch.setattr(WorkerMetrics, "otlp", lambda *a, **k: Metrics())
    args = SimpleNamespace(prometheus="http://localhost:9090", store=tmp_path,
        policy=Path("configs/slo_health_policy.json"), environment="test", status=False,
        max_buckets=1, poll_seconds=.25, metrics_otlp_endpoint="http://localhost:4317")
    with pytest.raises(PermissionError): asyncio.run(run(args))
    assert ("error", "artifact") in events and events[-2:] == [("heartbeat", False), ("close",)]
    pool.close.assert_awaited_once()
