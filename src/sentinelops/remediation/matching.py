"""Deterministic runbook suggestions from verified evidence; no action authority."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.health import validate_policy
from sentinelops.telemetry.core import stable_id

SYMPTOM_VERSION = "incident-symptoms/v1"
DECISION_VERSION = "runbook-decision/v1"
HEALTH_POLICY = Path(__file__).resolve().parents[3] / "configs/slo_health_policy.json"
ENUMS = {
    "symptom_version": (SYMPTOM_VERSION,),
    "severity": ("low", "medium", "high", "critical"),
    "modality": ("latency", "errors", "both", "none"),
    "latency_band": ("none", "mild", "moderate", "severe"),
    "onset": ("abrupt", "gradual", "unknown"),
}
BOOLEANS = {"rca_abstained", "deployment_change", "checkout_impacted", "db_lock"}
FIELDS = set(ENUMS) | BOOLEANS | {
    "root_service", "affected_services", "root_p95_ms", "root_error_rate",
}


def _number(value, *, maximum=None):
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
            or maximum is not None and value > maximum):
        raise ValueError("expected a finite nonnegative number")
    return float(value)


def _field_value(field, value):
    if field in ENUMS:
        valid = isinstance(value, str) and value in ENUMS[field]
    elif field in BOOLEANS:
        valid = type(value) is bool
    elif field == "root_service":
        valid = value is None or isinstance(value, str) and bool(value.strip())
    elif field == "affected_services":
        valid = (isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
                 and value == sorted(set(value)))
    elif field in ("root_p95_ms", "root_error_rate"):
        if value is not None:
            _number(value, maximum=1 if field == "root_error_rate" else None)
        valid = True
    else:
        valid = False
    if not valid:
        raise ValueError(f"invalid symptom value: {field}")


def _validate_symptoms(symptoms):
    if not isinstance(symptoms, dict) or set(symptoms) != FIELDS:
        raise ValueError("incomplete incident symptoms")
    for field, value in symptoms.items():
        _field_value(field, value)
    if symptoms["checkout_impacted"] != ("checkout-service" in symptoms["affected_services"]):
        raise ValueError("checkout impact differs from affected group")
    if not symptoms["affected_services"]:
        raise ValueError("missing affected services")
    if not symptoms["rca_abstained"] and any(symptoms[k] is None for k in (
            "root_service", "root_p95_ms", "root_error_rate")):
        raise ValueError("missing root evidence")


def _support(candidate, component):
    value = candidate["components"][component]["value"]
    return value is not None and _number(value, maximum=1) > 0


def _onset(derived, root, group):
    # The worker's existing wire shape is [(artifact_ref, model_signal), ...].
    # Only available, root-scoped risk observed AND emitted before onset counts.
    risks = []
    onset = utc_time(group["first_onset_at"])
    for _, signal in derived.get("model_signals", []):
        if signal["service"] != root or signal["status"] != "AVAILABLE":
            continue
        risk = signal["inference"]["risk"]
        if risk is None:
            continue
        validate_payload("degradation-risk.schema.json", risk)
        if risk["service"] != root:
            raise ValueError("risk belongs to another service")
        if utc_time(risk["observed_at"]) < onset and utc_time(signal["evaluated_at"]) < onset:
            risks.append(risk["state"])
    return "gradual" if set(risks) & {"watch", "degrading"} else "abrupt" if risks else "unknown"


def extract_symptoms(*, derived: dict, observations: dict[str, list[dict]]) -> dict:
    """Extract §2 from a verified worker result and opening observations.

    Accept telemetry payloads or observation envelopes (only eligible payloads
    contribute). Missing root data raises ValueError for the caller to escalate.
    Model onset uses the worker's optional, verified ``model_signals`` snapshots.
    """
    try:
        # A wheel carries schemas; runtime configs remain deployment inputs.
        policy_path = HEALTH_POLICY if HEALTH_POLICY.is_file() else Path("configs/slo_health_policy.json")
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        validate_policy(policy)
        rca = derived["rca"]
        if type(rca["abstained"]) is not bool:
            raise ValueError("RCA abstention must be boolean")
        groups = [g for g in rca["correlation"]["groups"] if g["group_id"] == rca["group_id"]]
        if len(groups) != 1:
            raise ValueError("RCA group does not resolve uniquely")
        group = groups[0]
        affected = sorted(group["affected_services"])
        root = None if rca["abstained"] else rca["ranking"][0]
        candidates = rca["candidates"]
        roots = [c for c in candidates if c["service"] == root]
        if root is not None and len(roots) != 1:
            raise ValueError("root candidate does not resolve uniquely")
        points = []
        if root is not None:
            for observation in observations[root]:
                if "quality" in observation:
                    if type(observation["quality"]["eligible_for_inference"]) is not bool:
                        raise ValueError("invalid eligibility")
                    if not observation["quality"]["eligible_for_inference"]:
                        continue
                    observation = observation["payload"]
                if observation.get("service", root) != root:
                    raise ValueError("root observation belongs to another service")
                points.append((_number(observation["p95_latency_ms"]),
                               _number(observation["error_rate"], maximum=1)))
            if not points:
                raise ValueError("no eligible root observations")
        p95 = max(p[0] for p in points) if points else None
        errors = max(p[1] for p in points) if points else None
        slow = p95 is not None and p95 > policy["latency_ms"]
        failed = errors is not None and errors > policy["error_rate"]
        symptoms = {
            "symptom_version": SYMPTOM_VERSION, "severity": derived["severity"],
            "rca_abstained": rca["abstained"], "root_service": root,
            "affected_services": affected,
            "modality": "both" if slow and failed else "latency" if slow else "errors" if failed else "none",
            "root_p95_ms": p95, "root_error_rate": errors,
            "latency_band": ("none" if not slow else "mild" if p95 <= 1250
                             else "moderate" if p95 <= 1500 else "severe"),
            "onset": _onset(derived, root, group) if root is not None else "unknown",
            "deployment_change": bool(roots and _support(roots[0], "recent_change_support")),
            "checkout_impacted": "checkout-service" in affected,
            "db_lock": any(c["service"].lower() in {"rds", "postgresql", "postgres"}
                           and (_support(c, "log_support") or _support(c, "anomaly_strength"))
                           for c in candidates),
        }
        _validate_symptoms(symptoms)
        return symptoms
    except (KeyError, IndexError, TypeError, AttributeError, OSError, OverflowError) as exc:
        raise ValueError("missing or invalid incident symptom evidence") from exc


def _validate_catalog(catalog):
    validate_payload("runbook-catalog.schema.json", catalog)
    entries = catalog["entries"]
    if len({e["id"] for e in entries}) != len(entries):
        raise ValueError("duplicate runbook IDs")
    thresholds = catalog["thresholds"]
    if thresholds["oncall_min_score"] > thresholds["auto_min_score"]:
        raise ValueError("oncall threshold exceeds auto threshold")
    for entry in entries:
        for field, value in entry["required"].items():
            _field_value(field, value)
        fields = [feature["field"] for feature in entry["features"]]
        if len(set(fields)) != len(fields):
            raise ValueError("duplicate feature fields")
        for feature in entry["features"]:
            values = feature["in"] if "in" in feature else [feature["equals"]]
            for value in values:
                _field_value(feature["field"], value)
        if not math.isfinite(math.fsum(f["weight"] for f in entry["features"])):
            raise ValueError("non-finite total feature weight")


def load_catalog(path) -> dict:
    """Load a strict, semantically checked catalog; never cache mutable objects."""
    try:
        catalog = json.loads(Path(path).read_text(encoding="utf-8"))
        _validate_catalog(catalog)
        return catalog
    except (OSError, TypeError, OverflowError) as exc:
        raise ValueError("invalid runbook catalog") from exc


def _equal(left, right):
    # Python's True == 1 must not match JSON booleans to numeric features.
    return type(left) is type(right) and left == right or (
        type(left) in (int, float) and type(right) in (int, float) and left == right)


def score(symptoms: dict, entry: dict) -> dict:
    failed = sorted(field for field, value in entry["required"].items()
                    if field not in symptoms or not _equal(symptoms[field], value))
    matched = [f for f in entry["features"] if f["field"] in symptoms and any(
        _equal(symptoms[f["field"]], value) for value in (f["in"] if "in" in f else [f["equals"]]))]
    total = math.fsum(f["weight"] for f in entry["features"])
    return {"id": entry["id"], "score": 0.0 if failed else math.fsum(f["weight"] for f in matched) / total,
            "required_failed": failed, "matched": sorted(f["field"] for f in matched)}


def rank(symptoms: dict, catalog: dict) -> list[dict]:
    return sorted((score(symptoms, entry) for entry in catalog["entries"]),
                  key=lambda result: (-result["score"], result["id"]))


def fingerprint(symptoms: dict, runbook_id: str | None) -> str:
    return stable_id(tuple(symptoms.get(field) for field in (
        "root_service", "modality", "latency_band", "deployment_change", "checkout_impacted")) + (runbook_id,))


def decide(symptoms: dict, catalog: dict) -> dict:
    """Apply §1; the caller owns the kill switch and extraction-error fallback.

    An oncall suggestion requires explicit approval and every executor guard.
    """
    _validate_catalog(catalog)
    decision, reasons, ranking, entry = "engineer", [], [], None
    top, margin = None, None
    try:
        _validate_symptoms(symptoms)
    except (ValueError, TypeError, OverflowError):
        reasons = ["symptoms-unavailable"]
    else:
        ranking = rank(symptoms, catalog)
        if ranking:
            top = ranking[0]["score"]
            margin = top - ranking[1]["score"] if len(ranking) > 1 else top
            entry = next(e for e in catalog["entries"] if e["id"] == ranking[0]["id"])
        thresholds = catalog["thresholds"]
        if symptoms["rca_abstained"]:
            reasons = ["rca-abstained"]
        elif entry is None or top < max(.80, thresholds["auto_min_score"]):
            reasons = ["insufficient-match"]
        elif (margin >= max(.15, thresholds["auto_min_margin"])
              and entry["executable"]):
            decision, reasons = "oncall", ["oncall-approval-required"]
        else:
            if not entry["executable"]:
                reasons.append("manual-runbook")
            if margin < max(.15, thresholds["auto_min_margin"]):
                reasons.append("insufficient-margin")
    selected = entry if top and top > 0 else None
    runbook_id = selected["id"] if selected else None
    return {"decision_version": DECISION_VERSION, "decision": decision,
            "runbook_id": runbook_id,
            "executor_runbook": selected["executor_runbook"] if selected else None,
            "score": top, "margin": margin, "reasons": reasons,
            "ranking": ranking[:3], "symptoms": deepcopy(symptoms),
            "catalog_sha256": stable_id(catalog),
            "fingerprint": fingerprint({} if reasons == ["symptoms-unavailable"] else symptoms, runbook_id)}
