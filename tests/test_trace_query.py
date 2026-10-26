import copy
import json

import pytest

from sentinelops.trace_query import decode_v3_trace
from scripts.step3_smoke_test import EXPECTED_SERVICES, validate_trace

TRACE_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def frames():
    # OTLP v3 JSON structure observed on the pinned Jaeger 2.21 container.
    resources = []
    for service, entries in (
        ("checkout-service", [("1", "9", 2, None), ("2", "1", 3, "inventory-service"), ("4", "1", 3, "payment-service")]),
        ("inventory-service", [("3", "2", 2, None)]),
        ("payment-service", [("5", "4", 2, None)]),
    ):
        spans = []
        for span_id, parent, kind, dependency in entries:
            attrs = [{"key": "http.response.status_code", "value": {"intValue": "200"}}]
            attrs.append({"key": "sentinelops.correlation_id", "value": {"stringValue": "request-a"}})
            if dependency:
                attrs.append({"key": "peer.service", "value": {"stringValue": dependency}})
            spans.append({
                "traceId": TRACE_ID, "spanId": span_id.zfill(16), "parentSpanId": parent.zfill(16),
                "kind": kind, "attributes": attrs, "status": {},
            })
        resources.append({
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
            "scopeSpans": [{"spans": spans}],
        })
    return [{"result": {"resourceSpans": [resource]}} for resource in resources]


def body(frames):
    return "\n".join(json.dumps(frame) for frame in frames)


def test_otlp_stream_decodes_all_resources_and_validates_client_parentage(frames):
    decoded = decode_v3_trace(body(frames), TRACE_ID)
    assert len(decoded["data"][0]["spans"]) == 5
    servers = validate_trace(decoded, TRACE_ID, "request-a", {s: 200 for s in EXPECTED_SERVICES})
    assert set(servers) == EXPECTED_SERVICES
    assert all(span["timing"] is None for span in decoded["data"][0]["spans"])
    with pytest.raises(ValueError, match="lacks native span timing"):
        validate_trace(decoded, TRACE_ID, "request-a", {s: 200 for s in EXPECTED_SERVICES}, require_timing=True)


def test_exact_nanosecond_timing_and_zero_duration_survive_string_and_integer_encoding(frames):
    start = 1792984632890188101
    for frame in frames:
        for span in frame["result"]["resourceSpans"][0]["scopeSpans"][0]["spans"]:
            span.update(startTimeUnixNano=str(start), endTimeUnixNano=start + 1)
    # Exact one-nanosecond spans would collapse under a float timestamp round trip.
    decoded = decode_v3_trace(body(frames), TRACE_ID)
    assert all(span["timing"] == {"start_unix_ns": start, "end_unix_ns": start+1, "duration_ns": 1}
               for span in decoded["data"][0]["spans"])
    validate_trace(decoded, TRACE_ID, "request-a", {s: 200 for s in EXPECTED_SERVICES}, require_timing=True)
    first = frames[0]["result"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    first.update(startTimeUnixNano=0, endTimeUnixNano="0")
    assert decode_v3_trace(body(frames), TRACE_ID)["data"][0]["spans"][0]["timing"]["duration_ns"] == 0
    decoded["data"][0]["spans"][0]["timing"]["duration_ns"] = 0
    with pytest.raises(ValueError, match="inconsistent native span timing"):
        validate_trace(decoded, TRACE_ID, "request-a", {s: 200 for s in EXPECTED_SERVICES}, require_timing=True)


@pytest.mark.parametrize(("start", "end"), [(1, None), (None, 1), (True, 2), (1.5, 2), (-1, 2),
    (1, 2**64), ("1e18", "2"), ("", "2"), (3, 2), ("NaN", "2"), ("1.0", "2"), (None, None)])
def test_declared_invalid_or_partial_timing_is_not_silently_zeroed(frames, start, end):
    span = frames[0]["result"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span.update(startTimeUnixNano=start, endTimeUnixNano=end)
    if start is None and end is not None: span.pop("startTimeUnixNano")
    if end is None and start is not None: span.pop("endTimeUnixNano")
    with pytest.raises(ValueError): decode_v3_trace(body(frames), TRACE_ID)


@pytest.mark.parametrize("mutation", ["another_trace", "duplicate", "zero_span", "no_service", "stream_error"])
def test_otlp_stream_rejects_corrupt_or_partial_success(frames, mutation):
    span = frames[0]["result"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    if mutation == "another_trace":
        span["traceId"] = "f" * 32
    elif mutation == "duplicate":
        frames.append(copy.deepcopy(frames[0]))
    elif mutation == "zero_span":
        span["spanId"] = "0" * 16
    elif mutation == "no_service":
        frames[0]["result"]["resourceSpans"][0]["resource"]["attributes"] = []
    else:
        frames.append({"error": {"message": "storage read interrupted"}})
    with pytest.raises(ValueError):
        decode_v3_trace(body(frames), TRACE_ID)


def test_5xx_requires_error_status_on_server_and_client(frames):
    for frame in frames:
        resource = frame["result"]["resourceSpans"][0]
        service = resource["resource"]["attributes"][0]["value"]["stringValue"]
        for span in resource["scopeSpans"][0]["spans"]:
            if service == "payment-service" or span["spanId"] in ("1".zfill(16), "4".zfill(16)):
                span["attributes"][0]["value"]["intValue"] = "502" if span["spanId"] == "1".zfill(16) else "503"
                span["status"] = {"code": 2}
    statuses = {"checkout-service": 502, "payment-service": 503, "inventory-service": 200}
    validate_trace(decode_v3_trace(body(frames), TRACE_ID), TRACE_ID, "request-a", statuses)
    frames[2]["result"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["status"] = {}
    with pytest.raises(ValueError, match="missing error span status"):
        validate_trace(decode_v3_trace(body(frames), TRACE_ID), TRACE_ID, "request-a", statuses)
