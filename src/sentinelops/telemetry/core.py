from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

from sentinelops.contracts import utc_time, validate_payload

FEATURE_SET = "http-red-5m/v1"
BUSINESS_ROUTES = {
    "checkout-service": ("POST", "/v1/checkout"),
    "payment-service": ("POST", "/v1/payments/charge"),
    "inventory-service": ("GET", "/v1/inventory/{sku}"),
}
FEATURE_KEYS = (
    "request_rate_mean_5m", "p95_latency_ms_mean_5m", "p95_latency_ms_slope_5m",
    "error_rate_mean_5m", "error_rate_slope_5m",
)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("expected timezone-aware UTC")
    return value.isoformat().replace("+00:00", "Z")


def stable_id(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def histogram_quantile(buckets: tuple[tuple[float, float], ...], quantile: float = .95) -> float:
    """Linear interpolation in a cumulative classic histogram, in milliseconds.

    Reject overflow instead of inventing a latency above the final finite bound.
    """
    if not 0 < quantile < 1 or len(buckets) < 2:
        raise ValueError("invalid histogram quantile/buckets")
    previous_bound, previous_count = -1.0, 0.0
    for bound, count in buckets:
        if math.isnan(bound) or bound < 0 or bound <= previous_bound:
            raise ValueError("histogram bounds must increase")
        if not math.isfinite(count) or count < previous_count:
            raise ValueError("histogram counts must be finite and cumulative")
        previous_bound, previous_count = bound, count
    if not math.isinf(buckets[-1][0]) or buckets[-1][1] <= 0:
        raise ValueError("histogram needs a nonempty +Inf bucket")
    target = buckets[-1][1] * quantile
    previous_bound, previous_count = 0.0, 0.0
    for bound, count in buckets:
        if count >= target:
            if not math.isfinite(bound):
                raise ValueError("histogram percentile overflow")
            if count == previous_count:
                return bound
            return previous_bound + (bound - previous_bound) * (target - previous_count) / (count - previous_count)
        previous_bound, previous_count = bound, count
    raise ValueError("histogram percentile unavailable")


@dataclass(frozen=True)
class BucketMetrics:
    service: str
    end: datetime
    request_count: float | None
    error_count: float | None
    buckets: tuple[tuple[float, float], ...] | None
    coverage_ratio: float
    available_at: datetime
    source_refs: tuple[str, ...]
    source_profile: str = "prometheus-increase-60s/v1"


def normalize_bucket(raw: BucketMetrics, *, environment: str = "local", minimum_requests: int = 30) -> dict[str, Any]:
    if raw.service not in BUSINESS_ROUTES:
        raise ValueError("unregistered business service")
    end = timestamp(raw.end)
    available = timestamp(raw.available_at)
    reasons = []
    coverage = raw.coverage_ratio
    if not math.isfinite(coverage) or not 0 <= coverage <= 1:
        coverage = 0.0
        reasons.append("invalid_coverage")
    counts = (raw.request_count, raw.error_count)
    invalid = any(value is not None and (not math.isfinite(value) or value < 0) for value in counts)
    count = raw.request_count if not invalid else None
    errors = raw.error_count if not invalid else None
    p95 = None
    status = "valid"
    if invalid or (count is not None and errors is not None and errors > count + 1e-6):
        status = "invalid"
        reasons.append("invalid_counts")
    elif count is None or errors is None or coverage < .8:
        status = "incomplete"
        reasons.append("missing_counts_or_coverage")
    elif count == 0:
        status = "no_traffic"
        reasons.append("no_business_requests")
    elif count < minimum_requests:
        status = "insufficient_traffic"
        reasons.append("insufficient_business_requests")
    else:
        try:
            if not raw.buckets:
                raise ValueError("missing_histogram")
            p95 = histogram_quantile(raw.buckets)
            if abs(raw.buckets[-1][1] - count) > max(1.0, count * .02):
                raise ValueError("histogram_request_count_mismatch")
        except ValueError as exc:
            status = "invalid"
            reasons.append(str(exc))
    payload = None
    if status == "valid":
        payload = {
            "schema_version": "1.0", "timestamp": end, "service": raw.service,
            "p95_latency_ms": p95, "error_rate": errors / count,
        }
    envelope = {
        "envelope_version": "1.0", "environment": environment,
        "service": raw.service, "source_profile": raw.source_profile,
        "bucket_start": timestamp(raw.end - timedelta(seconds=60)), "bucket_end": end,
        "available_at": available, "ingested_at": available, "payload": payload,
        "measurements": {"request_count": count, "error_count": errors},
        "quality": {"status": status, "coverage_ratio": coverage,
                    "eligible_for_inference": status == "valid", "reasons": reasons},
        "provenance": {"source_refs": list(raw.source_refs)},
    }
    # Availability timestamps are audit metadata, not observation identity.
    envelope["record_id"] = stable_id({k: v for k, v in envelope.items() if k not in ("available_at", "ingested_at")})
    validate_payload("observation-envelope.schema.json", envelope)
    return envelope


def _slope(points: list[tuple[float, float]]) -> float:
    x_mean = sum(x for x, _ in points) / len(points)
    y_mean = sum(y for _, y in points) / len(points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    return sum((x - x_mean) * (y - y_mean) for x, y in points) / denominator


def build_feature_window(observations: list[dict[str, Any]], *, service: str,
                         window_end: datetime, as_of: datetime,
                         environment: str = "local", source_profile: str = "prometheus-increase-60s/v1") -> dict[str, Any]:
    if service not in BUSINESS_ROUTES:
        raise ValueError("unregistered business service")
    end = timestamp(window_end)
    cutoff = timestamp(as_of)
    if as_of < window_end:
        raise ValueError("window is not closed")
    start_time = window_end - timedelta(seconds=300)
    selected = {}
    for item in observations:
        validate_payload("observation-envelope.schema.json", item)
        if item["service"] != service:
            continue
        observed = utc_time(item["bucket_end"])
        if not start_time < observed <= window_end or utc_time(item["available_at"]) > as_of:
            continue
        if item["environment"] != environment or item["source_profile"] != source_profile:
            raise ValueError("mixed environment/source profiles")
        if observed in selected and selected[observed]["record_id"] != item["record_id"]:
            raise ValueError("conflicting observation revisions require explicit resolution")
        selected[observed] = item
    accepted = sorted((time, item) for time, item in selected.items() if item["quality"]["eligible_for_inference"])
    coverage = len(accepted) / 5
    payload = None
    request_count = sum(item["measurements"]["request_count"] for _, item in accepted)
    if len(accepted) >= 4 and request_count > 0:
        latency = [((time - start_time).total_seconds() / 60, item["payload"]["p95_latency_ms"]) for time, item in accepted]
        errors = [((time - start_time).total_seconds() / 60, item["payload"]["error_rate"]) for time, item in accepted]
        features = {
            "request_rate_mean_5m": request_count / (len(accepted) * 60),
            "p95_latency_ms_mean_5m": sum(y for _, y in latency) / len(latency),
            "p95_latency_ms_slope_5m": _slope(latency),
            "error_rate_mean_5m": sum(item["measurements"]["error_count"] for _, item in accepted) / request_count,
            "error_rate_slope_5m": _slope(errors),
        }
        payload = {"schema_version": "1.0", "service": service,
                   "window_start": timestamp(start_time), "window_end": end, "features": features}
    envelope = {
        "envelope_version": "1.0", "feature_set_version": FEATURE_SET,
        "environment": environment, "source_profile": source_profile, "service": service,
        "window_start": timestamp(start_time), "window_end": end, "as_of": cutoff,
        "payload": payload,
        "quality": {"coverage_ratio": coverage, "request_count": request_count,
                    "eligible_for_inference": payload is not None,
                    "reasons": [] if payload is not None else ["insufficient_eligible_buckets"]},
        "provenance": {"input_refs": ["observation://" + item["record_id"] for _, item in sorted(selected.items())]},
    }
    envelope["record_id"] = stable_id({k: v for k, v in envelope.items() if k != "as_of"})
    validate_payload("feature-envelope.schema.json", envelope)
    return envelope
