import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from services.common import observability, runtime
from services.common.faults import FaultRequest

TRACE_ID = "0123456789abcdef0123456789abcdef"
PARENT_ID = "0123456789abcdef"
PAYLOAD = {"sku": "sku-001", "quantity": 1, "amount": 99.90, "currency": "USD"}


class TestPool:
    __test__ = False

    def __init__(self):
        self.available = True

    @asynccontextmanager
    async def acquire(self):
        if not self.available:
            raise RuntimeError("database unavailable")
        yield self

    async def fetchval(self, _query):
        return 1

    async def fetchrow(self, _query, sku):
        return {"sku": sku, "name": "Test item", "quantity": 50}

    async def execute(self, _query, *_args):
        return "INSERT 0 1"


@pytest.fixture
def observed_apps(monkeypatch):
    exporter = InMemorySpanExporter()
    providers = {}
    readers = {}

    def configure(service):
        if service not in providers:
            resource = Resource.create({"service.name": service})
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
            reader = InMemoryMetricReader()
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            providers[service] = tracer_provider, meter_provider
            readers[service] = reader
        tracer_provider, meter_provider = providers[service]
        return tracer_provider.get_tracer("test"), meter_provider.get_meter("test")

    monkeypatch.setattr(observability, "configure", configure)
    monkeypatch.setattr(runtime, "configure", configure)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    monkeypatch.setenv("PAYMENT_URL", "http://127.0.0.1:8002")
    monkeypatch.setenv("INVENTORY_URL", "http://127.0.0.1:8003")
    modules = {
        service: importlib.reload(importlib.import_module(f"services.{service}.app"))
        for service in ("payment", "inventory", "checkout")
    }
    for service in ("payment", "inventory"):
        modules[service].app.state.db = TestPool()

    real_client = httpx.AsyncClient
    state = SimpleNamespace(timeout_payment=False)
    downstream_apps = {8002: modules["payment"].app, 8003: modules["inventory"].app}

    class DownstreamTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if state.timeout_payment and request.url.port == 8002:
                raise httpx.ReadTimeout("payment timeout", request=request)
            transport = httpx.ASGITransport(app=downstream_apps[request.url.port], raise_app_exceptions=False)
            return await transport.handle_async_request(request)

    def downstream_client(**kwargs):
        assert kwargs["trust_env"] is False
        return real_client(transport=DownstreamTransport(), **kwargs)

    monkeypatch.setattr(modules["checkout"].httpx, "AsyncClient", downstream_client)

    async def request(path="/v1/checkout", method="POST", service="checkout", correlation_id="test-correlation", trace_id=TRACE_ID):
        async with real_client(
            transport=httpx.ASGITransport(app=modules[service].app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            return await client.request(
                method, path, json=PAYLOAD if method == "POST" else None,
                headers={"x-correlation-id": correlation_id, "traceparent": f"00-{trace_id}-{PARENT_ID}-01"},
            )

    state.modules = modules
    state.exporter = exporter
    state.readers = readers
    state.request = request
    state.client = real_client
    state.configure = configure
    yield state
    for tracer_provider, meter_provider in providers.values():
        tracer_provider.shutdown()
        meter_provider.shutdown()


def metrics_for(reader):
    data = reader.get_metrics_data()
    return {
        metric.name: metric.data.data_points
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def spans_by_service(exporter, kind):
    return {
        span.resource.attributes["service.name"]: span
        for span in exporter.get_finished_spans() if span.kind == kind
    }


def test_checkout_propagates_client_span_and_correlates_logs_and_metrics(observed_apps, caplog):
    caplog.set_level("INFO", logger="sentinelops-testbed")
    response = asyncio.run(observed_apps.request())
    assert response.status_code == 200
    assert response.headers["x-trace-id"] == TRACE_ID
    assert response.headers["x-correlation-id"] == response.json()["correlation_id"] == "test-correlation"
    servers = spans_by_service(observed_apps.exporter, SpanKind.SERVER)
    assert set(servers) == {"checkout-service", "payment-service", "inventory-service"}
    root = servers["checkout-service"]
    assert root.parent.span_id == int(PARENT_ID, 16)
    clients = [span for span in observed_apps.exporter.get_finished_spans() if span.kind == SpanKind.CLIENT]
    assert len(clients) == 2
    for client in clients:
        service = client.attributes["peer.service"]
        assert client.parent.span_id == root.context.span_id
        assert servers[service].parent.span_id == client.context.span_id
        assert client.attributes["http.response.status_code"] == 200
    for span in observed_apps.exporter.get_finished_spans():
        assert span.context.trace_id == int(TRACE_ID, 16)
        assert span.status.status_code == StatusCode.UNSET
    records = [json.loads(record.message) for record in caplog.records if record.name == "sentinelops-testbed"]
    for service, server in servers.items():
        for event in ("request_started", "request_completed"):
            matching = [record for record in records if record["service"] == service and record["event"] == event]
            assert len(matching) == 1
            assert matching[0]["correlation_id"] == "test-correlation"
            assert matching[0]["trace_id"] == TRACE_ID
            assert matching[0]["span_id"] == f"{server.context.span_id:016x}"
        metrics = metrics_for(observed_apps.readers[service])
        assert metrics["sentinelops_http_requests"][0].value == 1
        assert metrics["sentinelops_http_request_duration_ms"][0].count == 1
        assert metrics["sentinelops_http_requests"][0].attributes["status_code"] == 200
    inventory_metrics = metrics_for(observed_apps.readers["inventory-service"])
    assert inventory_metrics["sentinelops_http_requests"][0].attributes["route"] == "/v1/inventory/{sku}"


def test_cascading_5xx_marks_server_and_client_spans_and_counts_errors(observed_apps):
    observed_apps.modules["payment"].fault.update(FaultRequest(mode="error"))
    response = asyncio.run(observed_apps.request())
    assert response.status_code == 502
    servers = spans_by_service(observed_apps.exporter, SpanKind.SERVER)
    for service, status in (("checkout-service", 502), ("payment-service", 503)):
        assert servers[service].status.status_code == StatusCode.ERROR
        assert servers[service].attributes["http.response.status_code"] == status
        errors = metrics_for(observed_apps.readers[service])["sentinelops_http_errors"]
        assert errors[0].value == 1
        assert errors[0].attributes["status_code"] == status
    payment_client = next(
        span for span in observed_apps.exporter.get_finished_spans()
        if span.kind == SpanKind.CLIENT and span.attributes["peer.service"] == "payment-service"
    )
    assert payment_client.status.status_code == StatusCode.ERROR
    assert payment_client.attributes["http.response.status_code"] == 503
    assert servers["payment-service"].parent.span_id == payment_client.context.span_id
    assert servers["inventory-service"].status.status_code == StatusCode.UNSET


def test_downstream_timeout_is_traced_and_counted(observed_apps):
    observed_apps.timeout_payment = True
    response = asyncio.run(observed_apps.request())
    assert response.status_code == 504
    servers = spans_by_service(observed_apps.exporter, SpanKind.SERVER)
    assert servers["checkout-service"].status.status_code == StatusCode.ERROR
    payment_client = next(
        span for span in observed_apps.exporter.get_finished_spans()
        if span.kind == SpanKind.CLIENT and span.attributes["peer.service"] == "payment-service"
    )
    assert payment_client.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in payment_client.events)
    errors = metrics_for(observed_apps.readers["checkout-service"])["sentinelops_http_errors"]
    assert errors[0].attributes["status_code"] == 504


def test_unknown_urls_share_one_metric_dimension(observed_apps):
    async def run():
        for path in ("/missing/a", "/missing/b"):
            response = await observed_apps.request(path=path, method="GET")
            assert response.status_code == 404

    asyncio.run(run())
    points = metrics_for(observed_apps.readers["checkout-service"])["sentinelops_http_requests"]
    assert len(points) == 1
    assert points[0].attributes["route"] == "__unmatched__"
    assert points[0].value == 2


def test_readiness_checks_downstream_database(observed_apps):
    observed_apps.modules["payment"].app.state.db.available = False
    response = asyncio.run(observed_apps.request(path="/ready", method="GET"))
    assert response.status_code == 503
    assert response.json()["detail"] == {"payment-service": "unavailable", "inventory-service": "ok"}


def test_concurrent_requests_keep_trace_and_correlation_context_separate(observed_apps, caplog):
    caplog.set_level("INFO", logger="sentinelops-testbed")
    trace_ids = [TRACE_ID, "fedcba9876543210fedcba9876543210"]

    async def run():
        return await asyncio.gather(*(
            observed_apps.request(correlation_id=f"request-{index}", trace_id=trace_id)
            for index, trace_id in enumerate(trace_ids)
        ))

    responses = asyncio.run(run())
    assert all(response.status_code == 200 for response in responses)
    records = [json.loads(record.message) for record in caplog.records if record.name == "sentinelops-testbed"]
    for index, trace_id in enumerate(trace_ids):
        matching = [record for record in records if record["correlation_id"] == f"request-{index}"]
        assert {record["service"] for record in matching} == {"checkout-service", "payment-service", "inventory-service"}
        assert {record["trace_id"] for record in matching} == {trace_id}
    assert runtime.correlation_id_var.get() == "unknown"


def test_unhandled_exception_retains_route_and_records_failure(observed_apps, caplog):
    caplog.set_level("INFO", logger="sentinelops-testbed")
    app = FastAPI()
    runtime.install_request_context(app, "exception-service")

    @app.get("/fail/{item}")
    async def fail(item: str):
        raise RuntimeError("test failure")

    async def run():
        async with observed_apps.client(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            return await client.get("/fail/one")

    response = asyncio.run(run())
    assert response.status_code == 500
    server = spans_by_service(observed_apps.exporter, SpanKind.SERVER)["exception-service"]
    assert server.status.status_code == StatusCode.ERROR
    assert server.attributes["http.route"] == "/fail/{item}"
    assert server.attributes["http.response.status_code"] == 500
    metrics = metrics_for(observed_apps.readers["exception-service"])
    assert metrics["sentinelops_http_errors"][0].value == 1
    assert metrics["sentinelops_http_errors"][0].attributes["route"] == "/fail/{item}"
    completed = [json.loads(record.message) for record in caplog.records if '"request_completed"' in record.message]
    assert completed[0]["status_code"] == 500
    assert completed[0]["trace_id"] == f"{server.context.trace_id:032x}"
