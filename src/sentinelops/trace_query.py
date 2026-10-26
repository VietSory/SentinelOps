"""Decode Jaeger's stable v3 OTLP JSON query response.

The normalized representation is local to smoke/evidence validators; it does not
depend on Jaeger's removed internal UI API. A response may contain NDJSON frames.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _attributes(items: list[dict[str, Any]]) -> dict[str, Any]:
    values = {}
    for item in items:
        value = item["value"]
        if "intValue" in value:
            values[item["key"]] = int(value["intValue"])
        elif "doubleValue" in value:
            values[item["key"]] = float(value["doubleValue"])
        elif "boolValue" in value:
            values[item["key"]] = value["boolValue"]
        elif "stringValue" in value:
            values[item["key"]] = value["stringValue"]
    return values


def _hex_id(value: str, length: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value):
        raise ValueError("invalid OTLP trace/span ID")
    if int(value, 16) == 0:
        raise ValueError("zero OTLP trace/span ID")
    return value.lower()


def _span_timing(span: dict) -> dict | None:
    """Preserve fixed64 epoch nanoseconds without a float round trip.

    Legacy captures without both fields retain unavailable timing. Partial or
    malformed declared timing is an error, not an invented zero duration.
    """
    keys = ("startTimeUnixNano", "endTimeUnixNano")
    if not any(key in span for key in keys):
        return None
    if not all(key in span for key in keys):
        raise ValueError("OTLP span timing requires both start and end")
    values = []
    for key in keys:
        value = span[key]
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
            value = int(value)
        if type(value) is not int or not 0 <= value <= 2**64 - 1:
            raise ValueError("OTLP span time must be an exact unsigned 64-bit integer")
        values.append(value)
    start, end = values
    if end < start:
        raise ValueError("OTLP span end precedes start")
    return {"start_unix_ns": start, "end_unix_ns": end, "duration_ns": end - start}


def decode_v3_trace(body: str, trace_id: str) -> dict[str, Any]:
    trace_id = _hex_id(trace_id, 32)
    processes: dict[str, dict[str, str]] = {}
    spans = []
    seen = set()
    for line in body.splitlines():
        if not line.strip():
            continue
        frame = json.loads(line)
        if "error" in frame:
            raise ValueError(f"Jaeger stream error: {frame['error']}")
        result = frame.get("result")
        if not isinstance(result, dict):
            raise ValueError("Jaeger v3 response missing result")
        for resource in result.get("resourceSpans", []):
            attrs = _attributes(resource.get("resource", {}).get("attributes", []))
            service = attrs.get("service.name")
            if not isinstance(service, str) or not service:
                raise ValueError("OTLP resource missing service.name")
            process_id = str(len(processes))
            processes[process_id] = {"serviceName": service}
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    actual_trace = _hex_id(span["traceId"], 32)
                    if actual_trace != trace_id:
                        raise ValueError("response contains a span from another trace")
                    span_id = _hex_id(span["spanId"], 16)
                    if span_id in seen:
                        raise ValueError("duplicate OTLP span ID")
                    seen.add(span_id)
                    attributes = _attributes(span.get("attributes", []))
                    kind = span.get("kind")
                    attributes["span.kind"] = {
                        2: "server", 3: "client",
                        "SPAN_KIND_SERVER": "server", "SPAN_KIND_CLIENT": "client",
                    }.get(kind, "other")
                    if span.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR"):
                        attributes["error"] = True
                    parent = span.get("parentSpanId")
                    references = []
                    if parent:
                        references.append({
                            "refType": "CHILD_OF", "traceID": trace_id,
                            "spanID": _hex_id(parent, 16),
                        })
                    spans.append({
                        "traceID": trace_id, "spanID": span_id, "processID": process_id,
                        "references": references,
                        "timing": _span_timing(span),
                        "tags": [{"key": key, "value": value} for key, value in attributes.items()],
                    })
    if not spans:
        raise ValueError("Jaeger v3 trace is empty")
    return {"data": [{"traceID": trace_id, "spans": spans, "processes": processes}]}
