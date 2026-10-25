from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9-]+\.schema\.json", name):
        raise ValueError("invalid schema name")
    bundled = files("sentinelops").joinpath("schemas", name)
    if bundled.is_file():
        return json.loads(bundled.read_text(encoding="utf-8"))
    # Editable source checkout: the root contracts remain the only authority.
    root = Path(__file__).resolve().parents[2]
    path = root / "contracts" / name
    if not path.is_file():
        raise ValueError(f"unknown schema: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_payload(schema_name: str, payload: dict[str, Any]) -> None:
    validator = Draft202012Validator(
        load_schema(schema_name),
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        messages = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
        raise ValueError("; ".join(messages))
    _finite_values(payload)
    _semantic_validation(schema_name, payload)


def utc_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamps must be timezone-aware UTC")
    return parsed


def _finite_values(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numeric value")
    if isinstance(value, dict):
        for item in value.values():
            _finite_values(item)
    elif isinstance(value, list):
        for item in value:
            _finite_values(item)


def _semantic_validation(name: str, payload: dict[str, Any]) -> None:
    if name == "feature-window.schema.json":
        if utc_time(payload["window_start"]) >= utc_time(payload["window_end"]):
            raise ValueError("window_start must precede window_end")
    if name in ("telemetry.schema.json", "detection-result.schema.json", "degradation-risk.schema.json"):
        utc_time(payload.get("timestamp", payload.get("observed_at")))
    if name == "incident-evidence-record.schema.json":
        utc_time(payload["detected_at"])
        evidence_ids = [item["id"] for item in payload["evidence"]]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("duplicate evidence IDs")
        known = set(evidence_ids)
        previous = float("inf")
        services = set()
        for candidate in payload["root_cause_ranking"]:
            if not candidate["evidence_refs"] or not set(candidate["evidence_refs"]) <= known:
                raise ValueError("root cause references unknown or empty evidence")
            if candidate["service"] in services or candidate["score"] > previous:
                raise ValueError("root cause ranking must be unique and descending")
            services.add(candidate["service"])
            previous = candidate["score"]
        for evidence in payload["evidence"]:
            if evidence.get("observed_at") is not None:
                utc_time(evidence["observed_at"])
        for signal in payload["service_signals"]:
            if signal.get("first_anomaly_at") is not None:
                utc_time(signal["first_anomaly_at"])
    if name == "observation-envelope.schema.json":
        start, end = utc_time(payload["bucket_start"]), utc_time(payload["bucket_end"])
        if (end - start).total_seconds() != 60 or end.timestamp() % 60:
            raise ValueError("observation buckets must be aligned 60-second intervals")
        available, ingested = utc_time(payload["available_at"]), utc_time(payload["ingested_at"])
        if available < end or ingested < available:
            raise ValueError("invalid observation availability ordering")
        eligible = payload["quality"]["eligible_for_inference"]
        if eligible != (payload["quality"]["status"] == "valid"):
            raise ValueError("quality status and inference eligibility disagree")
        if eligible != (payload["payload"] is not None):
            raise ValueError("eligible observations require a telemetry payload")
        if payload["payload"] is not None:
            validate_payload("telemetry.schema.json", payload["payload"])
            if payload["payload"]["timestamp"] != payload["bucket_end"] or payload["payload"]["service"] != payload["service"]:
                raise ValueError("observation payload service/time mismatch")
    if name == "feature-envelope.schema.json":
        start, end = utc_time(payload["window_start"]), utc_time(payload["window_end"])
        if (end - start).total_seconds() != 300 or end.timestamp() % 60:
            raise ValueError("feature windows must be aligned 300-second intervals")
        if utc_time(payload["as_of"]) < end:
            raise ValueError("feature window is not closed at as_of")
        eligible = payload["quality"]["eligible_for_inference"]
        if eligible != (payload["payload"] is not None):
            raise ValueError("eligible features require a FeatureWindow payload")
        if payload["payload"] is not None:
            validate_payload("feature-window.schema.json", payload["payload"])
            for field in ("service", "window_start", "window_end"):
                if payload[field] != payload["payload"][field]:
                    raise ValueError(f"feature envelope {field} mismatch")
    if name == "infrastructure-observation.schema.json":
        if utc_time(payload["available_at"]) < utc_time(payload["observed_at"]):
            raise ValueError("infrastructure availability precedes observation")
        values = payload["metrics"]
        usable = values["max_connections"] - values["reserved_connections"]
        if usable <= 0 or not math.isclose(values["db_connections_pct"], values["db_connections_count"] / usable * 100, abs_tol=1e-9):
            raise ValueError("infrastructure capacity/percentage mismatch")
    if name == "experiment-run.schema.json":
        start = utc_time(payload["started_at"])
        finish = utc_time(payload["ended_at"]) if payload["ended_at"] else None
        if finish and finish <= start:
            raise ValueError("experiment end must follow start")
        for key in ("warmup_end_at", "fault_start_at", "recovery_start_at"):
            if payload[key]:
                point = utc_time(payload[key])
                if point < start or (finish and point > finish):
                    raise ValueError("experiment phase timestamp outside run")
        previous = start
        for phase in payload["phase_intervals"]:
            begin, end = utc_time(phase["start_at"]), utc_time(phase["end_at"])
            if begin < previous or end <= begin or (finish and end > finish):
                raise ValueError("experiment phases must be ordered and nonoverlapping")
            previous = end
        for service_slo in payload["slo_by_service"].values():
            first, confirmed = service_slo["first_violation_at"], service_slo["confirmed_at"]
            if bool(first) != bool(confirmed):
                raise ValueError("SLO onset and confirmation must be paired")
            if first and (utc_time(first) < start or utc_time(confirmed) <= utc_time(first)
                          or (finish and utc_time(confirmed) > finish)):
                raise ValueError("SLO timestamps outside experiment ordering")
        if payload["status"] == "completed":
            if not finish or not payload["warmup_end_at"] or not payload["source_manifest_ref"] or not payload["logs_ref"] or not payload["request_outcomes_ref"]:
                raise ValueError("completed experiment requires times and evidence references")
            if (utc_time(payload["warmup_end_at"])-start).total_seconds() < 300:
                raise ValueError("completed experiment requires a full feature warmup")
            if payload["scenario_id"] == "S0":
                if payload["fault_start_at"] or payload["fault_method"] != "none" or payload["affected_services_ground_truth"]:
                    raise ValueError("healthy scenario cannot declare a fault")
            elif not payload["fault_start_at"] or payload["fault_method"] == "none":
                raise ValueError("fault scenario requires actual fault timing/method")
            if payload["fault_start_at"] and utc_time(payload["fault_start_at"]) < utc_time(payload["warmup_end_at"]):
                raise ValueError("fault cannot precede completed warmup")
            if payload["recovery_start_at"] and payload["fault_start_at"] and utc_time(payload["recovery_start_at"]) < utc_time(payload["fault_start_at"]):
                raise ValueError("recovery cannot precede the fault")
    if name == "dataset-manifest.schema.json":
        utc_time(payload["created_at"])
        ready = payload["training_ready"]
        if ready != (payload["status"] == "training-ready") or ready == bool(payload["blocking_reasons"]):
            raise ValueError("dataset readiness contradicts blocking reasons")
        run_ids = [item["run_id"] for item in payload["run_refs"]]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("duplicate dataset run references")
