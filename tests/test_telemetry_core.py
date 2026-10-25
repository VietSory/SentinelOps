from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.telemetry.core import BucketMetrics, build_feature_window, histogram_quantile, normalize_bucket

END = datetime(2026, 10, 26, 0, 10, tzinfo=timezone.utc)


def observation(index=0, *, count=60, errors=0, coverage=1, bound=100, delay=30):
    end = END - timedelta(minutes=4-index)
    return normalize_bucket(BucketMetrics(
        "payment-service", end, count, errors, ((0, 0), (bound, count), (math.inf, count)),
        coverage, end + timedelta(seconds=delay), (f"capture://fixture/{index}",),
    ))


def window(items, *, as_of=END+timedelta(seconds=30)):
    return build_feature_window(items, service="payment-service", window_end=END, as_of=as_of)


def test_histogram_interpolation_and_overflow_are_explicit():
    assert histogram_quantile(((0, 0), (100, 90), (200, 100), (math.inf, 100))) == 150
    with pytest.raises(ValueError, match="overflow"):
        histogram_quantile(((100, 80), (math.inf, 100)))
    for buckets in (((100, 10), (50, 20), (math.inf, 20)), ((100, 20), (math.inf, 10)), ((100, math.nan), (math.inf, 20))):
        with pytest.raises(ValueError):
            histogram_quantile(buckets)


@pytest.mark.parametrize(("count", "errors", "coverage", "status"), [
    (0, 0, 1, "no_traffic"), (10, 0, 1, "insufficient_traffic"),
    (60, 0, .5, "incomplete"), (None, None, 1, "incomplete"),
    (60, 70, 1, "invalid"), (math.nan, 0, 1, "invalid"),
])
def test_missing_and_invalid_data_never_becomes_healthy_payload(count, errors, coverage, status):
    item = observation(count=count, errors=errors, coverage=coverage)
    assert item["payload"] is None
    assert item["quality"]["status"] == status
    assert item["quality"]["eligible_for_inference"] is False


def test_weighted_ratios_and_real_time_slopes():
    items = [observation(i, count=60*(i+1), errors=6*i, bound=100*(i+1)) for i in range(5)]
    result = window(items)
    features = result["payload"]["features"]
    assert features["request_rate_mean_5m"] == 3
    assert features["error_rate_mean_5m"] == pytest.approx(60/900)
    assert features["p95_latency_ms_mean_5m"] == 285
    assert features["p95_latency_ms_slope_5m"] == pytest.approx(95)
    # A missing middle bucket does not compress the remaining four timestamps.
    sparse = window([items[0], items[1], items[3], items[4]])
    assert sparse["quality"]["coverage_ratio"] == .8
    assert sparse["payload"]["features"]["p95_latency_ms_slope_5m"] == pytest.approx(95)


def test_three_buckets_abstain_and_available_time_is_respected():
    items = [observation(i) for i in range(5)]
    assert window(items[:3])["payload"] is None
    # Final two observations are late; they cannot enter a historical serving replay.
    for item in items[3:]:
        item["available_at"] = item["ingested_at"] = (END+timedelta(minutes=2)).isoformat()
    result = window(items)
    assert result["payload"] is None
    assert len(result["provenance"]["input_refs"]) == 3


def test_future_buckets_and_other_services_do_not_enter_current_features():
    items = [observation(i) for i in range(5)]
    first = window(items)
    assert window(items + [observation(5)], as_of=END+timedelta(minutes=2))["record_id"] == first["record_id"]
    assert window(list(reversed(items)) + [items[0]])["record_id"] == first["record_id"]


def test_conflicting_revisions_and_mixed_sources_are_rejected():
    items = [observation(i) for i in range(5)]
    with pytest.raises(ValueError, match="conflicting"):
        window(items + [observation(0, bound=200)])
    mixed = deepcopy(items)
    mixed[0]["source_profile"] = "other/v1"
    with pytest.raises(ValueError, match="mixed"):
        window(mixed)


def test_envelopes_validate_nested_payload_and_exact_alignment():
    item = observation()
    item["payload"]["service"] = "checkout-service"
    with pytest.raises(ValueError, match="mismatch"):
        validate_payload("observation-envelope.schema.json", item)
    raw = BucketMetrics("payment-service", END+timedelta(microseconds=1), 60, 0,
                        ((100, 60), (math.inf, 60)), 1, END+timedelta(seconds=30), ("fixture://raw",))
    with pytest.raises(ValueError, match="aligned"):
        normalize_bucket(raw)
