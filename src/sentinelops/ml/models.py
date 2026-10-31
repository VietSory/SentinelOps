"""Real numerical fit/inference primitives, exercised only with fixtures until G2.

Current fitting enters through train_local_http(), which recomputes original
source readiness before any estimator is constructed. Historical GAMMA remains
blocked. Numerical helpers never certify a dataset,
diagnostic quality or activation. No ground-truth metadata becomes a feature.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from sentinelops.ml.readiness import require_training_ready
from sentinelops.telemetry.core import stable_id


def matrix(values, width):
    raw = np.asarray(values)
    if raw.dtype.kind not in "fiu" or raw.ndim != 2 or raw.shape[1] != width or not len(raw):
        raise ValueError("features require a nonempty finite numeric matrix with the exact ordered schema")
    result = raw.astype(float)
    if not np.isfinite(result).all():
        raise ValueError("required feature missing/nonfinite; inference unavailable")
    return result


@dataclass
class Detector:
    scaler: object
    forest: object
    median: object
    mad_scale: object
    calibration_if: object
    calibration_mad: object
    thresholds: dict
    ordered_features: tuple[str, ...]
    seed: int = 42

    def raw_scores(self, values):
        array = matrix(values, len(self.ordered_features))
        return {"isolation_forest": -self.forest.score_samples(self.scaler.transform(array)),
                "median_mad": np.max(np.abs(array - self.median) / self.mad_scale, axis=1)}

    def predict(self, values):
        scores = self.raw_scores(values)
        return {name: {"score": np.searchsorted(reference, scores[name], side="right") / len(reference),
                       "alert": scores[name] > self.thresholds[name], "raw_score": scores[name]}
                for name, reference in (("isolation_forest", self.calibration_if), ("median_mad", self.calibration_mad))}


def fit_detector(train, calibration, ordered_features, *, seed=42, false_alert_budget=.01):
    """Low-level fit, not admission. Callers supply disjoint eligible matrices."""
    keys = tuple(ordered_features)
    if not keys or len(set(keys)) != len(keys) or any(not isinstance(k, str) or not k for k in keys):
        raise ValueError("ordered feature registry is invalid")
    if not 0 < false_alert_budget < .5 or type(seed) is not int:
        raise ValueError("invalid predeclared seed/calibration budget")
    fit, cal = matrix(train, len(keys)), matrix(calibration, len(keys))
    if len(fit) < 300 or len(cal) < 100:
        raise ValueError("fit requires at least 300 train and 100 calibration rows")
    scaler = RobustScaler().fit(fit)
    forest = IsolationForest(n_estimators=100, max_samples="auto", contamination="auto", random_state=seed, n_jobs=1)
    forest.fit(scaler.transform(fit))
    median = np.median(fit, axis=0)
    scale = np.maximum(1.4826 * np.median(np.abs(fit - median), axis=0), 1e-9)
    bundle = Detector(scaler, forest, median, scale, None, None, {}, keys, seed)
    raw = bundle.raw_scores(cal)
    bundle.calibration_if, bundle.calibration_mad = np.sort(raw["isolation_forest"]), np.sort(raw["median_mad"])
    bundle.thresholds = {key: float(np.quantile(value, 1 - false_alert_budget, method="higher")) for key, value in raw.items()}
    return bundle


def fit_risk(train, labels, ordered_features, *, seed=42):
    """Binary pre-SLO target primitive; admission is a separate mandatory gate."""
    from xgboost import XGBClassifier
    features = matrix(train, len(ordered_features))
    y = np.asarray(labels)
    if y.ndim != 1 or len(y) != len(features) or set(y.tolist()) != {0, 1}:
        raise ValueError("risk fitting requires both verified precursor and healthy/benign classes")
    # No fitting or early stopping on calibration/final labels.
    model = XGBClassifier(n_estimators=50, max_depth=3, learning_rate=.05, subsample=1., colsample_bytree=1.,
                          random_state=seed, n_jobs=1, eval_metric="logloss", objective="binary:logistic",
                          scale_pos_weight=float(np.sum(y == 0) / np.sum(y == 1)))
    model.fit(features, y)
    return model


def save_bundle(output: Path, detector: Detector, *, provenance: dict, risk=None, source_store=None):
    """Exclusive trusted local bundle; never a promotion operation."""
    if provenance.get("origin") not in ("fixture-only", "admitted-gamma", "admitted-local-http"):
        raise ValueError("bundle needs explicit numerical-test or admitted-source provenance")
    if provenance.get("origin") == "admitted-gamma":
        require_training_ready(provenance.get("dataset_report", {}))
    if provenance.get("origin") == "admitted-local-http":
        from sentinelops.ml.local_data import admitted_local_rows
        from sentinelops.public_data.policy import load_training_policy, policy_hash
        if (source_store is None or not provenance.get("dataset_ref")
            or source_store.read(provenance["dataset_ref"]) != provenance.get("dataset_report")
            or provenance.get("training_policy") != load_training_policy()
            or provenance.get("training_data_policy_hash") != policy_hash(load_training_policy())):
            raise ValueError("local model bundle lacks original-source admission provenance")
        admitted_local_rows(source_store, provenance["dataset_report"])
        from sentinelops.experiments.local_corpus import clean_source, read_binding
        try:
            frozen_ref, frozen = read_binding(source_store, source_store.root / "dataset-freeze.json")
        except FileNotFoundError as exc:
            raise ValueError("local model dataset has no immutable admission freeze") from exc
        if frozen != provenance["dataset_report"] or frozen_ref != provenance["dataset_ref"]:
            raise ValueError("local model differs from the frozen source admission")
        project = Path(__file__).resolve().parents[3]
        if provenance.get("fitting_source_commit") != clean_source(project):
            raise ValueError("local model fitting implementation is not the frozen source commit")
    output.mkdir(parents=True, exist_ok=False)
    detector_path = output / "detector.joblib"
    joblib.dump(detector, detector_path, compress=3)
    artifacts = {"detector.joblib": hashlib.sha256(detector_path.read_bytes()).hexdigest()}
    if risk is not None:
        risk.save_model(output / "risk.json")
        artifacts["risk.json"] = hashlib.sha256((output / "risk.json").read_bytes()).hexdigest()
    version = "sentinelops-model/v2" if provenance["origin"] == "admitted-local-http" else "sentinelops-model/v1"
    manifest = {"bundle_version": version, "origin": provenance["origin"], "provenance": provenance,
                "ordered_features": list(detector.ordered_features), "artifacts": artifacts,
                "thresholds": detector.thresholds, "calibration_ecdf_sha256": stable_id({
                    "if": detector.calibration_if.tolist(), "mad": detector.calibration_mad.tolist()}),
                "library_versions": {name: importlib.metadata.version(name) for name in ("numpy", "scikit-learn", "joblib", "xgboost-cpu")},
                "activation_allowed": False, "validation_status": "not-evaluated", "seed": detector.seed,
                "risk_thresholds": {"watch": .5, "degrading": .8},
                "detector_config": detector.forest.get_params(), "preprocessing": "train-only RobustScaler; complete required features",
                "baseline": "median-MAD; separate scores, same train/calibration windows and alert budget"}
    manifest["model_version"] = stable_id(manifest)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return manifest


def load_bundle(output: Path, *, expected_manifest_sha256: str):
    """Only trusted, checksum-pinned local artifacts; no API-supplied pickle."""
    path = output / "manifest.json"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_manifest_sha256:
        raise ValueError("model manifest checksum mismatch")
    manifest = json.loads(data)
    if manifest.get("bundle_version") not in ("sentinelops-model/v1", "sentinelops-model/v2") or manifest["model_version"] != stable_id({k: v for k, v in manifest.items() if k != "model_version"}):
        raise ValueError("unsupported or corrupt model identity")
    if manifest["bundle_version"] == "sentinelops-model/v2":
        from sentinelops.contracts import validate_payload
        from sentinelops.public_data.policy import policy_hash
        provenance = manifest["provenance"]
        validate_payload("training-data-policy-v2.schema.json", provenance.get("training_policy", {}))
        if (manifest["origin"] != "admitted-local-http"
            or provenance.get("training_data_policy_hash") != policy_hash(provenance["training_policy"])):
            raise ValueError("local model bundle source policy/version differs")
        report = provenance.get("dataset_report", {})
        if (report.get("dataset_version") != "local-http-admission/v1" or report.get("training_ready") is not True
            or provenance.get("dataset_ref") != "artifact://manifests/" + stable_id(report) + ".json"
            or report.get("training_data_policy_hash") != provenance["training_data_policy_hash"]):
            raise ValueError("local bundle dataset identity/admission differs")
    elif manifest["origin"] == "admitted-local-http":
        raise ValueError("local HTTP model requires its explicit v2 bundle format")
    if set(manifest["artifacts"]) not in ({"detector.joblib"}, {"detector.joblib", "risk.json"}):
        raise ValueError("unknown model artifacts")
    for name, digest in manifest["artifacts"].items():
        if hashlib.sha256((output / name).read_bytes()).hexdigest() != digest:
            raise ValueError("model artifact checksum mismatch")
    for name, version in manifest["library_versions"].items():
        if importlib.metadata.version(name) != version:
            raise ValueError("model runtime library version mismatch")
    detector = joblib.load(output / "detector.joblib")
    if not isinstance(detector, Detector) or list(detector.ordered_features) != manifest["ordered_features"] or detector.thresholds != manifest["thresholds"]:
        raise ValueError("loaded detector differs from declared schema/thresholds")
    if stable_id({"if": detector.calibration_if.tolist(), "mad": detector.calibration_mad.tolist()}) != manifest["calibration_ecdf_sha256"]:
        raise ValueError("model calibration reference differs")
    risk = None
    if "risk.json" in manifest["artifacts"]:
        from xgboost import XGBClassifier
        risk = XGBClassifier(); risk.load_model(output / "risk.json")
    return manifest, detector, risk


def train_gamma(store, dataset_ref: str, output: Path):
    report = store.read(dataset_ref)
    # Deliberately before estimator construction, table access or output.
    require_training_ready(report)
    from sentinelops.ml.corpus import load_training_rows
    rows = load_training_rows(store, report)
    # Validate every service before writing any artifact. All final/test refs stay closed.
    artifacts = []
    output.mkdir(parents=True, exist_ok=False)
    for service in sorted(report["services"]):
        train = [r for r in rows if r["service"] == service and r["split"] == "train"]
        calibration = [r for r in rows if r["service"] == service and r["split"] == "calibration" and r["healthy"]]
        detector = fit_detector([r["features"] for r in train if r["healthy"]], [r["features"] for r in calibration], report["ordered_features"])
        risk = fit_risk([r["features"] for r in train], [r["risk_label"] for r in train], report["ordered_features"])
        manifest = save_bundle(output / stable_id(service), detector, risk=risk, provenance={"origin": "admitted-gamma",
            "dataset_report": report, "dataset_ref": dataset_ref, "service": service, "source_profile": report["source_profile"],
            "feature_set_version": report["feature_set_version"], "training_rows_sha256": stable_id(train),
            "calibration_rows_sha256": stable_id(calibration)})
        artifacts.append({"service": service, "model_version": manifest["model_version"], "activation_allowed": False})
    result = {"training_manifest_version": "gamma-fit/v1", "dataset_ref": dataset_ref, "models": artifacts,
              "activation_allowed": False, "holdout_opened": False}
    (output / "training-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def train_local_http(store, dataset_ref: str, output: Path):
    """Fit actual estimators only after recomputing every local source certificate."""
    from sentinelops.ml.corpus import load_training_rows
    from sentinelops.public_data.policy import load_training_policy, policy_hash
    report = store.read(dataset_ref)
    rows = load_training_rows(store, report)  # no output/estimator before this gate
    if report['dataset_version'] != 'local-http-admission/v1':
        raise ValueError('local fitting cannot substitute historical or public source rows')
    from sentinelops.experiments.local_corpus import clean_source, read_binding
    from sentinelops.ml.readiness import DatasetNotReady
    try:
        frozen_ref, frozen = read_binding(store, store.root/'dataset-freeze.json')
    except FileNotFoundError as exc:
        raise DatasetNotReady('prospective source admission is not frozen') from exc
    if frozen_ref != dataset_ref or frozen != report:
        raise DatasetNotReady('prospective fitting differs from the frozen admission inventory')
    fitting_commit = clean_source(Path(__file__).resolve().parents[3])
    artifacts = []
    output.mkdir(parents=True, exist_ok=False)
    for service in sorted(report['services']):
        train = [r for r in rows if r['service'] == service and r['split'] == 'train']
        calibration = [r for r in rows if r['service'] == service and r['split'] == 'calibration' and r['healthy']]
        risk_train = [r for r in train if type(r['risk_label']) is int and r['risk_label'] in (0,1)]
        detector = fit_detector([r['features'] for r in train if r['healthy']],
                                [r['features'] for r in calibration], report['ordered_features'])
        risk = fit_risk([r['features'] for r in risk_train], [r['risk_label'] for r in risk_train], report['ordered_features'])
        provenance = {'origin':'admitted-local-http','dataset_report':report,'dataset_ref':dataset_ref,
            'service':service,'source_profile':report['source_profile'],'feature_set_version':report['feature_set_version'],
            'training_policy':load_training_policy(),'training_data_policy_hash':policy_hash(load_training_policy()),
            'training_rows_sha256':stable_id(train),'calibration_rows_sha256':stable_id(calibration),
            'risk_training_rows_sha256':stable_id(risk_train),'risk_horizon_seconds':300}
        provenance['fitting_source_commit'] = fitting_commit
        directory = output / stable_id(service)
        manifest = save_bundle(directory,detector,risk=risk,provenance=provenance,source_store=store)
        artifacts.append({'service':service,'model_version':manifest['model_version'],
                          'bundle_directory':str(directory),'activation_allowed':False})
    result = {'training_manifest_version':'local-http-fit/v1','dataset_ref':dataset_ref,'models':artifacts,
              'activation_allowed':False,'holdout_opened':False}
    (output/'training-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
