from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_KEYS, FEATURE_SET, timestamp


@dataclass(frozen=True)
class SLOConfig:
    latency_ms: float = 750.
    error_rate: float = .05
    minimum_requests: int = 30
    persistence: int = 2

    def validate(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, (int,float)) for value in (self.latency_ms,self.error_rate)):
            raise ValueError("SLO thresholds must be numeric")
        if type(self.minimum_requests) is not int or type(self.persistence) is not int:
            raise ValueError("SLO count/persistence must be integers")
        if not math.isfinite(self.latency_ms) or self.latency_ms <= 0 or not 0 < self.error_rate < 1:
            raise ValueError("invalid SLO thresholds")
        if self.minimum_requests < 1 or self.persistence < 1:
            raise ValueError("invalid SLO count/persistence")


def percentile(values: list[float], quantile: float) -> float:
    if not values or not 0 <= quantile <= 1 or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid request durations")
    ordered = sorted(values)
    position = (len(ordered)-1)*quantile
    lower = int(position)
    fraction = position-lower
    return ordered[lower] if lower == len(ordered)-1 else ordered[lower]*(1-fraction)+ordered[lower+1]*fraction


def measure_slo(records: list[dict[str, Any]], service: str, config: SLOConfig = SLOConfig(), *,
                start_at: datetime | None = None, end_at: datetime | None = None) -> dict[str, Any]:
    """Ground truth from unsampled SERVER completion logs, never model output.

    The onset is the start of the first violating bucket in a sustained sequence;
    confirmation is the end of its final required bucket. Keep both timestamps.
    """
    config.validate()
    if service not in BUSINESS_ROUTES:
        raise ValueError("unknown service")
    method, route = BUSINESS_ROUTES[service]
    seen = {}
    buckets = defaultdict(list)
    for item in records:
        if item.get("service") != service or item.get("event") != "request_completed":
            continue
        if item.get("route") != route or item.get("method") != method:
            continue
        identifier = item.get("request_instance_id")
        if not identifier:
            raise ValueError("ground truth needs stable request-instance IDs")
        fact = (item["timestamp_unix_ms"], item["status_code"], item["duration_ms"])
        if identifier in seen:
            if seen[identifier] != fact:
                raise ValueError("conflicting duplicate request log")
            continue
        seen[identifier] = fact
        milliseconds, status, duration = fact
        if type(milliseconds) is not int or milliseconds < 0 or type(status) is not int or not 100 <= status <= 599:
            raise ValueError("invalid request ground-truth timestamp/status")
        if isinstance(duration, bool) or not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration < 0:
            raise ValueError("invalid request ground-truth duration")
        buckets[milliseconds//60_000*60].append((status, duration))
    measured = []
    first, confirmed, sequence_start, streak, previous = None, None, None, 0, None
    for start, requests in sorted(buckets.items()):
        count = len(requests)
        p95 = percentile([duration for _,duration in requests], .95)
        ratio = sum(status >= 500 for status,_ in requests)/count
        eligible = count >= config.minimum_requests
        if start_at and start < start_at.timestamp():
            eligible = False
        if end_at and start+60 > end_at.timestamp():
            eligible = False
        breach = eligible and (p95 > config.latency_ms or ratio > config.error_rate)
        if breach:
            if previous is None or start != previous+60 or streak == 0:
                streak, sequence_start = 1, start
            else:
                streak += 1
            if first is None and streak >= config.persistence:
                first, confirmed = sequence_start, start+60
        else:
            streak, sequence_start = 0, None
        previous = start
        measured.append({"bucket_start":timestamp(datetime.fromtimestamp(start, timezone.utc)),
                         "request_count":count,"p95_latency_ms":p95,"error_rate":ratio,
                         "eligible":eligible,"breach":breach})
    return {
        "first_violation_at": timestamp(datetime.fromtimestamp(first, timezone.utc)) if first is not None else None,
        "confirmed_at": timestamp(datetime.fromtimestamp(confirmed, timezone.utc)) if confirmed is not None else None,
        "request_count":len(seen), "buckets":measured,
    }


def feature_vector(features: dict[str, Any]) -> list[float]:
    if set(features) != set(FEATURE_KEYS):
        raise ValueError("features must exactly match the registered feature set; metadata/labels are forbidden")
    values = [features[key] for key in FEATURE_KEYS]
    if any(isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) for value in values):
        raise ValueError("feature vector must contain finite numeric values")
    return [float(value) for value in values]


def label_feature(feature: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    validate_payload("experiment-run.schema.json", run)
    validate_payload("feature-envelope.schema.json", feature)
    if run["status"] != "completed" or feature["payload"] is None:
        return None
    if feature["source_profile"] != run["source_profile"] or feature["environment"] != run["environment"]:
        raise ValueError("run/feature source or environment mismatch")
    if feature["feature_set_version"] != FEATURE_SET:
        raise ValueError("unknown feature set")
    start, end = utc_time(feature["window_start"]), utc_time(feature["window_end"])
    if end <= utc_time(run["warmup_end_at"]):
        return None
    if start < utc_time(run["started_at"]) or end > utc_time(run["ended_at"]):
        raise ValueError("feature outside experiment bounds")
    service = feature["service"]
    slo = run["slo_by_service"][service]
    if slo["request_count"] == 0:
        return None
    onset = utc_time(slo["first_violation_at"]) if slo["first_violation_at"] else None
    fault = utc_time(run["fault_start_at"]) if run["fault_start_at"] else None
    recovery = utc_time(run["recovery_start_at"]) if run["recovery_start_at"] else None
    affected = service in run["affected_services_ground_truth"]
    healthy = False
    risk = None
    if run["scenario_id"] == "S0" or not affected:
        if onset is None:
            healthy, risk = True, 0
    elif fault and end <= fault and (onset is None or end <= onset):
        healthy, risk = True, 0
    elif run["scenario_id"] == "S3" and fault and end > fault and (onset is None or end < onset):
        if recovery is None or end < recovery:
            risk = 1
    # Abrupt/post-incident/recovery windows are not mislabeled as precursors.
    feature_vector(feature["payload"]["features"])
    return {
        "run_id":run["run_id"],"session_id":run["session_id"],"declared_split":run["declared_split"],
        "service":service,"window_start":feature["window_start"],"window_end":feature["window_end"],
        "feature_set_version":FEATURE_SET,"source_profile":feature["source_profile"],
        "features":feature["payload"]["features"],"is_healthy":healthy,"risk_label":risk,
        "feature_record_id":feature["record_id"],
    }
