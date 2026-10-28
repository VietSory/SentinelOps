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
    *, require_timing: bool = False, root_service: str = "checkout-service",
) -> dict[str, dict[str, Any]]:
    if root_service != "checkout-service":
        return _validate_direct_trace(payload,trace_id,correlation_id,statuses,root_service)
    traces = payload.get("data", [])
    if len(traces) != 1 or traces[0]["traceID"].lower().zfill(32) != trace_id:
        raise ValueError(f"Jaeger has not returned trace {trace_id}")
    trace_data = traces[0]
    spans = trace_data["spans"]
    processes = trace_data["processes"]
    servers: dict[str, dict[str, Any]] = {}
    clients: dict[str, dict[str, Any]] = {}
    for span in spans:
        timing = span.get("timing")
        if require_timing and timing is None:
            raise ValueError("trace lacks native span timing required by this consumer")
        if timing is not None:
            if (not isinstance(timing, dict) or set(timing) != {"start_unix_ns", "end_unix_ns", "duration_ns"}
                or any(type(value) is not int or not 0 <= value <= 2**64 - 1 for value in timing.values())
                or timing["end_unix_ns"] < timing["start_unix_ns"]
                or timing["duration_ns"] != timing["end_unix_ns"] - timing["start_unix_ns"]):
                raise ValueError("trace has inconsistent native span timing")
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


def _validate_direct_trace(payload,trace_id,correlation_id,statuses,service):
    """Explicit single-business-route profile; never salvage a partial cascade."""
    if service not in {"payment-service","inventory-service"} or set(statuses)!={service}:
        raise ValueError("unsupported direct business trace scope")
    traces=payload.get("data",[])
    if len(traces)!=1 or traces[0]["traceID"].lower().zfill(32)!=trace_id:
        raise ValueError("direct trace identity differs")
    trace=traces[0];spans=trace["spans"];servers=[];clients=[];identities=set()
    for span in spans:
        if span["spanID"] in identities or span["traceID"].lower().zfill(32)!=trace_id:
            raise ValueError("duplicate or mixed direct trace span identity")
        identities.add(span["spanID"])
        if trace["processes"][span["processID"]]["serviceName"]!=service:
            raise ValueError("direct trace contains another service; partial cascades are not direct requests")
        timing=span.get("timing")
        if (not isinstance(timing,dict) or set(timing)!={"start_unix_ns","end_unix_ns","duration_ns"}
            or any(type(value) is not int or not 0<=value<=2**64-1 for value in timing.values())
            or timing["end_unix_ns"]<timing["start_unix_ns"]
            or timing["duration_ns"]!=timing["end_unix_ns"]-timing["start_unix_ns"]):
            raise ValueError("direct trace requires consistent native timing")
        tags=span_tags(span)
        if tags.get("span.kind")=="server":servers.append(span)
        elif (tags.get("span.kind")=="client" and tags.get("peer.service")=="rds"
              and tags.get("db.system.name")=="postgresql"):clients.append(span)
        else:raise ValueError("unsupported span in direct business trace")
    if len(servers)!=1:raise ValueError("direct trace requires exactly one business server")
    root=servers[0];tags=span_tags(root);method,route=BUSINESS_ROUTES[service]
    if (tags.get("http.request.method"),tags.get("http.route"))!=(method,route):
        raise ValueError("direct trace is outside the canonical business route")
    if tags.get("sentinelops.correlation_id")!=correlation_id or tags.get("http.response.status_code")!=statuses[service]:
        raise ValueError("direct trace status/correlation mismatch")
    if statuses[service]>=500 and tags.get("error") is not True:
        raise ValueError("direct server failure lacks native error status")
    if root.get("references") and parent_id(root) in identities:
        raise ValueError("direct server cannot descend from a captured internal span")
    for client in clients:
        timing=client["timing"];parent=root["timing"]
        if (client["processID"]!=root["processID"] or parent_id(client)!=root["spanID"]
            or not parent["start_unix_ns"]<=timing["start_unix_ns"]<=timing["end_unix_ns"]<=parent["end_unix_ns"]):
            raise ValueError("direct database client is outside its owning business server")
    return {service:root}


