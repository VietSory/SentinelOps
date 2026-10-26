"""Objective SLO health transitions; no model, fault truth or action authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import math

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp


def validate_policy(policy: dict) -> None:
    expected = {"policy_version", "latency_ms", "error_rate", "minimum_requests", "minimum_coverage",
                "entry_buckets", "recovery_buckets", "maximum_lag_seconds"}
    if not isinstance(policy, dict) or set(policy) != expected or policy["policy_version"] != "slo-health/local-v1":
        raise ValueError("unsupported SLO health policy")
    for key in ("latency_ms", "error_rate", "minimum_coverage"):
        if isinstance(policy[key], bool) or not isinstance(policy[key], (int, float)) or not math.isfinite(policy[key]):
            raise ValueError("policy thresholds must be finite numbers")
    if policy["latency_ms"] <= 0 or not 0 < policy["error_rate"] < 1 or not 0 < policy["minimum_coverage"] <= 1:
        raise ValueError("invalid SLO health thresholds")
    for key in ("minimum_requests", "entry_buckets", "recovery_buckets", "maximum_lag_seconds"):
        if type(policy[key]) is not int or not 1 <= policy[key] <= 3600:
            raise ValueError("policy count/delay must be a bounded positive integer")


def validate_signal(signal: dict) -> None:
    """Semantic checks after JSON Schema validation; no recursive schema call."""
    end, evaluated = utc_time(signal["bucket_end"]), utc_time(signal["evaluated_at"])
    if end.timestamp() % 60 or evaluated < end:
        raise ValueError("health signal needs a closed aligned UTC bucket")
    if signal["signal_id"] != stable_id({k: v for k, v in signal.items() if k != "signal_id"}):
        raise ValueError("health signal identity mismatch")
    unknown = signal["availability"] == "UNKNOWN"
    if unknown != (signal["health"] is None) or unknown != (signal["event"] == "data_unknown"):
        raise ValueError("unknown data cannot establish a health state")
    if unknown != bool(signal["reasons"]):
        raise ValueError("health availability disagrees with its quality reasons")
    if unknown and (signal["breach_streak"] or signal["clear_streak"]):
        raise ValueError("unknown data cannot contribute to persistence")
    if signal["breach_streak"] and signal["clear_streak"]:
        raise ValueError("breach and clear persistence cannot coexist")
    if len(signal["evidence_refs"]) != signal["breach_streak"] + signal["clear_streak"]:
        raise ValueError("health persistence must retain its supporting observations")
    if signal["incident_active"] and signal["episode_confirmed_at"] is None:
        raise ValueError("active incident requires explicit confirmation")
    if signal["health"] in ("incident", "recovering") and not signal["incident_active"]:
        raise ValueError("incident/recovering health requires an active incident")
    if signal["health"] in ("healthy", "degrading") and signal["incident_active"]:
        raise ValueError("active incident cannot project healthy/degrading")
    if signal["episode_confirmed_at"] is not None and signal["episode_start_at"] is None:
        raise ValueError("confirmed episode requires its first violating bucket")
    if signal["episode_start_at"] is not None:
        start = utc_time(signal["episode_start_at"])
        if start.timestamp() % 60 or start >= end:
            raise ValueError("episode onset must be a prior aligned bucket start")
    if signal["episode_confirmed_at"] is not None:
        confirmed = utc_time(signal["episode_confirmed_at"])
        if not utc_time(signal["episode_start_at"]) < confirmed <= evaluated:
            raise ValueError("episode confirmation time is inconsistent")
    if signal["event"] == "incident_opened" and not signal["incident_active"]:
        raise ValueError("opening event requires an active incident")
    if signal["event"] == "recovery_confirmed" and (signal["incident_active"] or signal["health"] != "healthy"):
        raise ValueError("recovery event cannot retain an active incident")
    if not unknown and (not signal["observation_ref"] or not signal["evidence_refs"]):
        raise ValueError("eligible health needs source evidence")
    if not unknown and signal["evidence_refs"][-1] != signal["observation_ref"]:
        raise ValueError("health evidence must end with the current observation")
    if signal["health"] in ("incident", "degrading") and not signal["breach_streak"]:
        raise ValueError("violating health requires breach persistence")
    if signal["health"] in ("healthy", "recovering") and not signal["clear_streak"]:
        raise ValueError("clean health requires clear persistence")
    if signal["event"] == "incident_opened" and signal["episode_confirmed_at"] != signal["evaluated_at"]:
        raise ValueError("opening confirmation must match its decision clock")


def advance_health(previous: dict | None, observation: dict | None, *, service: str, environment: str,
                   source_profile: str, bucket_end: datetime, evaluated_at: datetime, policy: dict) -> dict:
    """One immutable decision per bucket. Persist snapshots before live use.

    Identical retries are idempotent. Re-evaluating/revising an already decided
    bucket requires an explicit replay, never an extra persistence increment.
    """
    validate_policy(policy)
    end, evaluated = timestamp(bucket_end), timestamp(evaluated_at)
    if service not in BUSINESS_ROUTES or not environment or not source_profile:
        raise ValueError("health source identity is missing/unregistered")
    if bucket_end.timestamp() % 60 or evaluated_at < bucket_end:
        raise ValueError("health decisions require closed aligned buckets")
    policy_sha = stable_id(policy)
    observation_ref = None
    if observation is not None:
        validate_payload("observation-envelope.schema.json", observation)
        if (observation["service"], observation["environment"], observation["source_profile"], observation["bucket_end"]) != (service, environment, source_profile, end):
            raise ValueError("health observation source/bucket identity differs")
        identity = {k: v for k, v in observation.items() if k not in ("record_id", "available_at", "ingested_at")}
        if observation["record_id"] != stable_id(identity):
            raise ValueError("health observation record identity mismatch")
        observation_ref = "artifact://observations/" + stable_id(observation) + ".json"
    if previous is not None:
        validate_payload("health-signal.schema.json", previous)
        if (previous["service"], previous["environment"], previous["source_profile"], previous["policy_sha256"]) != (service, environment, source_profile, policy_sha):
            raise ValueError("health checkpoint source/policy differs")
        if previous["bucket_end"] == end:
            if previous["evaluated_at"] == evaluated and previous["observation_ref"] == observation_ref:
                return deepcopy(previous)
            raise ValueError("bucket already evaluated; revision requires explicit replay")
        if utc_time(previous["bucket_end"]) > bucket_end or utc_time(previous["evaluated_at"]) > evaluated_at:
            raise ValueError("out-of-order health decision requires explicit replay")
    consecutive = previous is not None and bucket_end - utc_time(previous["bucket_end"]) == timedelta(seconds=60)
    active = bool(previous and previous["incident_active"])
    breach = previous["breach_streak"] if consecutive else 0
    clear = previous["clear_streak"] if consecutive else 0
    onset = previous["episode_start_at"] if previous and (active or breach) else None
    confirmed = previous["episode_confirmed_at"] if active else None
    support = previous["evidence_refs"][:] if consecutive and previous["availability"] == "ELIGIBLE" else []
    reasons = []
    if observation is None:
        reasons.append("missing_observation")
    elif max(utc_time(observation["available_at"]), utc_time(observation["ingested_at"])) > evaluated_at:
        reasons.append("observation_not_available")
    elif (evaluated_at - bucket_end).total_seconds() > policy["maximum_lag_seconds"]:
        reasons.append("stale_observation")
    elif not observation["quality"]["eligible_for_inference"]:
        reasons.append("ineligible_observation")
    elif observation["measurements"]["request_count"] is None or observation["measurements"]["request_count"] < policy["minimum_requests"]:
        reasons.append("insufficient_traffic")
    elif observation["quality"]["coverage_ratio"] < policy["minimum_coverage"]:
        reasons.append("insufficient_coverage")
    if reasons:
        health, event, breach, clear, support = None, "data_unknown", 0, 0, []
        if not active:
            onset, confirmed = None, None
    else:
        payload, measurements = observation["payload"], observation["measurements"]
        if measurements["error_count"] is None or measurements["error_count"] > measurements["request_count"] or not math.isclose(payload["error_rate"], measurements["error_count"] / measurements["request_count"], abs_tol=1e-9):
            raise ValueError("health error ratio contradicts request measurements")
        violated = payload["p95_latency_ms"] > policy["latency_ms"] or payload["error_rate"] > policy["error_rate"]
        event = "observation"
        if violated:
            if not breach:
                support = []
                if not active:
                    onset = timestamp(bucket_end - timedelta(seconds=60))
            breach, clear = min(breach + 1, policy["entry_buckets"]), 0
            support = (support + [observation_ref])[-policy["entry_buckets"]:]
            if not active and breach >= policy["entry_buckets"]:
                active, confirmed, event = True, evaluated, "incident_opened"
            health = "incident" if active else "degrading"
        else:
            if not clear:
                support = []
            clear, breach = min(clear + 1, policy["recovery_buckets"]), 0
            support = (support + [observation_ref])[-policy["recovery_buckets"]:]
            if active and clear >= policy["recovery_buckets"]:
                active, event = False, "recovery_confirmed"
            health = "recovering" if active else "healthy"
            if not active and event != "recovery_confirmed":
                onset, confirmed = None, None
    signal = {"schema_version": "1.0", "signal_version": "slo-health/v1", "source": "slo",
        "service": service, "environment": environment, "source_profile": source_profile,
        "bucket_end": end, "evaluated_at": evaluated, "policy_sha256": policy_sha,
        "availability": "UNKNOWN" if reasons else "ELIGIBLE", "health": health,
        "incident_active": active, "breach_streak": breach, "clear_streak": clear,
        "episode_start_at": onset, "episode_confirmed_at": confirmed, "event": event,
        "observation_ref": observation_ref, "evidence_refs": support, "reasons": reasons,
        "previous_signal_id": previous["signal_id"] if previous else None, "model_outputs_available": False}
    signal["signal_id"] = stable_id(signal)
    validate_payload("health-signal.schema.json", signal)
    return signal
