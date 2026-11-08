"""Verify a materialized H1 candidate without creating a serving release."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

from sentinelops.ml.handoff import _read_pinned
from sentinelops.ml.local_data import _inside
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_KEYS, FEATURE_SET, stable_id


def artifact_path(reference):
    if not isinstance(reference, str) or not re.fullmatch(r"artifact://manifests/[0-9a-f]{64}\.json", reference):
        raise ValueError("expected a pinned H1 manifest reference")
    return "audit/" + reference.removeprefix("artifact://")


def verify_candidate(root, expected_id, *, replay=False):
    root = Path(root).resolve(strict=True)
    index = json.loads(_inside(root, "handoff.json").read_bytes())
    identity = stable_id({key: value for key, value in index.items() if key != "candidate_id"})
    if (index.get("candidate_id") != expected_id or identity != expected_id
        or index.get("handoff_version") != "unapproved-model-candidate/v1"
        or index.get("activation_allowed") is not False or index.get("holdout_opened") is not False
        or index.get("original_corpus_included") is not False or index.get("source_parity") != "not-accepted"
        or (index.get("runtime_root"), index.get("audit_root")) != ("runtime", "audit")):
        raise ValueError("candidate identity or approval state differs from the selected H1 package")
    models = index["models"]
    if len(models) != len(BUSINESS_ROUTES) or {item["service"] for item in models} != set(BUSINESS_ROUTES):
        raise ValueError("candidate must include exactly the three business routes")
    pins = index["asset_sha256"]

    def read(relative, sha):
        path = _inside(root, relative)
        if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("candidate asset is missing or exceeds the transport bound")
        return _read_pinned(path, sha)

    def audit(reference):
        path = artifact_path(reference)
        digest = Path(path).stem
        return path, json.loads(read(path, digest))

    dataset_path, dataset = audit(index["dataset_ref"])
    evaluation_path, evaluation = audit(index["evaluation_ref"])
    predictions_path, predictions = audit(evaluation["predictions_ref"])
    training_path = "audit/training-manifest.json"
    training = json.loads(read(training_path, index["training_manifest_sha256"]))
    expected = {dataset_path, evaluation_path, predictions_path, training_path}
    if (dataset.get("training_ready") is not True or evaluation.get("G3_accepted") is not False
        or any(doc.get("dataset_ref") != index["dataset_ref"] or doc.get("activation_allowed") is not False
               or doc.get("holdout_opened") is not False for doc in (training, evaluation))
        or evaluation.get("training_manifest_sha256") != index["training_manifest_sha256"]
        or predictions.get("table_version") != "local-development-predictions/v1"):
        raise ValueError("candidate audit records disagree on dataset, evaluation or activation")
    training_models = {row["service"]: row["model_version"] for row in training["models"]}
    evaluation_models = {row["service"]: (row["model_version"], row["manifest_sha256"]) for row in evaluation["models"]}
    if (len(training["models"]) != len(models) or set(training_models) != set(BUSINESS_ROUTES)
        or len(evaluation["models"]) != len(models) or set(evaluation_models) != set(BUSINESS_ROUTES)):
        raise ValueError("candidate training and evaluation route inventories differ")
    routes = {}
    for model in models:
        service = model["service"]
        if model["bundle_directory"] != "models/" + stable_id(service):
            raise ValueError("candidate bundle directory differs from service identity")
        directory = "runtime/" + model["bundle_directory"]
        manifest_path = directory + "/manifest.json"
        manifest = json.loads(read(manifest_path, model["manifest_sha256"]))
        card_path, card = audit(model["model_card_ref"])
        if (manifest.get("bundle_version") != "sentinelops-model/v2" or manifest.get("origin") != "admitted-local-http"
            or manifest["model_version"] != model["model_version"] or card["model_version"] != model["model_version"]
            or training_models[service] != model["model_version"]
            or evaluation_models[service] != (model["model_version"], model["manifest_sha256"])
            or card["development_comparison"] != evaluation["services"][service]
            or card["service"] != service or card["dataset_ref"] != index["dataset_ref"]
            or card["evaluation_ref"] != index["evaluation_ref"] or card["activation_allowed"] is not False
            or manifest["provenance"]["service"] != service or manifest["provenance"]["dataset_ref"] != index["dataset_ref"]
            or manifest["provenance"]["source_profile"] != evaluation["source_profile"]
            or manifest["provenance"]["feature_set_version"] != FEATURE_SET or manifest["ordered_features"] != list(FEATURE_KEYS)
            or manifest.get("activation_allowed") is not False
            or set(manifest["artifacts"]) != {"detector.joblib", "risk.json"}):
            raise ValueError("candidate route, model manifest or card differs from its audit binding")
        binary_paths = {directory + "/" + name: sha for name, sha in manifest["artifacts"].items()}
        if pins.get(manifest_path) != model["manifest_sha256"] or any(pins.get(path) != sha for path, sha in binary_paths.items()):
            raise ValueError("candidate weights differ from the route manifest pins")
        expected.update({manifest_path, *binary_paths})
        routes[service] = (model, directory, card_path)
    if (set(pins) != expected or pins[training_path] != index["training_manifest_sha256"]
        or any(pins[path] != Path(path).stem for path in (dataset_path, evaluation_path, predictions_path))):
        raise ValueError("candidate asset inventory or audit pin differs")
    inventory = expected | {"handoff.json", *(route[2] for route in routes.values())}
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("candidate directory contains a link or special file")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != inventory:
        raise ValueError("candidate directory contains unexpected or missing files")
    for path, sha in pins.items():
        read(path, sha)
    rows = predictions["rows"]
    if ({row["service"] for row in rows} != set(routes)
        or len({row["feature_ref"] for row in rows}) != len(rows)
        or any(row["split"] != "development-validation" for row in rows)):
        raise ValueError("candidate prediction table has invalid scope or duplicate features")
    grouped = {service: [row for row in rows if row["service"] == service] for service in routes}
    if any(len(group) != evaluation["services"][service]["development_windows"] for service, group in grouped.items()):
        raise ValueError("candidate prediction table differs from evaluation denominators")
    if replay:
        from sentinelops.ml.local_evaluation import predict_windows
        from sentinelops.ml.models import load_bundle
    results = {}
    for service, (model, directory, _) in routes.items():
        stored = grouped[service]
        comparison = evaluation["services"][service]
        if replay:
            _, detector, risk = load_bundle(_inside(root, directory), expected_manifest_sha256=model["manifest_sha256"])
            predicted = predict_windows(stored, detector, risk, comparison["slope_threshold"], evaluation["protocol"])
            for original, reproduced in zip(stored, predicted, strict=True):
                for key in ("isolation_forest", "median_mad", "xgboost", "slope"):
                    flag, score = key + "_flag", key + "_score"
                    if (original[flag] is not reproduced[flag]
                        or not math.isclose(original[score], reproduced[score], rel_tol=0, abs_tol=1e-6)):
                        raise ValueError("candidate development prediction differs on this machine")
        detector = comparison["metrics"]["counterfactual_detector_events"]["isolation_forest"]
        results[service] = {"development_windows": len(stored), "IF_matched_events": detector["tp"],
                            "IF_eligible_events": detector["tp"] + detector["fn"]}
    return {"candidate_id": expected_id, "verified_files": len(inventory), "services": results,
            "development_predictions_reproduced": replay, "G3_accepted": False,
            "source_parity_accepted": False, "activation_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-id", required=True, help="Candidate ID from the trusted handoff channel")
    parser.add_argument("--replay", action="store_true", help="Load pinned models from a trusted sender and reproduce development scores")
    args = parser.parse_args()
    print(json.dumps(verify_candidate(args.candidate, args.expected_id, replay=args.replay), indent=2))


if __name__ == "__main__":
    main()
