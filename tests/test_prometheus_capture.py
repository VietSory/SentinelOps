from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import PrometheusSource, decode_capture, queries
from sentinelops.telemetry.store import ArtifactStore

END = datetime(2026, 10, 26, 0, 10, tzinfo=timezone.utc)


def source_response(key, end=END):
    values = {"requests": [({}, 120)], "errors": [({}, 12)],
              "buckets": [({"le": "0"}, 0), ({"le": "100"}, 108), ({"le": "200"}, 120), ({"le": "+Inf"}, 120)],
              "scrapes": [({}, 12)], "series_scrapes": [({}, 12)], "up": [({}, 1)]}
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": labels, "value": [end.timestamp(), str(value)]} for labels, value in values[key]
    ]}}


def capture(end=END):
    catalog = queries("payment-service")

    def handle(request):
        assert float(request.url.params["time"]) == end.timestamp()
        key = next(key for key, expression in catalog.items() if expression == request.url.params["query"])
        return httpx.Response(200, json=source_response(key, end))

    source = PrometheusSource(transport=httpx.MockTransport(handle))
    try:
        return source.capture("payment-service", end, now=end+timedelta(seconds=30))
    finally:
        source.close()


def test_pinned_queries_exclude_probes_and_use_reset_aware_per_series_increases():
    query = queries("inventory-service")
    assert 'route="/v1/inventory/{sku}"' in query["requests"]
    assert 'sum(increase(' in query["requests"]
    assert 'sum by (le) (increase(' in query["buckets"]
    assert 'status_code=~"5.."' in query["errors"]
    with pytest.raises(ValueError):
        queries('rds')


def test_real_api_shape_capture_normalizes_counts_distribution_and_lineage(tmp_path):
    raw = capture()
    item = decode_capture(raw)
    assert item["payload"]["error_rate"] == .1
    assert item["payload"]["p95_latency_ms"] == 150
    store = ArtifactStore(tmp_path)
    assert store.write("raw", raw) == item["provenance"]["source_refs"][0]
    assert store.read(item["provenance"]["source_refs"][0]) == raw


@pytest.mark.parametrize("mutation", ["warning", "future", "nan", "wrong_catalog", "missing", "down"])
def test_source_faults_never_become_valid_healthy_buckets(mutation):
    raw = capture()
    if mutation == "warning":
        raw["queries"]["requests"]["response"]["warnings"] = ["partial data"]
    elif mutation == "future":
        raw["queries"]["requests"]["response"]["data"]["result"][0]["value"][0] += 1
    elif mutation == "nan":
        raw["queries"]["requests"]["response"]["data"]["result"][0]["value"][1] = "NaN"
    elif mutation == "wrong_catalog":
        raw["queries"]["requests"]["expression"] = 'sum(up)'
    elif mutation == "missing":
        raw["queries"].pop("errors")
    else:
        raw["queries"]["up"]["response"]["data"]["result"][0]["value"][1] = "0"
        assert decode_capture(raw)["payload"] is None
        return
    with pytest.raises(ValueError):
        decode_capture(raw)


def test_future_or_unaligned_capture_is_blocked_before_http():
    source = PrometheusSource(transport=httpx.MockTransport(lambda request: pytest.fail("unexpected networking")))
    try:
        for end, now in ((END, END), (END+timedelta(seconds=1), END+timedelta(seconds=60))):
            with pytest.raises(ValueError):
                source.capture("payment-service", end, now=now)
    finally:
        source.close()


def test_old_15_second_profile_remains_replayable_without_reinterpreting_coverage():
    raw = capture()
    raw["source_profile"] = "prometheus-increase-60s/v1"
    for key in ("scrapes", "series_scrapes"):
        raw["queries"][key]["response"]["data"]["result"][0]["value"][1] = "4"
    assert decode_capture(raw)["quality"]["coverage_ratio"] == 1


@pytest.mark.parametrize("mode", ["http_error", "transport_error", "warning"])
def test_actual_fetch_retains_failed_source_capture_for_quarantine(mode, tmp_path):
    def handle(request):
        if mode == "http_error":
            return httpx.Response(503, text="not available")
        if mode == "transport_error":
            raise httpx.ConnectError("not reachable", request=request)
        value = source_response("requests")
        value["warnings"] = ["partial results"]
        return httpx.Response(200, json=value)
    source = PrometheusSource(transport=httpx.MockTransport(handle))
    try:
        raw = source.capture("payment-service", END, now=END+timedelta(seconds=30))
    finally:
        source.close()
    result = process_captures([raw], ArtifactStore(tmp_path))
    assert len(result["raw_refs"]) == 1
    assert len(result["quarantine_refs"]) == 1
    assert result["observation_count"] == result["eligible_feature_count"] == 0


def test_saved_capture_replay_is_immutable_order_independent_and_deduplicated(tmp_path):
    captures = [capture(END-timedelta(minutes=i)) for i in range(5)]
    store = ArtifactStore(tmp_path)
    first = process_captures(captures + [captures[0]], store)
    second = process_captures(list(reversed(captures)), store)
    assert first == second
    assert first["eligible_feature_count"] >= 1
    saved = store.read(first["manifest_ref"])
    assert process_captures([store.read(ref) for ref in saved["raw_refs"]], store) == first
    corrupt = deepcopy(captures[0])
    corrupt["queries"]["requests"]["expression"] = "invalid"
    result = process_captures([corrupt], store)
    assert len(result["quarantine_refs"]) == 1
    assert result["observation_count"] == 0


def test_artifact_store_rejects_corruption_traversal_and_nonfinite_data(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = store.write("raw", {"data": 1})
    assert store.write("raw", {"data": 1}) == reference
    path = tmp_path / reference.removeprefix("artifact://")
    path.write_text("broken")
    with pytest.raises(ValueError, match="checksum"):
        store.read(reference)
    with pytest.raises(ValueError, match="corrupt"):
        store.write("raw", {"data": 1})
    with pytest.raises(ValueError):
        store.read("artifact://raw/../../secret")
    with pytest.raises(ValueError):
        store.write("raw", {"data": float("nan")})


def test_v2_profile_adds_latency_sum_count_and_leaves_v1_unchanged():
    from sentinelops.telemetry.prometheus import SOURCE_PROFILE, SOURCE_PROFILE_V2, mean_latency_ms, queries
    v1, v2 = queries('payment-service'), queries('payment-service', SOURCE_PROFILE_V2)
    assert set(v2) - set(v1) == {'duration_sum', 'duration_count'} and all(v2[k] == v1[k] for k in v1)
    def resp(value):
        return {'status': 'success', 'data': {'resultType': 'vector', 'result': [] if value is None else
                [{'metric': {}, 'value': [1762600000, str(value)]}]}}
    capture = {'source_profile': SOURCE_PROFILE_V2, 'service': 'payment-service', 'bucket_end': '2025-11-08T12:26:40Z',
               'queries': {'duration_sum': {'expression': v2['duration_sum'], 'response': resp(600.0)},
                           'duration_count': {'expression': v2['duration_count'], 'response': resp(120.0)}}}
    assert mean_latency_ms(capture) == 5.0
    capture['queries']['duration_count']['response'] = resp(0.0)
    assert mean_latency_ms(capture) is None
    assert mean_latency_ms({**capture, 'source_profile': SOURCE_PROFILE}) is None
