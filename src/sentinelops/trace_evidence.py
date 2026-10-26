"""Shared trace parentage/status/correlation validation for smoke and offline evidence."""
from typing import Any
from sentinelops.telemetry.core import BUSINESS_ROUTES

EXPECTED_SERVICES=set(BUSINESS_ROUTES)

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
            if statuses[service] >= 500 and tags.get("error") is not True:
                raise ValueError(f"missing error span status for {service}")
            servers[service] = span
        elif kind == "client" and service == "checkout-service":
            dependency = tags.get("peer.service")
            if dependency in {"payment-service", "inventory-service"}:
                if dependency in clients:
                    raise ValueError(f"multiple client spans for {dependency}")
                if tags.get("http.response.status_code") != statuses[dependency]:
                    raise ValueError(f"unexpected downstream HTTP status for {dependency}")
                if statuses[dependency] >= 500 and tags.get("error") is not True:
                    raise ValueError(f"missing client error span status for {dependency}")
                clients[dependency] = span
    if set(servers) != EXPECTED_SERVICES or set(clients) != {"payment-service", "inventory-service"}:
        raise ValueError(f"trace incomplete: servers={sorted(servers)}, clients={sorted(clients)}")
    for service, client_span in clients.items():
        if parent_id(client_span) != servers["checkout-service"]["spanID"]:
            raise ValueError(f"{service} client span is not a child of Checkout")
        if parent_id(servers[service]) != client_span["spanID"]:
            raise ValueError(f"{service} server span is not a child of its client span")
    return servers


