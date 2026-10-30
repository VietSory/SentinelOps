"""Reproducible readiness accounting; reference proxies never certify health."""
from __future__ import annotations

from copy import deepcopy

from sentinelops.public_data.policy import load_policy
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id


class DatasetNotReady(ValueError):
    pass


def audit_readiness(report: dict) -> dict:
    if report.get("dataset_version") != "gamma-grouped-resource/v1":
        raise ValueError("expected the approved GAMMA grouped resource report")
    policy = load_policy()
    if report["training_data_policy_hash"] != stable_id(policy):
        raise ValueError("dataset differs from frozen training source policy")
    candidate = sum(v for k, v in report["counts"].items() if k.endswith("/candidate"))
    eligible = sum(v for k, v in report["counts"].items() if k.endswith("/feature-eligible"))
    if candidate != report["rows"] or not 0 <= eligible <= candidate:
        raise ValueError("dataset candidate/eligibility accounting differs")
    if sum(report["case_counts"].values()) != 194:
        raise ValueError("GAMMA cohort must account for all 194 cases")
    reasons = list(report["blocking_reasons"])
    for key in ("health_labels_available", "source_reference_is_healthy_certificate", "source_reference_traffic_verified",
                "risk_target_available", "live_compatible", "training_ready"):
        if report.get(key) is not True:
            reasons.append(key + "=false")
    # The resource report has no health-certified business-service training rows.
    # Do not assign anonymous native entities to the three live HTTP services.
    certified = {service: {"train": 0, "calibration": 0, "train_runs": 0, "calibration_runs": 0} for service in BUSINESS_ROUTES}
    for service in certified:
        reasons.append("insufficient_healthy_train_and_calibration:" + service)
    return {"readiness_version": "gamma-model-readiness/v1", "status": "BLOCKED", "G2": "BLOCKED", "G3": "BLOCKED",
            "source_report_sha256": stable_id(report), "training_data_policy_hash": stable_id(policy),
            "candidate_windows": candidate, "feature_eligible_windows": eligible, "excluded_windows": candidate - eligible,
            "case_counts": deepcopy(report["case_counts"]), "counts": deepcopy(report["counts"]),
            "reference_proxy_counts": deepcopy(report["feature_eligible_label_counts"]),
            "certified_business_service_health": certified, "required_per_service": {"healthy_train": 300, "healthy_calibration": 100},
            "reference_support": deepcopy(report.get("reference_support", {})),
            "blockers": sorted(set(reasons)), "fitted_models": [], "activation_allowed": False,
            "verified_precursor_runs": 0, "holdout_opened": False,
            "limitations": ["Input is a previously committed measured report, not a new raw-source audit.",
                            "Overlapping resource windows are not independent experiments.",
                            "No-recorded-interference reference is not healthy/SLO/precursor truth.",
                            "Live HTTP feature/source parity is unverified; test data remains sealed."]}


def require_training_ready(report: dict) -> None:
    if (report.get("training_ready") is not True or report.get("health_labels_available") is not True
        or report.get("source_reference_is_healthy_certificate") is not True):
        raise DatasetNotReady("GAMMA healthy-reference/G2 gate is blocked; no estimator fitted")
    if (report.get("dataset_version") != "gamma-verified-training/v1"
        or report.get("source") != load_policy()["sources"]["gamma"]
        or report.get("training_data_policy_hash") != stable_id(load_policy())
        or report.get("independence_established") is not True
        or report.get("risk_target_available") is not True):
        raise DatasetNotReady("no admitted source-compatible health/precursor/independence certificate")
    # Content-addressed objects establish byte integrity, not scientific truth.
    # No accepted GAMMA adapter currently verifies traffic, SLO outcomes and
    # independent precursor certificates. Do not let caller-authored flags (or
    # arbitrary existing evidence_refs) create an admitted-gamma model. Enabling
    # this path requires an evidence verifier, not another approval boolean.
    raise DatasetNotReady("no source-backed GAMMA health/precursor certificate verifier; self-attested admission rejected")
