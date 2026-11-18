"""Original customer-impact policy (CONSTRAINTS_AND_GATES §4), live and replay."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import cache
import json
import logging
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id

LOGGER = logging.getLogger(__name__)
CONFIGS = Path(__file__).resolve().parents[3] / "configs"
CHECKOUT = "checkout-service"


@dataclass(frozen=True)
class SeverityPolicy:
    version: str
    critical_error_rate: float
    high_min_affected_services: int
    consecutive_buckets: int
    bucket_seconds: int
    sha256: str


@cache
def load_policy() -> SeverityPolicy:
    """Load the frozen original policy; no latency bands or demo calibration."""
    configs = CONFIGS if (CONFIGS / "severity_policy.json").is_file() else Path.cwd() / "configs"
    policy = json.loads((configs / "severity_policy.json").read_text(encoding="utf-8"))
    expected = {
        "policy_version": "severity/v1",
        "critical_checkout_error_rate": .20,
        "high_checkout_impacted": True,
        "high_min_affected_services": 2,
        "fallback_severity": "medium",
        "consecutive_buckets": 2,
        "bucket_seconds": 60,
    }
    if (not isinstance(policy, dict) or policy != expected
            or any(type(policy[key]) is not type(value) for key, value in expected.items())):
        raise ValueError("unsupported severity policy; expected frozen severity/v1")
    return SeverityPolicy(policy["policy_version"], policy["critical_checkout_error_rate"],
                          policy["high_min_affected_services"], policy["consecutive_buckets"],
                          policy["bucket_seconds"], stable_id(policy))


def group_severity(telemetry, group, signals):
    """Restore group_severity before d1eee79; severity grants no action authority.

    Critical requires Checkout errors >=20% in the latest two eligible adjacent
    60s buckets. Any Checkout impact or >=2 affected services is high; otherwise
    medium. LOW remains a valid watch-level label, not an objective incident band.
    """
    policy = load_policy()
    LOGGER.info("severity_policy_sha256=%s policy_version=%s", policy.sha256, policy.version)
    critical = False
    for signal_id in group["signal_ids"]:
        _, signal = signals[signal_id]
        if signal["service"] != CHECKOUT:
            continue
        observed = sorted((telemetry.read(ref) for ref in signal["evidence_refs"]),
                          key=lambda item: item["bucket_end"])
        pair = observed[-policy.consecutive_buckets:]
        critical = (len(pair) == policy.consecutive_buckets
                    and all(item["quality"]["eligible_for_inference"] is True
                            and item["payload"]["error_rate"] >= policy.critical_error_rate for item in pair)
                    and utc_time(pair[-1]["bucket_end"]) - utc_time(pair[-2]["bucket_end"])
                    == timedelta(seconds=policy.bucket_seconds))
    if critical:
        return "critical"
    if CHECKOUT in group["affected_services"] or len(group["affected_services"]) >= policy.high_min_affected_services:
        return "high"
    return "medium"
