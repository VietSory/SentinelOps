import copy
import json

import httpx
import pytest

from scripts import step3_smoke_test as smoke

TRACE_ID = "0123456789abcdef0123456789abcdef"
CORRELATION_ID = "smoke-test"
STATUSES = {service: 200 for service in smoke.EXPECTED_SERVICES}


@pytest.fixture
def trace_payload():
    spans = []
    for span_id, process, kind, parent, dependency in (
        ("0000000000000001", "checkout", "server", "0000000000000009", None),
        ("0000000000000002", "checkout", "client", "0000000000000001", "inventory-service"),
        ("0000000000000003", "inventory", "server", "0000000000000002", None),
        ("0000000000000004", "checkout", "client", "0000000000000001", "payment-service"),
        ("0000000000000005", "payment", "server", "0000000000000004", None),
    ):
        tags = {"span.kind": kind, "http.response.status_code": 200}
        if kind == "server":
            tags["sentinelops.correlation_id"] = CORRELATION_ID
        if dependency:
            tags["peer.service"] = dependency
        spans.append({
            "spanID": span_id, "traceID": TRACE_ID, "processID": process,
            "tags": [{"key": key, "value": value} for key, value in tags.items()],
            "references": [{"refType": "CHILD_OF", "spanID": parent, "traceID": TRACE_ID}],
        })
    return {"data": [{
        "traceID": TRACE_ID, "spans": spans,
        "processes": {service: {"serviceName": f"{service}-service"} for service in ("checkout", "payment", "inventory")},
    }]}


def test_trace_validation_requires_one_complete_trace_with_client_parentage(trace_payload):
    servers = smoke.validate_trace(trace_payload, TRACE_ID, CORRELATION_ID, STATUSES)
    assert set(servers) == smoke.EXPECTED_SERVICES
    trace_payload["data"][0]["spans"][-1]["references"][0]["spanID"] = "0000000000000001"
    with pytest.raises(ValueError, match="server span is not a child"):
        smoke.validate_trace(trace_payload, TRACE_ID, CORRELATION_ID, STATUSES)


@pytest.mark.parametrize("mutation", ["other_trace", "partial_trace", "other_correlation", "wrong_status"])
def test_trace_validation_rejects_unrelated_or_incomplete_evidence(trace_payload, mutation):
    spans = trace_payload["data"][0]["spans"]
    if mutation == "other_trace":
        spans[-1]["traceID"] = "f" * 32
    elif mutation == "partial_trace":
        spans.pop()
    elif mutation == "other_correlation":
        spans[-1]["tags"].append({"key": "sentinelops.correlation_id", "value": "unrelated"})
    else:
        spans[-1]["tags"].append({"key": "http.response.status_code", "value": 503})
    with pytest.raises(ValueError):
        smoke.validate_trace(trace_payload, TRACE_ID, CORRELATION_ID, STATUSES)


def test_logs_must_match_the_same_server_span_ids(trace_payload):
    servers = smoke.validate_trace(trace_payload, TRACE_ID, CORRELATION_ID, STATUSES)
    records = [
        {
            "service": service, "event": event, "timestamp_unix_ms": 123456789,
            "correlation_id": CORRELATION_ID, "trace_id": TRACE_ID, "span_id": span["spanID"], "status_code": 200,
        }
        for service, span in servers.items() for event in ("request_started", "request_completed")
    ]
    smoke.validate_logs(records, TRACE_ID, CORRELATION_ID, servers, STATUSES)
    records[-1]["span_id"] = "f" * 16
    with pytest.raises(ValueError, match="log/trace mismatch"):
        smoke.validate_logs(records, TRACE_ID, CORRELATION_ID, servers, STATUSES)


def metrics_text():
    lines = []
    for service, route, method in (
        ("checkout-service", "/v1/checkout", "POST"),
        ("payment-service", "/v1/payments/charge", "POST"),
        ("inventory-service", "/v1/inventory/{sku}", "GET"),
    ):
        labels = {"service": service, "route": route, "method": method, "status_code": "200"}
        dimensions = ",".join(f"{key}={json.dumps(value)}" for key, value in labels.items())
        lines.append(f"sentinelops_http_requests_total{{{dimensions}}} 4")
        lines.append(f"sentinelops_http_request_duration_ms_milliseconds_count{{{dimensions}}} 4")
        if service != "inventory-service":
            labels["status_code"] = "502" if service == "checkout-service" else "503"
            dimensions = ",".join(f"{key}={json.dumps(value)}" for key, value in labels.items())
            lines.append(f"sentinelops_http_errors_total{{{dimensions}}} 1")
    return "\n".join(lines)


def test_metrics_require_all_services_histograms_and_error_counters():
    text = metrics_text()
    smoke.validate_metrics(text)
    with pytest.raises(ValueError, match="histogram missing"):
        smoke.validate_metrics("\n".join(line for line in text.splitlines() if "_count{" not in line))
    with pytest.raises(ValueError, match="error counter missing"):
        smoke.validate_metrics(text.replace("sentinelops_http_errors_total", "other_errors_total"))
    with pytest.raises(ValueError, match="not updated"):
        smoke.validate_metrics(text, baseline=text)


def test_parent_reference_cannot_point_to_another_trace(trace_payload):
    trace_payload["data"][0]["spans"][-1]["references"][0]["traceID"] = "f" * 32
    with pytest.raises(ValueError, match="parent from another trace"):
        smoke.validate_trace(trace_payload, TRACE_ID, CORRELATION_ID, STATUSES)


def test_health_wait_retries_404_and_transient_transport_errors(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("not started", request=request)
        return httpx.Response(404 if len(calls) == 2 else 200)

    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert smoke.wait_for(client, "http://test/health").status_code == 200
    assert len(calls) == 3


def test_health_wait_timeout_reports_last_failure(monkeypatch):
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(503, text="not ready"))) as client:
        with pytest.raises(RuntimeError, match="HTTP 503: not ready"):
            smoke.wait_for(client, "http://test/health", timeout=1)


def test_smoke_restores_both_original_faults_when_checkout_fails(monkeypatch):
    original = {
        8002: {"mode": "latency", "delay_ms": 15},
        8003: {"mode": "error", "delay_ms": 0},
    }
    faults = copy.deepcopy(original)

    def handle(request):
        if request.url.path == "/internal/faults":
            if request.method == "POST":
                faults[request.url.port] = json.loads(request.content)
            return httpx.Response(200, json=faults[request.url.port])
        if request.url.path == "/v1/checkout":
            return httpx.Response(500, text="checkout broken")
        return httpx.Response(200)

    real_client = httpx.Client
    monkeypatch.setattr(smoke.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(RuntimeError, match="checkout: expected 200, got 500"):
        smoke.main()
    assert faults == original
