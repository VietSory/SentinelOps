"""Customer-impact severity shared by continuous incidents and RCA replay."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
import json
import logging
import math
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.incidents.health import validate_policy as validate_health_policy
from sentinelops.telemetry.core import stable_id

LOGGER = logging.getLogger(__name__)
CONFIGS = Path(__file__).resolve().parents[3] / "configs"
CHECKOUT = "checkout-service"


@dataclass(frozen=True)
class SeverityPolicy:
    version: str
    latency_min_ms: float
    error_rate: float
    critical_error_rate: float
    high_latency_ms: float
    low_latency_max_ms: float
    independent_roots: int
    consecutive_buckets: int
    bucket_seconds: int
    sha256: str


@cache
def load_policy() -> SeverityPolicy:
    """Load once per process; include the referenced SLO thresholds in the hash."""
    # Source checkouts keep configs beside src; installed workers use the
    # application's working directory (e.g. /opt/sentinelops/configs).
    configs = CONFIGS if (CONFIGS / "severity_policy.json").is_file() else Path.cwd() / "configs"
    policy = json.loads((configs / "severity_policy.json").read_text(encoding="utf-8"))
    health = json.loads((configs / "slo_health_policy.json").read_text(encoding="utf-8"))
    validate_health_policy(health)
    expected = {"policy_version", "critical_checkout_error_rate", "high_checkout_latency_ms",
                "low_latency_max_ms", "independent_structural_roots", "consecutive_buckets", "bucket_seconds"}
    if set(policy) != expected or policy["policy_version"] != "severity/v2":
        raise ValueError("unsupported severity policy")
    for key in ("critical_checkout_error_rate", "high_checkout_latency_ms", "low_latency_max_ms"):
        if type(policy[key]) not in (int, float) or not math.isfinite(policy[key]):
            raise ValueError("severity thresholds must be finite numbers")
    if not (health["error_rate"] < policy["critical_checkout_error_rate"] <= 1
            and health["latency_ms"] < policy["low_latency_max_ms"] < policy["high_checkout_latency_ms"]):
        raise ValueError("severity thresholds must be ordered")
    if any(type(policy[key]) is not int or policy[key] != value for key, value in
           (("independent_structural_roots", 2), ("consecutive_buckets", 2), ("bucket_seconds", 60))):
        raise ValueError("unsupported severity persistence/structural profile")
    return SeverityPolicy(policy["policy_version"], health["latency_ms"], health["error_rate"],
                          policy["critical_checkout_error_rate"], policy["high_checkout_latency_ms"],
                          policy["low_latency_max_ms"], policy["independent_structural_roots"],
                          policy["consecutive_buckets"], policy["bucket_seconds"],
                          stable_id({"severity_policy": policy, "slo_health_policy": health}))


def _eligible(observation):
    if observation["quality"]["eligible_for_inference"] is not True:
        return False
    payload = observation["payload"]
    latency, errors = payload["p95_latency_ms"], payload["error_rate"]
    return (type(latency) in (int, float) and math.isfinite(latency) and latency >= 0
            and type(errors) in (int, float) and math.isfinite(errors) and 0 <= errors <= 1)


def group_severity(telemetry, group, signals):
    """Classify verified opening evidence without changing incident contracts.

    Structural candidates in a verified correlation group are independent roots,
    not the count of impacted services. UNKNOWN evidence cannot establish low.
    Critical retains the existing last-two eligible, adjacent 60s bucket rule.
    """
    policy = load_policy()
    LOGGER.info("severity_policy_sha256=%s policy_version=%s", policy.sha256, policy.version)
    observations = defaultdict(dict)
    for signal_id in group["signal_ids"]:
        _, signal = signals[signal_id]
        for ref in signal["evidence_refs"]:
            observations[signal["service"]][ref] = telemetry.read(ref)
    by_service = {service: sorted(items.values(), key=lambda item: utc_time(item["bucket_end"]))
                  for service, items in observations.items()}
    checkout = by_service.get(CHECKOUT, [])
    pair = checkout[-policy.consecutive_buckets:]
    consecutive = (len(pair) == policy.consecutive_buckets and all(_eligible(item) for item in pair)
                   and utc_time(pair[-1]["bucket_end"]) - utc_time(pair[-2]["bucket_end"])
                   == timedelta(seconds=policy.bucket_seconds))
    if consecutive and all(item["payload"]["error_rate"] >= policy.critical_error_rate for item in pair):
        return "critical"
    if (len(set(group["structural_candidates"])) >= policy.independent_roots
            or consecutive and all(item["payload"]["error_rate"] >= policy.error_rate
                                   or item["payload"]["p95_latency_ms"] > policy.high_latency_ms for item in pair)):
        return "high"
    # Even a single high/unknown Checkout bucket prevents a low classification.
    if CHECKOUT in group["affected_services"] and (not checkout or any(
            not _eligible(item) or item["payload"]["error_rate"] >= policy.error_rate
            or item["payload"]["p95_latency_ms"] > policy.high_latency_ms for item in checkout)):
        return "medium"
    services = set(group["affected_services"]) - {CHECKOUT}
    if not group["affected_services"]:
        return "medium"
    for service in services:
        items = by_service.get(service, [])
        if not items or not all(_eligible(item) for item in items):
            return "medium"
        if not (policy.latency_min_ms < max(item["payload"]["p95_latency_ms"] for item in items)
                <= policy.low_latency_max_ms and max(item["payload"]["error_rate"] for item in items) <= policy.error_rate):
            return "medium"
    return "low"
