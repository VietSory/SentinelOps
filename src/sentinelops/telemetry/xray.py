"""Read-only X-Ray request capture and log-attested structural trace adaptation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re

from sentinelops.contracts import utc_time
from sentinelops.telemetry.cloudwatch import decode_capture as decode_cloudwatch
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.trace_evidence import validate_trace

SOURCE_PROFILE = "xray-log-attested-request/v1"
MAX_PAGES, MAX_BYTES, MAX_DOCUMENT_BYTES, MAX_SPANS, MAX_DEPTH = 20, 16*1024*1024, 65536, 4096, 64


class TraceUnavailable(ValueError):
    """Incomplete, future or unsupported evidence cannot supply a supported trace."""


def trace_ids(value):
    if not isinstance(value, str):
        raise ValueError("X-Ray trace identity must be a string")
    value = value.lower()
    if re.fullmatch("[0-9a-f]{32}", value):
        flat = value
    elif re.fullmatch("1-[0-9a-f]{8}-[0-9a-f]{24}", value):
        flat = value[2:10] + value[11:]
    else:
        raise ValueError("unsupported X-Ray/W3C trace ID")
    if int(flat, 16) == 0:
        raise ValueError("zero trace ID is unavailable")
    # W3C IDs need not encode an event clock in the first eight digits.
    return "1-" + flat[:8] + "-" + flat[8:], flat


def _span_id(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-fA-F]{16}", value) or int(value, 16) == 0:
        raise ValueError("invalid X-Ray segment identity")
    return value.lower()


def _context(value, environment):
    if (not isinstance(value, dict) or set(value) != {"region", "account_id", "environment"}
        or not isinstance(value["region"], str) or not re.fullmatch("[a-z0-9-]+", value["region"])
        or not isinstance(value["account_id"], str) or not re.fullmatch("[0-9]{12}", value["account_id"])
        or not isinstance(environment, str) or not environment or value["environment"] != environment):
        raise ValueError("explicit X-Ray source account/region/environment required")


def _response(body):
    if not isinstance(body, dict) or "Error" in body or not isinstance(body.get("Traces"), list):
        raise ValueError("X-Ray source response is unavailable")
    result = {"Traces": [], "UnprocessedTraceIds": deepcopy(body.get("UnprocessedTraceIds", []))}
    for trace in body["Traces"]:
        result["Traces"].append({key: deepcopy(trace[key]) for key in ("Id", "Segments", "LimitExceeded") if key in trace})
    if "NextToken" in body:
        result["NextToken"] = body["NextToken"]
    return result


class XRaySource:
    """Injected read-only client; its configured timeouts/retries are operator-owned."""
    def __init__(self, client, *, region, account_id, environment, clock=None):
        self.context = {"region": region, "account_id": account_id, "environment": environment}
        _context(self.context, environment)
        if getattr(getattr(client, "meta", None), "region_name", None) != region:
            raise ValueError("X-Ray client region differs from the capture context")
        self.client, self.clock = client, clock or (lambda: datetime.now(timezone.utc))

    def capture(self, trace_id, *, request_entry_service):
        xray, flat = trace_ids(trace_id)
        if request_entry_service not in BUSINESS_ROUTES:
            raise ValueError("observed request-entry service is required before querying")
        result = {"capture_version": SOURCE_PROFILE, "source_profile": SOURCE_PROFILE,
            "context": deepcopy(self.context), "trace_id": flat, "xray_trace_id": xray,
            "request_entry_service": request_entry_service, "started_at": timestamp(self.clock()), "pages": []}
        token, seen, size = None, set(), 0
        try:
            for _ in range(MAX_PAGES):
                request = {"TraceIds": [xray], **({"NextToken": token} if token is not None else {})}
                body = _response(self.client.batch_get_traces(**request))
                size += len(json.dumps(body, allow_nan=False).encode())
                if size > MAX_BYTES:
                    raise ValueError("X-Ray byte budget exceeded")
                result["pages"].append({"request": request, "response": body, "received_at": timestamp(self.clock())})
                token = body.get("NextToken")
                if token is None:
                    break
                if not isinstance(token, str) or not token or token in seen:
                    raise ValueError("X-Ray pagination did not advance")
                seen.add(token)
            else:
                raise ValueError("X-Ray page budget exceeded")
        except Exception as exc:
            result["source_error"] = {"error_type": type(exc).__name__[:64]}
        result["collected_at"] = timestamp(self.clock())
        return result


def _document(text):
    if not isinstance(text, str) or not 0 < len(text.encode()) <= MAX_DOCUMENT_BYTES:
        raise ValueError("X-Ray document is missing or exceeds its byte bound")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate X-Ray JSON key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite X-Ray JSON value")
    try:
        value = json.loads(text, parse_float=Decimal, object_pairs_hook=pairs, parse_constant=invalid)
    except (InvalidOperation, OverflowError, RecursionError) as exc:
        raise ValueError("X-Ray JSON numeric/depth bound exceeded") from exc
    if not isinstance(value, dict):
        raise ValueError("X-Ray document must be an object")
    return value


def _ns(value):
    if type(value) is int:
        value = Decimal(value)
    if (not isinstance(value, Decimal) or not value.is_finite()
        or not 0 <= value <= Decimal(2**64-1) / Decimal(1000000000)):
        raise ValueError("X-Ray timing requires bounded native JSON seconds")
    scaled = value * Decimal(1000000000)
    if scaled != scaled.to_integral_value():
        raise ValueError("X-Ray source timing exceeds supported nanosecond representation")
    return int(scaled)


def _nodes(capture, as_of):
    if (not isinstance(capture, dict) or capture.get("capture_version") != SOURCE_PROFILE
        or capture.get("source_profile") != SOURCE_PROFILE or "source_error" in capture):
        raise TraceUnavailable("X-Ray capture is unavailable or unsupported")
    context = capture["context"]
    _context(context, context["environment"])
    xray, flat = trace_ids(capture["trace_id"])
    if capture["xray_trace_id"] != xray or capture.get("request_entry_service") not in BUSINESS_ROUTES:
        raise ValueError("X-Ray request identity/scope differs")
    started, collected = (utc_time(capture[key]) for key in ("started_at", "collected_at"))
    if not started <= collected <= as_of:
        raise TraceUnavailable("X-Ray response had not arrived at the decision cutoff")
    pages, token, tokens, previous, size = capture["pages"], None, set(), started, 0
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise TraceUnavailable("X-Ray page inventory missing or unbounded")
    nodes, trace_seen = {}, False

    def add(doc, parent=None, depth=0, *, top=False):
        if depth > MAX_DEPTH or len(nodes) >= MAX_SPANS:
            raise ValueError("X-Ray span/depth budget exceeded")
        if not isinstance(doc, dict):
            raise ValueError("X-Ray subsegment must be an object")
        identity = _span_id(doc["id"])
        if identity in nodes:
            raise ValueError("duplicate X-Ray segment ID")
        if "trace_id" in doc and (not isinstance(doc["trace_id"], str) or doc["trace_id"].lower() != xray):
            raise ValueError("X-Ray document belongs to another trace")
        if top and "trace_id" not in doc:
            raise ValueError("independent X-Ray document lacks trace identity")
        if doc.get("type") not in (None, "subsegment"):
            raise ValueError("unsupported X-Ray segment type")
        subsegment = not top or doc.get("type") == "subsegment"
        declared = _span_id(doc["parent_id"]) if "parent_id" in doc else None
        if parent is not None and declared is not None and parent != declared:
            raise ValueError("nested X-Ray parent conflicts with its native hierarchy")
        parent = parent if parent is not None else declared
        if subsegment and parent is None:
            raise TraceUnavailable("independent X-Ray subsegment lacks its parent")
        for key in ("in_progress", "inferred", "error", "fault", "throttle"):
            if key in doc and type(doc[key]) is not bool:
                raise ValueError("X-Ray source flags must be booleans")
        if doc.get("in_progress") or "start_time" not in doc or "end_time" not in doc:
            raise TraceUnavailable("X-Ray segment is still incomplete")
        start, end = _ns(doc["start_time"]), _ns(doc["end_time"])
        if end < start or end > _epoch_ns(as_of):
            raise TraceUnavailable("X-Ray timing is reversed or beyond the cutoff")
        http = doc.get("http", {})
        if (not isinstance(http, dict) or not isinstance(http.get("request", {}), dict)
            or not isinstance(http.get("response", {}), dict)):
            raise ValueError("X-Ray HTTP source fields are malformed")
        if not isinstance(doc.get("name"), str) or not 0 < len(doc["name"]) <= 200:
            raise ValueError("X-Ray segment lacks native service/name identity")
        nodes[identity] = {"doc": doc, "parent": parent, "subsegment": subsegment,
            "timing": {"start_unix_ns": start, "end_unix_ns": end, "duration_ns": end-start}}
        children = doc.get("subsegments", [])
        if not isinstance(children, list):
            raise ValueError("X-Ray nested span inventory differs")
        for child in children:
            add(child, identity, depth+1)

    for index, page in enumerate(pages):
        expected = {"TraceIds": [xray], **({"NextToken": token} if token is not None else {})}
        received = utc_time(page["received_at"])
        if stable_id(page["request"]) != stable_id(expected) or not previous <= received <= collected:
            raise ValueError("X-Ray query/receipt chain differs")
        previous = received
        body = page["response"]
        if (not isinstance(body, dict) or not set(body) <= {"Traces", "UnprocessedTraceIds", "NextToken"}
            or not isinstance(body.get("Traces"), list) or not isinstance(body.get("UnprocessedTraceIds"), list)):
            raise ValueError("X-Ray source response scope differs")
        if body["UnprocessedTraceIds"]:
            raise TraceUnavailable("X-Ray has unprocessed trace IDs")
        size += len(json.dumps(body, allow_nan=False).encode())
        if size > MAX_BYTES:
            raise ValueError("X-Ray byte budget exceeded")
        for trace in body["Traces"]:
            if not isinstance(trace["Id"], str) or trace["Id"].lower() != xray:
                raise ValueError("X-Ray returned another requested trace")
            trace_seen = True
            if "LimitExceeded" in trace and type(trace["LimitExceeded"]) is not bool:
                raise ValueError("X-Ray truncation flag is malformed")
            if trace.get("LimitExceeded"):
                raise TraceUnavailable("X-Ray trace is truncated")
            if not isinstance(trace.get("Segments"), list):
                raise ValueError("X-Ray segment inventory differs")
            for item in trace["Segments"]:
                doc = _document(item["Document"])
                if _span_id(item["Id"]) != _span_id(doc["id"]):
                    raise ValueError("X-Ray outer/document segment IDs differ")
                add(doc, top=True)
        token = body.get("NextToken")
        if token is not None:
            if not isinstance(token, str) or not token or token in tokens or index == len(pages)-1:
                raise TraceUnavailable("X-Ray pagination is cyclic or incomplete")
            tokens.add(token)
        elif index != len(pages)-1:
            raise ValueError("X-Ray pages continue after terminal response")
    if not trace_seen or not nodes:
        raise TraceUnavailable("X-Ray has not returned the requested trace")
    for identity in nodes:
        seen, cursor = set(), identity
        while cursor in nodes:
            if cursor in seen:
                raise ValueError("X-Ray parent graph contains a cycle")
            seen.add(cursor)
            if len(seen) > MAX_DEPTH:
                raise ValueError("X-Ray parent depth budget exceeded")
            cursor = nodes[cursor]["parent"]
        if nodes[identity]["subsegment"] and nodes[identity]["parent"] not in nodes:
            raise TraceUnavailable("X-Ray independent subsegment parent is unavailable")
    return nodes


def _epoch_ns(point):
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = point - epoch
    return ((delta.days*86400+delta.seconds)*1000000+delta.microseconds)*1000


def _completions(captures, trace_id, correlation_id, context, as_of):
    facts, refs, available, size = {}, set(), [], 0
    if not isinstance(captures, list) or not 1 <= len(captures) <= 1000:
        raise ValueError("bounded original CloudWatch completion captures required")
    for capture in captures:
        if not isinstance(capture, dict):
            raise ValueError("original completion capture must be an object")
        size += len(json.dumps(capture, allow_nan=False).encode())
        if size > MAX_BYTES * 4:
            raise ValueError("X-Ray completion source byte budget exceeded")
        # Inspect receipt metadata before parsing any event message.
        received = utc_time(capture["collected_at"])
        if received > as_of:
            raise TraceUnavailable("completion capture had not arrived at the cutoff")
        normalized = decode_cloudwatch(capture, environment=context["environment"])
        catalog = capture["catalog"]
        arn = catalog["log_group_arn"].split(":")
        if arn[3] != context["region"] or arn[4] != context["account_id"]:
            raise ValueError("X-Ray and completion logs have another account/region")
        if normalized["quality"]["status"] not in ("valid", "insufficient_traffic", "no_traffic"):
            raise TraceUnavailable("completion source counts are incomplete or inconsistent")
        ref = "artifact://raw/" + stable_id(capture) + ".json"
        refs.add(ref)
        available.append(received)
        for page in capture["logs"]:
            for event in page["response"]["events"]:
                row = json.loads(event["message"])
                if row.get("trace_id") != trace_id:
                    continue
                identity = _span_id(row["span_id"])
                if row.get("correlation_id") != correlation_id:
                    raise ValueError("completion correlation differs from the observed request")
                fact = {key: row[key] for key in ("service", "method", "route", "status_code",
                    "request_instance_id", "timestamp_unix_ms", "duration_ms", "correlation_id")}
                if identity in facts and stable_id(facts[identity]) != stable_id(fact):
                    raise ValueError("conflicting completion identity for X-Ray server")
                facts[identity] = fact
    return facts, refs, available


def normalize_capture(capture, completion_captures, *, environment, correlation_id, as_of):
    """Structural conversion only; never supplies RCA/action/source-parity approval."""
    timestamp(as_of)
    if not isinstance(correlation_id, str) or not correlation_id:
        raise ValueError("observed request correlation identity required")
    nodes = _nodes(capture, as_of)
    _context(capture["context"], environment)
    xray, flat = trace_ids(capture["trace_id"])
    logs, refs, receipts = _completions(completion_captures, flat, correlation_id, capture["context"], as_of)
    entry = capture["request_entry_service"]
    if entry != "checkout-service" and any(row["service"] != entry for row in logs.values()):
        raise ValueError("direct request scope conflicts with observed cascade logs")
    selected, servers, processes, ignored = {}, {}, {}, []
    for identity, node in sorted(nodes.items()):
        doc = node["doc"]
        if node["subsegment"] or doc["name"] not in BUSINESS_ROUTES:
            continue
        log = logs.get(identity)
        if log is None or doc.get("inferred"):
            raise TraceUnavailable("native business segment lacks original server completion proof")
        if doc["name"] != log["service"]:
            raise ValueError("native X-Ray service differs from completion identity")
        lower = log["timestamp_unix_ms"] * 1000000
        if node["timing"]["end_unix_ns"] < lower or node["timing"]["start_unix_ns"] >= lower + 1000000:
            raise ValueError("native server timing conflicts with completion's millisecond interval")
        if log["service"] in servers:
            raise ValueError("multiple native servers for a supported business service")
        http = doc.get("http", {})
        if ("method" in http.get("request", {}) and http["request"]["method"] != log["method"]
            or "status" in http.get("response", {}) and http["response"]["status"] != log["status_code"]):
            raise ValueError("native X-Ray HTTP fields conflict with completion logs")
        servers[log["service"]] = identity
        processes[identity] = {"serviceName": log["service"]}
        selected[identity] = {"node": node, "owner": identity, "tags": {
            "span.kind": "server", "http.request.method": log["method"], "http.route": log["route"],
            "http.response.status_code": log["status_code"], "sentinelops.correlation_id": correlation_id}}
    expected = set(BUSINESS_ROUTES) if entry == "checkout-service" else {entry}
    if set(servers) != expected:
        raise TraceUnavailable("X-Ray is missing log-attested servers in the original request scope")
    if entry != "checkout-service" and nodes[servers[entry]]["parent"] in nodes:
        raise TraceUnavailable("direct request has a captured upstream parent")
    for service, server in servers.items():
        parent = nodes[server]["parent"]
        if parent is None or parent not in nodes or not nodes[parent]["subsegment"]:
            continue
        cursor = parent
        while nodes[cursor]["subsegment"]:
            cursor = nodes[cursor]["parent"]
        if cursor not in selected or cursor == server:
            continue
        doc = nodes[parent]["doc"]
        status = doc.get("http", {}).get("response", {}).get("status")
        method = doc.get("http", {}).get("request", {}).get("method")
        if (doc.get("inferred") or doc.get("namespace") != "remote" or type(status) is not int
            or not 100 <= status <= 599 or method != BUSINESS_ROUTES[service][0]):
            continue
        child_time, owner_time = nodes[parent]["timing"], nodes[cursor]["timing"]
        if not owner_time["start_unix_ns"] <= child_time["start_unix_ns"] <= child_time["end_unix_ns"] <= owner_time["end_unix_ns"]:
            raise ValueError("native remote client escapes its owning segment clock")
        selected[parent] = {"node": nodes[parent], "owner": cursor, "tags": {
            "span.kind": "client", "peer.service": service, "http.response.status_code": status}}
    spans = []
    for identity, node in sorted(nodes.items()):
        if identity not in selected:
            ignored.append({"span_id": identity, "reason": "no-supported-log-attested-business-role"})
            continue
        item, doc = selected[identity], node["doc"]
        tags = item["tags"]
        if any(doc.get(key) is True for key in ("fault", "error", "throttle")):
            tags["error"] = True
        spans.append({"traceID": flat, "spanID": identity, "processID": item["owner"], "timing": node["timing"],
            "references": [{"refType": "CHILD_OF", "traceID": flat, "spanID": node["parent"]}] if node["parent"] else [],
            "tags": [{"key": key, "value": value} for key, value in sorted(tags.items())]})
    payload = {"data": [{"traceID": flat, "spans": spans, "processes": processes}]}
    statuses = {service: logs[identity]["status_code"] for service, identity in servers.items()}
    try:
        validate_trace(payload, flat, correlation_id, statuses, require_timing=True, root_service=entry)
    except (ValueError, KeyError) as exc:
        raise TraceUnavailable("native X-Ray request shape is incomplete or unsupported") from exc
    refs.add("artifact://raw/" + stable_id(capture) + ".json")
    available = max([utc_time(capture["collected_at"]), *receipts])
    return {"adapter_version": SOURCE_PROFILE, "environment": environment, "trace_id": flat, "xray_trace_id": xray,
        "request_entry_service": entry, "available_at": timestamp(available), "as_of": timestamp(as_of),
        "trace_payload": payload, "source_refs": sorted(refs), "ignored_spans": ignored,
        "structural_request_validated": True, "source_parity": "not-accepted", "actions_allowed": False,
        "clock_uncertainty_ns": None, "earliest_backend_availability_verified": False,
        "limitations": ["Original response receipts are availability upper bounds, not earliest backend arrival.",
            "Exact decimal conversion preserves source values, not nanosecond clock accuracy.",
            "Only log-attested HTTP servers and observed remote caller links have supported roles.",
            "Unsupported/inferred spans, sampling and actual source/account/deployment compatibility need H3 review.",
            "No RCA scores, causality, model, holdout or action approval is produced."]}
