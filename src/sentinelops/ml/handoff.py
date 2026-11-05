"""Export trusted unapproved H1 candidates, isolating runtime models from audit labels."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from sentinelops.experiments.local_corpus import PROJECT, clean_source, read_binding
from sentinelops.ml.local_data import _inside, admitted_local_evaluation_rows
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.core import FEATURE_KEYS, FEATURE_SET, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


def _read_pinned(path, expected):
    if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
        raise ValueError("expected SHA256 bytes checksum required")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("candidate asset checksum mismatch")
    return data


def _copy_asset(source, relative, destination, expected):
    """Preserve exact bytes; never follow links, traverse parents or overwrite."""
    origin, target = _inside(Path(source), relative), _inside(Path(destination), relative)
    data = _read_pinned(origin, expected)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(data)
    return expected


def _development_table(table, rows):
    if table.get("table_version") != "local-development-predictions/v1" or not isinstance(table.get("rows"), list):
        raise ValueError("not a development prediction table")
    expected = {row["feature_ref"]: row for row in rows if row["split"] == "development-validation"}
    selected = table["rows"]
    scores = {"isolation_forest_score", "median_mad_score", "xgboost_score", "slope_score"}
    flags = {"isolation_forest_flag", "median_mad_flag", "xgboost_flag", "slope_flag"}
    seen = set()
    for row in selected:
        if not isinstance(row, dict) or not isinstance(row.get("feature_ref"), str):
            raise ValueError("malformed development prediction")
        reference = row["feature_ref"]
        original = expected.get(reference)
        if (original is None or reference in seen or set(row) != set(original) | scores | flags | {"decision_at"}
            or stable_id({key: row[key] for key in original}) != stable_id(original)
            or row["decision_at"] != original["prediction_cutoff"]
            or any(type(row[key]) not in (int, float) or not math.isfinite(row[key]) or row[key] < 0
                   or key != "slope_score" and row[key] > 1 for key in scores)
            or any(row[key] is not None and type(row[key]) is not bool for key in flags)):
            raise ValueError("prediction table differs from original nonfinal development rows")
        seen.add(reference)
    if seen != set(expected):
        raise ValueError("prediction table omits admitted development windows")


def export_model_candidate(source_store, dataset_ref, model_root, evaluation_store, evaluation_ref, *,
                           training_manifest_sha256, output, project=PROJECT):
    """Trusted operator roots only. Does not create any serving approval/release ledger."""
    report = source_store.read(dataset_ref)
    rows, _ = admitted_local_evaluation_rows(source_store, report)
    try:
        frozen_ref, frozen = read_binding(source_store, source_store.root / "dataset-freeze.json")
    except FileNotFoundError as exc:
        raise DatasetNotReady("candidate handoff requires frozen source admission") from exc
    if frozen_ref != dataset_ref or frozen != report:
        raise DatasetNotReady("candidate handoff differs from the source freeze")
    exporter_commit = clean_source(project)
    output, model_root = Path(output), Path(model_root)
    if output.exists():
        raise FileExistsError("handoff output must be new")
    training_path = _inside(model_root, "training-manifest.json")
    training_bytes = _read_pinned(training_path, training_manifest_sha256)
    training = json.loads(training_bytes)
    services = set(report["services"])
    if (training.get("training_manifest_version") != "local-http-fit/v1" or training.get("dataset_ref") != dataset_ref
        or training.get("activation_allowed") is not False or training.get("holdout_opened") is not False
        or not isinstance(training.get("models"), list) or len(training["models"]) != len(services)
        or {row["service"] for row in training["models"]} != services):
        raise ValueError("training handoff differs from the admitted candidate scope")
    evaluation = evaluation_store.read(evaluation_ref)
    if (evaluation.get("evaluation_version") != "local-model-development/v1"
        or evaluation.get("dataset_ref") != dataset_ref
        or evaluation.get("training_manifest_sha256") != training_manifest_sha256
        or evaluation.get("source_profile") != report["source_profile"]
        or evaluation.get("feature_set_version") != FEATURE_SET
        or evaluation.get("training_data_policy_hash") != report["training_data_policy_hash"]
        or evaluation.get("activation_allowed") is not False or evaluation.get("holdout_opened") is not False
        or evaluation.get("G3_accepted") is not False
        or not isinstance(evaluation.get("models"), list) or len(evaluation["models"]) != len(services)
        or {row["service"] for row in evaluation["models"]} != services):
        raise ValueError("evaluation handoff differs from the unapproved candidate")
    predictions_ref = evaluation["predictions_ref"]
    _development_table(evaluation_store.read(predictions_ref), rows)
    from sentinelops.ml.models import load_bundle
    assets, cards, selected_models = [], [], []
    for service in sorted(services):
        relative = stable_id(service)
        directory = _inside(model_root, relative)
        pin = next(row for row in evaluation["models"] if row["service"] == service)
        trained = next(row for row in training["models"] if row["service"] == service)
        body = _read_pinned(_inside(model_root, relative + "/manifest.json"), pin["manifest_sha256"])
        header = json.loads(body)
        provenance = header["provenance"]
        train = [row for row in rows if row["service"] == service and row["split"] == "train"]
        calibration = [row for row in rows if row["service"] == service and row["split"] == "calibration" and row["healthy"]]
        risk_rows = [row for row in train if type(row["risk_label"]) is int and row["risk_label"] in (0, 1)]
        if (header.get("bundle_version") != "sentinelops-model/v2" or header.get("origin") != "admitted-local-http"
            or header["model_version"] != trained["model_version"] or header["model_version"] != pin["model_version"]
            or provenance["dataset_ref"] != dataset_ref or provenance["dataset_report"] != report
            or provenance["service"] != service or provenance["source_profile"] != report["source_profile"]
            or provenance["training_data_policy_hash"] != report["training_data_policy_hash"]
            or header["ordered_features"] != list(FEATURE_KEYS) or provenance["feature_set_version"] != FEATURE_SET
            or provenance.get("risk_horizon_seconds") != 300 or header.get("activation_allowed") is not False
            or provenance["training_rows_sha256"] != stable_id(train)
            or provenance["calibration_rows_sha256"] != stable_id(calibration)
            or provenance["risk_training_rows_sha256"] != stable_id(risk_rows)):
            raise ValueError("bundle origin, row provenance or version differs from accepted source")
        # Verify even the binary paths before the existing trusted bundle loader.
        for name, digest in header["artifacts"].items():
            if name not in ("detector.joblib", "risk.json"):
                raise ValueError("unsupported candidate model asset")
            _read_pinned(_inside(model_root, relative + "/" + name), digest)
        manifest, _, risk = load_bundle(directory, expected_manifest_sha256=pin["manifest_sha256"])
        if risk is None:
            raise ValueError("candidate requires its precursor estimator")
        assets.extend((relative + "/" + name, digest) for name, digest in
                      {"manifest.json": pin["manifest_sha256"], **manifest["artifacts"]}.items())
        cards.append({"card_version": "unapproved-model-card/v1", "service": service,
            "model_version": manifest["model_version"], "dataset_ref": dataset_ref,
            "source_profile": provenance["source_profile"], "feature_set_version": FEATURE_SET,
            "ordered_features": manifest["ordered_features"], "library_versions": manifest["library_versions"],
            "fitting_source_commit": provenance["fitting_source_commit"], "thresholds": manifest["thresholds"],
            "risk_thresholds": manifest["risk_thresholds"], "risk_horizon_seconds": provenance["risk_horizon_seconds"],
            "risk_is_calibrated_probability": False, "detector_config": manifest["detector_config"],
            "source_counts": {role: counts[service] for role, counts in report["counts"].items()},
            "development_comparison": evaluation["services"][service],
            "evaluation_ref": evaluation_ref, "activation_allowed": False, "source_parity": "not-accepted",
            "limitations": evaluation["limitations"] + ["Candidate export is identity/materialization verification, not model quality approval."]})
        selected_models.append({"service": service, "model_version": manifest["model_version"],
            "bundle_directory": "models/" + relative, "manifest_sha256": pin["manifest_sha256"]})
    # Nothing is written until source, identities and all bundle assets pass.
    output.mkdir(parents=True, exist_ok=False)
    runtime, audit_root = output / "runtime", output / "audit"
    audit = ArtifactStore(audit_root)
    checksums = {}
    for relative, digest in assets:
        _copy_asset(model_root, relative, runtime / "models", digest)
        checksums["runtime/models/" + relative] = digest
    _copy_asset(model_root, "training-manifest.json", audit_root, training_manifest_sha256)
    checksums["audit/training-manifest.json"] = training_manifest_sha256
    for store, ref in ((source_store, dataset_ref), (evaluation_store, evaluation_ref), (evaluation_store, predictions_ref)):
        relative = ref.removeprefix("artifact://")
        digest = Path(relative).stem
        store.read(ref)  # Validate the content-addressed source again before copying.
        _copy_asset(store.root, relative, audit_root, digest)
        checksums["audit/" + relative] = digest
    for selected, card in zip(selected_models, cards):
        selected["model_card_ref"] = audit.write("manifests", card)
    index = {"handoff_version": "unapproved-model-candidate/v1", "runtime_root": "runtime", "audit_root": "audit",
        "models": selected_models, "dataset_ref": dataset_ref, "evaluation_ref": evaluation_ref,
        "training_manifest_sha256": training_manifest_sha256, "asset_sha256": checksums,
        "exporter_source_commit": exporter_commit, "exported_at": timestamp(datetime.now(timezone.utc)),
        "activation_allowed": False, "holdout_opened": False, "source_parity": "not-accepted",
        "original_corpus_included": False,
        "external_requirements": ["Original frozen corpus and Git objects for source replay.",
                                  "Independent G3/source-parity review and separate operator serving approval."]}
    index["candidate_id"] = stable_id(index)
    with (output / "handoff.json").open("x") as stream:
        json.dump(index, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return index
