"""Reproducible readiness accounting; reference proxies never certify health."""
from __future__ import annotations

from sentinelops.public_data.policy import load_policy
from sentinelops.telemetry.core import stable_id


class DatasetNotReady(ValueError):
    pass


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
