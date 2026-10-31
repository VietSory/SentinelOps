"""Source-verified corpus dispatch. Sealed final references are never opened.

Local development support is audited separately from the train/calibration rows
returned for fitting. Historical GAMMA certificates remain unavailable.
"""
from __future__ import annotations

from collections import defaultdict

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.ml.readiness import DatasetNotReady, require_training_ready
from sentinelops.telemetry.core import stable_id


def load_training_rows(store, report):
    if report.get("dataset_version") == "local-http-admission/v1":
        from sentinelops.ml.local_data import admitted_local_rows
        return admitted_local_rows(store, report)
    require_training_ready(report)
    features = tuple(report["ordered_features"])
    if not features or len(set(features)) != len(features):
        raise ValueError("duplicate/empty feature registry")
    if not report.get("source_audit_refs"):
        raise DatasetNotReady("source audit/health certification references required")
    for reference in report["source_audit_refs"]:
        store.read(reference)
    table = store.read(report["training_rows_ref"])
    if table.get("table_version") != "gamma-verified-training-rows/v1":
        raise ValueError("unrecognized admitted training table")
    assignments, seen, result = {}, set(), []
    groups = defaultdict(set)
    for item in table["rows"]:
        role = item["split"]
        if role not in ("train", "calibration"):
            raise ValueError("training table must not contain development/validation/test rows")
        group = item["group_id"]
        if group in assignments and assignments[group] != role:
            raise ValueError("whole-group split leakage")
        assignments[group] = role
        if item["feature_ref"] in seen:
            raise ValueError("duplicate/reused feature row")
        seen.add(item["feature_ref"])
        frame, label = store.read(item["feature_ref"]), store.read(item["label_ref"])
        validate_payload("research-resource-window.schema.json", frame)
        if (not frame["eligible_for_features"] or set(frame["features"]) != set(features)
            or frame["feature_set_version"] != report["feature_set_version"]
            or utc_time(frame["max_input_observed_at"]) > utc_time(frame["window_end"])):
            raise ValueError("invalid/incompatible admitted feature frame")
        if (label.get("label_version") != "verified-health-and-precursor/v1" or label.get("feature_ref") != item["feature_ref"]
            or type(label.get("healthy")) is not bool or label.get("risk_label") not in (0, 1)
            or not label.get("evidence_refs")):
            raise DatasetNotReady("interference/fault labels cannot substitute for health/precursor evidence")
        for ref in label["evidence_refs"]:
            store.read(ref)
        if label["risk_label"] == 1 and (label.get("gradual_precursor_verified") is not True
            or label.get("slo_breach_at") is None or utc_time(frame["window_end"]) >= utc_time(label["slo_breach_at"])):
            raise DatasetNotReady("risk positive is not a verified pre-SLO gradual precursor")
        if label["healthy"] and label["risk_label"] != 0:
            raise ValueError("healthy row cannot have a positive precursor label")
        service = frame["entity"]
        if label["healthy"]:
            groups[service, role, "healthy"].add(group)
        if label["risk_label"] == 1:
            groups[service, role, "precursor"].add(group)
        result.append({"service": service, "split": role, "window_start": frame["window_start"], "window_end": frame["window_end"],
            "features": [frame["features"][key] for key in features], "healthy": label["healthy"], "risk_label": label["risk_label"],
            "group_id": group, "run_id": item["run_id"], "feature_ref": item["feature_ref"]})
    if sorted({row["service"] for row in result}) != sorted(report["services"]):
        raise ValueError("admission service scope differs from table")
    for service in report["services"]:
        for role, floor, run_floor in (("train", 300, 3), ("calibration", 100, 2)):
            rows = [r for r in result if r["service"] == service and r["split"] == role]
            if (sum(r["healthy"] for r in rows) < floor or len(groups[service, role, "healthy"]) < run_floor
                or len(groups[service, role, "precursor"]) < run_floor or {r["risk_label"] for r in rows} != {0, 1}):
                raise DatasetNotReady("insufficient healthy/class/independent gradual support:" + service + "/" + role)
        train = [r for r in result if r["service"] == service and r["split"] == "train"]
        cal = [r for r in result if r["service"] == service and r["split"] == "calibration"]
        if max(utc_time(r["window_end"]) for r in train) > min(utc_time(r["window_start"]) for r in cal):
            raise ValueError("chronological training/calibration window overlap")
    return result
