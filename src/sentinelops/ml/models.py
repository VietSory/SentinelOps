"""Real numerical fit/inference primitives, exercised only with fixtures until G2.

Official fitting is ADR-016 (`scripts/train_alibaba.py`, bundle v3 in
`ml/alibaba_bundle`). Local-data fitting was retired on 2026-11-09 with owner
approval; local systems are evaluation only. Historical GAMMA remains blocked. Numerical helpers never certify a dataset,
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
    # ADR-019: the subset IF and the MAD control score; None keeps every ordered feature (earlier bundles).
    detector_features: tuple[str, ...] | None = None

    def selected(self, values):
        array = matrix(values, len(self.ordered_features))
        names = getattr(self, "detector_features", None)
        return array if not names else array[:, [self.ordered_features.index(name) for name in names]]

    def raw_scores(self, values):
        array = self.selected(values)
        return {"isolation_forest": -self.forest.score_samples(self.scaler.transform(array)),
                "median_mad": np.max(np.abs(array - self.median) / self.mad_scale, axis=1)}

    def predict(self, values):
        scores = self.raw_scores(values)
        return {name: {"score": np.searchsorted(reference, scores[name], side="right") / len(reference),
                       "alert": scores[name] > self.thresholds[name], "raw_score": scores[name]}
                for name, reference in (("isolation_forest", self.calibration_if), ("median_mad", self.calibration_mad))}


def fit_detector(train, calibration, ordered_features, *, seed=42, false_alert_budget=.01, detector_features=None):
    """Low-level fit, not admission. Callers supply disjoint eligible matrices."""
    keys = tuple(ordered_features)
    if not keys or len(set(keys)) != len(keys) or any(not isinstance(k, str) or not k for k in keys):
        raise ValueError("ordered feature registry is invalid")
    if not 0 < false_alert_budget < .5 or type(seed) is not int:
        raise ValueError("invalid predeclared seed/calibration budget")
    subset = None if detector_features is None else tuple(detector_features)
    if subset is not None and (not subset or len(set(subset)) != len(subset) or any(k not in keys for k in subset)):
        raise ValueError("detector features must be a distinct subset of the ordered features")
    fit, cal = matrix(train, len(keys)), matrix(calibration, len(keys))
    if subset:
        columns = [keys.index(name) for name in subset]
        fit, cal = fit[:, columns], cal[:, columns]
    if len(fit) < 300 or len(cal) < 100:
        raise ValueError("fit requires at least 300 train and 100 calibration rows")
    scaler = RobustScaler().fit(fit)
    forest = IsolationForest(n_estimators=100, max_samples="auto", contamination="auto", random_state=seed, n_jobs=1)
    forest.fit(scaler.transform(fit))
    median = np.median(fit, axis=0)
    scale = np.maximum(1.4826 * np.median(np.abs(fit - median), axis=0), 1e-9)
    bundle = Detector(scaler, forest, median, scale, None, None, {}, keys, seed, subset)
    raw = {"isolation_forest": -forest.score_samples(scaler.transform(cal)),
           "median_mad": np.max(np.abs(cal - median) / scale, axis=1)}
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


class ExceedanceRisk:
    """ADR-019: monotone probability that latency exceeds the service's breach level within the horizon.

    Inputs are the window's latency level, peak and slope expressed relative to the breach level, so the
    same model serves a breach defined as a multiple of the baseline (training) or as an SLO (serving).
    """
    INPUTS = ("rt_log_level_rel", "rt_log_peak_rel", "rt_slope_rel_per_min", "rt_log_baseline_to_breach")

    def __init__(self, model, ordered_features):
        keys = tuple(ordered_features)
        if any(name not in keys for name in self.INPUTS):
            raise ValueError("exceedance risk needs the breach-relative latency features")
        self.model, self.ordered_features = model, keys

    def inputs(self, values):
        array = matrix(values, len(self.ordered_features))
        level, peak, slope, breach = (array[:, self.ordered_features.index(name)] for name in self.INPUTS)
        return np.column_stack([level + breach, peak + breach, slope * np.exp(breach)])

    def predict_proba(self, values):
        return self.model.predict_proba(self.inputs(values))

    def save_model(self, path):
        self.model.save_model(path)


def fit_exceedance_risk(train, labels, weights, ordered_features, *, monotone_constraints, estimator, seed=42):
    """Weighted binary fit with monotone constraints; no early stopping on calibration or evaluation labels."""
    from xgboost import XGBClassifier
    wrapper = ExceedanceRisk(None, ordered_features)
    y, w = np.asarray(labels), np.asarray(weights, dtype=float)
    inputs = wrapper.inputs(train)
    if (y.ndim != 1 or len(y) != len(inputs) or w.shape != y.shape or set(y.tolist()) != {0, 1}
            or not np.isfinite(w).all() or (w <= 0).any() or len(monotone_constraints) != inputs.shape[1]
            or any(c != 1 for c in monotone_constraints)):
        raise ValueError("exceedance risk needs both classes, positive weights and increasing constraints")
    wrapper.model = XGBClassifier(
        n_estimators=estimator["n_estimators"], max_depth=estimator["max_depth"], learning_rate=estimator["learning_rate"],
        subsample=1., colsample_bytree=1., tree_method="hist", random_state=seed, n_jobs=1, eval_metric="logloss",
        objective="binary:logistic", monotone_constraints="(" + ",".join(str(c) for c in monotone_constraints) + ")",
        scale_pos_weight=float(w[y == 0].sum() / w[y == 1].sum()))
    wrapper.model.fit(inputs, y, sample_weight=w)
    return wrapper


def save_bundle(output: Path, detector: Detector, *, provenance: dict, risk=None):
    """Exclusive trusted local bundle; never a promotion operation."""
    if provenance.get("origin") not in ("fixture-only", "admitted-gamma"):
        raise ValueError("bundle needs explicit numerical-test or admitted-source provenance")
    if provenance.get("origin") == "admitted-gamma":
        require_training_ready(provenance.get("dataset_report", {}))
    output.mkdir(parents=True, exist_ok=False)
    detector_path = output / "detector.joblib"
    joblib.dump(detector, detector_path, compress=3)
    artifacts = {"detector.joblib": hashlib.sha256(detector_path.read_bytes()).hexdigest()}
    if risk is not None:
        risk.save_model(output / "risk.json")
        artifacts["risk.json"] = hashlib.sha256((output / "risk.json").read_bytes()).hexdigest()
    manifest = {"bundle_version": "sentinelops-model/v1", "origin": provenance["origin"], "provenance": provenance,
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


def read_bundle_manifest(output: Path, *, expected_manifest_sha256: str) -> dict:
    """Validate the pinned metadata without deserializing model artifacts."""
    path = output / "manifest.json"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_manifest_sha256:
        raise ValueError("model manifest checksum mismatch")
    manifest = json.loads(data)
    if manifest.get("bundle_version") not in ("sentinelops-model/v1", "sentinelops-model/v3") or manifest["model_version"] != stable_id({k: v for k, v in manifest.items() if k != "model_version"}):
        raise ValueError("unsupported or corrupt model identity")
    if manifest["bundle_version"] == "sentinelops-model/v3":
        from sentinelops.ml.alibaba_bundle import validate_alibaba_manifest
        validate_alibaba_manifest(manifest)
    elif manifest["origin"] not in ("fixture-only", "admitted-gamma"):
        raise ValueError("local-data model bundles were retired with ADR-016")
    return manifest


def load_bundle(output: Path, *, expected_manifest_sha256: str):
    """Only trusted, checksum-pinned local artifacts; no API-supplied pickle."""
    manifest = read_bundle_manifest(output, expected_manifest_sha256=expected_manifest_sha256)
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
    if list(getattr(detector, "detector_features", None) or []) != list(manifest.get("detector_features") or []):
        raise ValueError("loaded detector scores a different feature subset than declared")
    if stable_id({"if": detector.calibration_if.tolist(), "mad": detector.calibration_mad.tolist()}) != manifest["calibration_ecdf_sha256"]:
        raise ValueError("model calibration reference differs")
    risk = None
    if "risk.json" in manifest["artifacts"]:
        from xgboost import XGBClassifier
        risk = XGBClassifier(); risk.load_model(output / "risk.json")
        if manifest.get("feature_set_version") == "http-relative-5m/v2":
            risk = ExceedanceRisk(risk, manifest["ordered_features"])
    return manifest, detector, risk
