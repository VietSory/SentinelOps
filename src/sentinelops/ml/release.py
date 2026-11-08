"""Operator-run serving release from a verified H1 candidate and explicit decisions.

Training and evaluation never call this. It records an owner/operator decision
that was taken outside software, after checking that every referenced piece of
evidence matches the exact candidate. It writes a new trusted store: the received
candidate stays byte-for-byte reverifiable. It does not activate anything; B
selects the release through the existing serving settings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from sentinelops.contracts import utc_time
from sentinelops.ml.handoff import _read_pinned
from sentinelops.ml.local_data import _inside
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

PARITY_VERSION = "model-source-parity/v1"
PARITY_VERIFICATION_VERSION = "model-source-parity-check/v1"
APPROVAL_VERSION = "model-release-approval/v1"
RELEASE_VERSION = "validated-model-release/v1"
MANIFEST_REF = re.compile(r"artifact://manifests/[0-9a-f]{64}\.json")
PARITY_SCOPE = ("candidate_id", "environment", "source_profile", "models", "verified_by", "verified_at")


def _audit(root, reference):
    if not MANIFEST_REF.fullmatch(reference or ""):
        raise ValueError("candidate audit reference is not pinned")
    path = _inside(root, "audit/" + reference.removeprefix("artifact://"))
    return json.loads(_read_pinned(path, path.stem))


def parity_verification_ref(verification):
    return f"artifact://manifests/{stable_id(verification)}.json"


def validate_parity_record(record, index, source_profile, verification, candidate_check):
    """Bind B's parity decision to the portable C1–C6 evidence for this release."""
    expected = {"parity_version", "status", "evidence", *PARITY_SCOPE}
    routes = {m["service"]: m["model_version"] for m in index["models"]}
    if (not isinstance(record, dict) or set(record) != expected or record["parity_version"] != PARITY_VERSION
        or record["candidate_id"] != index["candidate_id"] or record["status"] != "PASS"
        or record["source_profile"] != source_profile
        or not isinstance(record["environment"], str) or not record["environment"].strip()
        or not isinstance(record["verified_by"], str) or not record["verified_by"].strip()
        or not isinstance(record["verified_at"], str)
        or not isinstance(record["models"], list)
        or any(not isinstance(m, dict) or set(m) != {"service", "model_version"} for m in record["models"])
        or {(m.get("service"), m.get("model_version")) for m in record["models"]} != set(routes.items())
        or len(record["models"]) != len(routes)):
        raise ValueError("source parity record does not certify this exact candidate and source profile")
    utc_time(record["verified_at"])

    if (not isinstance(verification, dict) or verification.get("version") != PARITY_VERIFICATION_VERSION
        or record["evidence"] != [parity_verification_ref(verification)]
        or any(verification.get(key) != record[key] for key in PARITY_SCOPE)
        or verification.get("activation_allowed") is not False or verification.get("G3_accepted") is not False
        or not re.fullmatch(r"[0-9a-f]{40}", verification.get("source_commit") or "")
        or not re.fullmatch(r"[0-9a-f]{64}", verification.get("source_shapes_sha256") or "")
        or not MANIFEST_REF.fullmatch(verification.get("normalization_ref") or "")
        or verification.get("failures") != []
        or verification.get("replay") != {**candidate_check, "development_predictions_reproduced": True}):
        raise ValueError("source parity verification is missing, failed or differs from the pinned decision")

    criteria = verification.get("criteria")
    expected_counts = {"C2": 17, "C3": 16, "C4": 15, "C5": 15, "C6": 3}
    if (not isinstance(criteria, dict) or set(criteria) != set(expected_counts)
        or any(not isinstance(criteria[key], list) or len(criteria[key]) != count
               or any(not isinstance(row, dict) or row.get("ok") is not True for row in criteria[key])
               for key, count in expected_counts.items())):
        raise ValueError("source parity verification lacks complete passing C2–C6 checks")
    scopes = {(row.get("service"), row.get("bucket_end")) for row in criteria["C4"]}
    ends = {end for _, end in scopes if isinstance(end, str)}
    if (len(ends) != 5 or scopes != {(service, end) for service in routes for end in ends}
        or any({(row.get("service"), row.get("bucket_end")) for row in criteria[key] if "service" in row} != scopes
               or sum("service" in row for row in criteria[key]) != len(scopes)
               for key in ("C2", "C3", "C5"))
        or {(row.get("service"), row.get("model_version")) for row in criteria["C6"]} != set(routes.items())):
        raise ValueError("source parity verification lacks five matched buckets per route or exact model coverage")
    ordered = sorted(map(utc_time, ends))
    if any(end.second or end.microsecond for end in ordered) or any(
        right - left != timedelta(minutes=1) for left, right in zip(ordered, ordered[1:])
    ):
        raise ValueError("source parity verification buckets are not five consecutive UTC minutes")


def validate_v2_report(report, v1, expected_ref):
    pins = sorted((m["service"], m["model_version"], m["manifest_sha256"]) for m in v1["models"])
    if (not isinstance(report, dict) or report.get("evaluation_version") != "local-model-development/v2"
        or report.get("authorizing_adr") != "ADR-015" or report.get("G3_accepted") is not False
        or report.get("activation_allowed") is not False or report.get("holdout_opened") is not False
        or report.get("dataset_ref") != v1["dataset_ref"]
        or report.get("training_manifest_sha256") != v1["training_manifest_sha256"]
        or report.get("source_profile") != v1["source_profile"]
        or report.get("feature_set_version") != v1["feature_set_version"]
        or report.get("training_data_policy_hash") != v1["training_data_policy_hash"]
        or sorted((m["service"], m["model_version"], m["manifest_sha256"]) for m in report.get("models", [])) != pins
        or set(report.get("services", {})) != set(BUSINESS_ROUTES)):
        raise ValueError("v2 detector-event report is not for this exact candidate")
    if expected_ref != f"artifact://manifests/{stable_id(report)}.json":
        raise ValueError("v2 detector-event report differs from the reviewed reference")
    protocol = report.get("protocol")
    if (not isinstance(protocol, dict) or protocol.get("protocol_version") != report["evaluation_version"]
        or protocol.get("base_protocol_sha256") != v1["protocol_sha256"]
        or report.get("base_protocol") != v1["protocol"]
        or report.get("protocol_sha256") != stable_id(protocol)
        or not all(isinstance(report.get(key), str) and MANIFEST_REF.fullmatch(report[key])
                   for key in ("detector_report_ref", "predictions_ref"))):
        raise ValueError("v2 detector-event report does not bind the detector epoch, predictions and protocols")
    for service, item in report["services"].items():
        if not isinstance(item, dict):
            raise ValueError(f"invalid v2 detector-event result for {service}")
        groups, windows = item.get("abrupt_groups"), item.get("abrupt_windows")
        metrics = item.get("abrupt_detector_events", {})
        if (type(groups) is not int or groups < 5 or type(windows) is not int or windows < groups
            or not isinstance(metrics, dict)
            or not all(isinstance(metrics.get(name), dict) and metrics[name] for name in ("isolation_forest", "median_mad"))):
            raise ValueError(f"v2 detector-event report lacks abrupt evaluation evidence for {service}")


def build_model_release(candidate, expected_id, *, v2_report, v2_report_ref, parity_record, parity_verification,
                        approved_by, decision_note, output, replay=False, now=None):
    from scripts.verify_model_candidate import verify_candidate
    from sentinelops.ml.registry import ModelRegistry
    candidate, output = Path(candidate).resolve(strict=True), Path(output)
    if output.exists():
        raise FileExistsError("release output must be a new directory")
    for name, value in (("approved_by", approved_by), ("decision_note", decision_note)):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(name + " must be a nonempty explicit owner decision")
    verified = verify_candidate(candidate, expected_id, replay=replay)
    index = json.loads(_inside(candidate, "handoff.json").read_bytes())
    dataset, v1 = _audit(candidate, index["dataset_ref"]), _audit(candidate, index["evaluation_ref"])
    if dataset.get("training_ready") is not True or dataset.get("dataset_version") != "local-http-admission/v1":
        raise ValueError("candidate dataset is not the frozen G2 admission")
    validate_v2_report(v2_report, v1, v2_report_ref)
    validate_parity_record(parity_record, index, v1["source_profile"], parity_verification, verified)
    decided = timestamp(now or datetime.now(timezone.utc))
    staging = output.with_name("." + output.name + ".staging")
    if staging.exists():
        raise FileExistsError("a previous release build left a staging directory; investigate it")
    staging.mkdir(parents=True)
    store = ArtifactStore(staging)
    evidence = [store.write("manifests", value) for value in (dataset, v1, v2_report, parity_record, parity_verification)]
    rows = []
    for model in index["models"]:
        source_dir = _inside(candidate, "runtime/" + model["bundle_directory"])
        manifest = json.loads(_read_pinned(source_dir / "manifest.json", model["manifest_sha256"]))
        target = staging / model["bundle_directory"]
        target.mkdir(parents=True)
        for name, sha in (("manifest.json", model["manifest_sha256"]), *manifest["artifacts"].items()):
            data = _read_pinned(source_dir / name, sha)
            (target / name).write_bytes(data)
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != sha:
                raise ValueError("copied release asset differs from its pin")
        approval = {"approval_version": APPROVAL_VERSION, "candidate_id": expected_id, "service": model["service"],
            "model_version": model["model_version"], "G2": "PASS", "G3": "PASS", "source_parity": "PASS",
            "activation_allowed": True, "evaluation_refs": evidence,
            "dataset_ref": manifest["provenance"]["dataset_ref"],
            "training_data_policy_hash": manifest["provenance"]["training_data_policy_hash"],
            "approved_by": approved_by.strip(), "decision_note": decision_note.strip(), "approved_at": decided}
        rows.append({"bundle_directory": model["bundle_directory"], "manifest_sha256": model["manifest_sha256"],
                     "validation_ref": store.write("manifests", approval)})
    release = {"release_version": RELEASE_VERSION, "candidate_id": expected_id, "models": rows,
               "built_at": decided, "approved_by": approved_by.strip()}
    reference = store.write("manifests", release)
    loaded = ModelRegistry.load(ArtifactStore(staging), reference)
    if not loaded.available or loaded.identity != reference:
        raise ValueError("built release does not load as a complete validated registry")
    os.rename(staging, output)
    return {"release_ref": reference, "store": str(output), "candidate_id": expected_id,
            "services": sorted(r["bundle_directory"] for r in rows), "verified_files": verified["verified_files"],
            "activation_performed": False}


def abandon_staging(output):
    """Explicit operator cleanup of a failed build's own staging directory."""
    staging = Path(output).with_name("." + Path(output).name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
