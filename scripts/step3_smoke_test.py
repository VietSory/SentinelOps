from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = "http://127.0.0.1:8001"
PAYMENT = "http://127.0.0.1:8002"
INVENTORY = "http://127.0.0.1:8003"
OTEL_HEALTH = "http://127.0.0.1:13133"
OTEL_METRICS = "http://127.0.0.1:8889/metrics"
JAEGER = "http://127.0.0.1:16686"
EXPECTED_SERVICES = {"checkout-service", "payment-service", "inventory-service"}


def wait_for(
    client: httpx.Client,
    url: str,
    check: Callable[[httpx.Response], None] | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}: {response.text[:300]}")
            if check is not None:
                check(response)
            return response
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {url}: {last_error}")


def span_tags(span: dict[str, Any]) -> dict[str, Any]:
    return {tag["key"]: tag["value"] for tag in span.get("tags", [])}


def parent_id(span: dict[str, Any]) -> str:
    parents = [ref for ref in span.get("references", []) if ref["refType"] == "CHILD_OF"]
    if len(parents) != 1:
        raise ValueError(f"span {span['spanID']} must have exactly one parent")
    if parents[0]["traceID"].lower().zfill(32) != span["traceID"].lower().zfill(32):
        raise ValueError(f"span {span['spanID']} references a parent from another trace")
    return parents[0]["spanID"]


def validate_trace(
    payload: dict[str, Any],
    trace_id: str,
    correlation_id: str,
    statuses: dict[str, int],
) -> dict[str, dict[str, Any]]:
    traces = payload.get("data", [])
    if len(traces) != 1 or traces[0]["traceID"].lower().zfill(32) != trace_id:
        raise ValueError(f"Jaeger has not returned trace {trace_id}")
    trace_data = traces[0]
    spans = trace_data["spans"]
    processes = trace_data["processes"]
    servers: dict[str, dict[str, Any]] = {}
    clients: dict[str, dict[str, Any]] = {}
    for span in spans:
        if span["traceID"].lower().zfill(32) != trace_id:
            raise ValueError("response contains a span from another trace")
        service = processes[span["processID"]]["serviceName"]
        tags = span_tags(span)
        kind = str(tags.get("span.kind", "")).lower()
        if kind == "server" and service in EXPECTED_SERVICES:
            if service in servers:
                raise ValueError(f"multiple server spans for {service}")
            if tags.get("sentinelops.correlation_id") != correlation_id:
                raise ValueError(f"correlation ID mismatch for {service}")
            if tags.get("http.response.status_code") != statuses[service]:
                raise ValueError(f"unexpected HTTP status for {service}: {tags}")
            servers[service] = span
        elif kind == "client" and service == "checkout-service":
            dependency = tags.get("peer.service")
            if dependency in {"payment-service", "inventory-service"}:
                if dependency in clients:
                    raise ValueError(f"multiple client spans for {dependency}")
                if tags.get("http.response.status_code") != statuses[dependency]:
                    raise ValueError(f"unexpected downstream HTTP status for {dependency}")
                clients[dependency] = span
    if set(servers) != EXPECTED_SERVICES or set(clients) != {"payment-service", "inventory-service"}:
        raise ValueError(f"trace incomplete: servers={sorted(servers)}, clients={sorted(clients)}")
    for service, client_span in clients.items():
        if parent_id(client_span) != servers["checkout-service"]["spanID"]:
            raise ValueError(f"{service} client span is not a child of Checkout")
        if parent_id(servers[service]) != client_span["spanID"]:
            raise ValueError(f"{service} server span is not a child of its client span")
    return servers


def metric_value(text: str, prefix: str, labels: dict[str, str], suffix: str = "") -> float:
    # Collector unit/counter suffixes vary; match the instrument prefix and exact dimensions.
    for line in text.splitlines():
        name, separator, rest = line.partition("{")
        if not separator or not name.startswith(prefix) or not name.endswith(suffix):
            continue
        dimensions, separator, value = rest.rpartition("}")
        if not separator:
            continue
        if not all(
            re.search(r"(?:^|,)" + re.escape(f"{key}={json.dumps(val)}") + r"(?:,|$)", dimensions)
            for key, val in labels.items()
        ):
            continue
        return float(value.split()[0])
    return 0.0


def validate_metrics(text: str, baseline: str = "") -> None:
    routes = {
        "checkout-service": ("POST", "/v1/checkout"),
        "payment-service": ("POST", "/v1/payments/charge"),
        "inventory-service": ("GET", "/v1/inventory/{sku}"),
    }
    for service, (method, route) in routes.items():
        labels = {"service": service, "method": method, "route": route, "status_code": "200"}
        expected = 4 if service == "inventory-service" else 3
        previous = metric_value(baseline, "sentinelops_http_requests", labels)
        if metric_value(text, "sentinelops_http_requests", labels) < previous + expected:
            raise ValueError(f"request counter missing or not updated for {service}")
        previous = metric_value(baseline, "sentinelops_http_request_duration_ms", labels, "_count")
        if metric_value(text, "sentinelops_http_request_duration_ms", labels, "_count") < previous + expected:
            raise ValueError(f"duration histogram missing or not updated for {service}")
    for service, status in (("checkout-service", "502"), ("payment-service", "503")):
        method, route = routes[service]
        labels = {"service": service, "method": method, "route": route, "status_code": status}
        previous = metric_value(baseline, "sentinelops_http_errors", labels)
        if metric_value(text, "sentinelops_http_errors", labels) < previous + 1:
            raise ValueError(f"error counter missing or not updated for {service}")


def validate_logs(
    records: list[dict[str, Any]],
    trace_id: str,
    correlation_id: str,
    servers: dict[str, dict[str, Any]],
    statuses: dict[str, int],
) -> None:
    for service, span in servers.items():
        for event in ("request_started", "request_completed"):
            matches = [
                record for record in records
                if record.get("correlation_id") == correlation_id
                and record.get("service") == service and record.get("event") == event
            ]
            if len(matches) != 1:
                raise ValueError(f"expected one {service}/{event} log for {correlation_id}")
            record = matches[0]
            if record.get("trace_id") != trace_id or record.get("span_id") != span["spanID"].zfill(16):
                raise ValueError(f"log/trace mismatch for {service}/{event}")
            if not isinstance(record.get("timestamp_unix_ms"), int):
                raise ValueError(f"timestamp missing for {service}/{event}")
            if event == "request_completed" and record.get("status_code") != statuses[service]:
                raise ValueError(f"log HTTP status mismatch for {service}")


def read_logs(since: str) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--no-log-prefix", "--since", since, *sorted(EXPECTED_SERVICES)],
            cwd=ROOT, capture_output=True, text=True, timeout=15, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"cannot read testbed logs: {exc.stderr.strip()}") from exc
    records = []
    for line in result.stdout.splitlines():
        if line.lstrip().startswith("{"):
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
    return records


def set_fault(client: httpx.Client, url: str, mode: str, delay_ms: int = 0) -> None:
    response = client.post(f"{url}/internal/faults", json={"mode": mode, "delay_ms": delay_ms})
    response.raise_for_status()


def run_checkout(client: httpx.Client, expected_status: int) -> tuple[str, str, dict[str, int], float]:
    trace_id = uuid4().hex
    correlation_id = f"step3-{uuid4().hex}"
    started = time.monotonic()
    response = client.post(
        f"{CHECKOUT}/v1/checkout",
        headers={"x-correlation-id": correlation_id, "traceparent": f"00-{trace_id}-{uuid4().hex[:16]}-01"},
        json={"sku": "sku-001", "quantity": 1, "amount": 99.90, "currency": "USD"},
    )
    elapsed = time.monotonic() - started
    if response.status_code != expected_status:
        raise RuntimeError(f"checkout: expected {expected_status}, got {response.status_code}: {response.text}")
    if response.headers.get("x-correlation-id") != correlation_id or response.headers.get("x-trace-id") != trace_id:
        raise RuntimeError("checkout did not return the supplied correlation/trace IDs")
    if expected_status == 200 and response.json()["correlation_id"] != correlation_id:
        raise RuntimeError("checkout body correlation ID does not match its headers")
    statuses = {
        "checkout-service": expected_status,
        "payment-service": 503 if expected_status == 502 else 200,
        "inventory-service": 200,
    }
    return trace_id, correlation_id, statuses, elapsed


def main() -> None:
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        for url in (OTEL_HEALTH, JAEGER, f"{CHECKOUT}/ready", f"{PAYMENT}/ready", f"{INVENTORY}/ready"):
            wait_for(client, url)
        print("PASS collector, Jaeger and service readiness")

        baseline_metrics = wait_for(client, OTEL_METRICS).text
        logs_since = datetime.now(timezone.utc).isoformat()
        originals = {url: wait_for(client, f"{url}/internal/faults").json() for url in (PAYMENT, INVENTORY)}
        runs = []
        try:
            for url in originals:
                set_fault(client, url, "healthy")
            runs.append(run_checkout(client, 200))
            set_fault(client, PAYMENT, "latency", 1200)
            runs.append(run_checkout(client, 200))
            if runs[-1][3] < 1.0:
                raise RuntimeError("payment latency did not propagate to Checkout")
            set_fault(client, PAYMENT, "error")
            runs.append(run_checkout(client, 502))
            set_fault(client, PAYMENT, "healthy")
            runs.append(run_checkout(client, 200))
        finally:
            restore_errors = []
            for url, original in originals.items():
                try:
                    set_fault(client, url, original["mode"], original["delay_ms"])
                except httpx.HTTPError as exc:
                    restore_errors.append(f"{url}: {exc}")
            if restore_errors:
                raise RuntimeError("could not restore original faults: " + "; ".join(restore_errors))
        print("PASS healthy checkout, latency, cascading failure and recovery; original faults restored")

        def check_services(response: httpx.Response) -> None:
            found = set(response.json().get("data", []))
            if not EXPECTED_SERVICES.issubset(found):
                raise ValueError(f"Jaeger service registry incomplete: {sorted(found)}")

        wait_for(client, f"{JAEGER}/api/services", check_services)
        print("PASS Jaeger service registry")
        traces = []
        for trace_id, correlation_id, statuses, _elapsed in runs:
            def check_trace(response: httpx.Response) -> None:
                validate_trace(response.json(), trace_id, correlation_id, statuses)

            # The UI query API reads one exact trace and avoids streaming v3 search responses.
            response = wait_for(client, f"{JAEGER}/api/traces/{trace_id}", check_trace)
            servers = validate_trace(response.json(), trace_id, correlation_id, statuses)
            traces.append((trace_id, correlation_id, servers, statuses))
        print("PASS all four distributed traces and SERVER -> CLIENT -> SERVER parentage")
        wait_for(client, OTEL_METRICS, lambda response: validate_metrics(response.text, baseline_metrics))
        print("PASS request counters, duration histograms and 5xx counters for the business routes")

        deadline = time.monotonic() + 10
        while True:
            records = read_logs(logs_since)
            try:
                for trace_id, correlation_id, servers, statuses in traces:
                    validate_logs(records, trace_id, correlation_id, servers, statuses)
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        print("PASS structured logs match correlation IDs, trace IDs, server span IDs and HTTP statuses")
    print("STEP 3 SMOKE TEST: PASS")


if __name__ == "__main__":
    main()
