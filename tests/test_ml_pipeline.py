"""Numerical fits below are software fixtures, never accepted dataset/model evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xgboost")

from fastapi.testclient import TestClient
from sentinelops.api import create_app
from sentinelops.ml.evaluation import event_metrics
from sentinelops.ml.models import fit_detector, fit_risk, save_bundle, load_bundle, train_gamma, matrix
from sentinelops.ml.readiness import audit_readiness, DatasetNotReady
from sentinelops.ml.registry import ModelRegistry, ModelUnavailable, IncompatibleFeatures
from sentinelops.telemetry.core import FEATURE_KEYS, timestamp
from sentinelops.telemetry.store import ArtifactStore


@pytest.fixture(scope="module")
def fitted():
    rng = np.random.default_rng(42)
    train = rng.normal(size=(320, 5))
    cal = rng.normal(100, 1, size=(110, 5))
    return fit_detector(train, cal, FEATURE_KEYS), train, cal


def test_train_only_transforms_and_distinct_baseline(fitted):
    detector, train, cal = fitted
    assert np.array_equal(detector.median, np.median(train, axis=0))
    assert np.array_equal(detector.scaler.center_, np.median(train, axis=0))
    frozen = deepcopy(detector.thresholds)
    result = detector.predict(np.ones((2, 5)) * 1000)
    assert set(result) == {"isolation_forest", "median_mad"}
    assert all(np.isfinite(r["score"]).all() for r in result.values())
    assert detector.thresholds == frozen
    assert np.array_equal(detector.raw_scores(cal)["isolation_forest"], -detector.forest.score_samples(detector.scaler.transform(cal)))


def test_real_fixture_artifact_roundtrip_and_risk_fit(tmp_path, fitted):
    detector, train, cal = fitted
    risk = fit_risk(train, (train[:, 0] > 0).astype(int), FEATURE_KEYS)
    manifest = save_bundle(tmp_path / "fixture", detector, risk=risk, provenance={"origin": "fixture-only"})
    assert manifest["activation_allowed"] is False
    digest = hashlib.sha256((tmp_path / "fixture/manifest.json").read_bytes()).hexdigest()
    loaded, model, classifier = load_bundle(tmp_path / "fixture", expected_manifest_sha256=digest)
    assert loaded == manifest
    assert np.array_equal(model.predict(cal)["isolation_forest"]["score"], detector.predict(cal)["isolation_forest"]["score"])
    assert np.array_equal(classifier.predict_proba(train[:3]), risk.predict_proba(train[:3]))
    with (tmp_path / "fixture/detector.joblib").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        load_bundle(tmp_path / "fixture", expected_manifest_sha256=digest)


def test_fixture_cannot_be_promoted_even_with_forged_approval(tmp_path, fitted):
    store = ArtifactStore(tmp_path)
    manifest = save_bundle(tmp_path / "fixture", fitted[0], provenance={"origin": "fixture-only"})
    evidence = store.write("manifests", {"type": "fixture-evaluation"})
    validation = store.write("manifests", {"model_version": manifest["model_version"], "G2": "PASS", "G3": "PASS",
        "source_parity": "PASS", "activation_allowed": True, "evaluation_refs": [evidence]})
    release = store.write("manifests", {"release_version": "validated-model-release/v1", "models": [{"bundle_directory": "fixture",
        "manifest_sha256": hashlib.sha256((tmp_path / "fixture/manifest.json").read_bytes()).hexdigest(), "validation_ref": validation}]})
    with pytest.raises(ModelUnavailable):
        ModelRegistry.load(store, release)


@pytest.mark.parametrize("values", [[], [[None] * 5], [[float("nan")] * 5], [[True] * 5], [[1] * 4], [["1"] * 5]])
def test_missing_nonfinite_boolean_or_wrong_schema_rejected(values):
    with pytest.raises(ValueError):
        matrix(values, 5)


def test_exact_readiness_and_no_invalid_training_artifact(tmp_path):
    recorded = json.loads(Path("docs/reports/evidence/2026-10-26-gamma-grouped-dataset.json").read_text())
    audit = audit_readiness(recorded)
    assert (audit["candidate_windows"], audit["feature_eligible_windows"], audit["excluded_windows"]) == (29610, 22771, 6839)
    assert audit["status"] == "BLOCKED" and audit["verified_precursor_runs"] == 0
    assert all(row["train"] == row["calibration"] == 0 for row in audit["certified_business_service_health"].values())
    store = ArtifactStore(tmp_path / "store")
    ref = store.write("manifests", recorded)
    with pytest.raises(DatasetNotReady):
        train_gamma(store, ref, tmp_path / "models")
    assert not (tmp_path / "models").exists()


def test_default_registry_and_api_fail_closed_without_scores():
    feature = {"schema_version": "1.0", "service": "payment-service", "window_start": "2026-10-29T00:00:00Z",
               "window_end": "2026-10-29T00:05:00Z", "features": {k: 1. for k in FEATURE_KEYS}}
    registry = ModelRegistry()
    with pytest.raises(ModelUnavailable): registry.infer(feature, source_profile="fixture")
    with TestClient(create_app(model_registry=registry)) as client:
        response = client.post("/v1/detect", json=feature)
        assert response.status_code == 503 and response.json()["code"] == "model_unavailable"
        assert "anomaly_score" not in response.json()
        assert client.post("/v1/risk", json=feature).status_code == 503


def test_registry_checks_source_window_and_finite_feature_before_real_inference(fitted):
    # Trusted route injection tests real inference numerics only; it is not promotion.
    now = datetime.now(timezone.utc)
    detector = fitted[0]
    registry = ModelRegistry()
    registry._models["payment-service"] = ({"model_version": "fixture-only", "ordered_features": list(FEATURE_KEYS),
        "provenance": {"source_profile": "fixture"}}, detector, None, "fixture://not-approved")
    feature = {"schema_version": "1.0", "service": "payment-service", "window_start": timestamp(now - timedelta(minutes=5)),
        "window_end": timestamp(now), "features": {k: 1. for k in FEATURE_KEYS}}
    output = registry.infer(feature, source_profile="fixture", now=now)
    assert output["risk"] is None and output["detection"]["model_version"] == "fixture-only"
    for problem in ("source", "stale", "null", "extra"):
        changed = deepcopy(feature)
        if problem == "stale": changed["window_end"] = timestamp(now - timedelta(minutes=4))
        if problem == "null": changed["features"][FEATURE_KEYS[0]] = None
        if problem == "extra": changed["features"]["root_cause_ground_truth"] = 1
        with pytest.raises(IncompatibleFeatures):
            registry.infer(changed, source_profile="wrong" if problem == "source" else "fixture", now=now)


def test_real_fixture_xgboost_risk_uses_existing_contract_and_api(fitted):
    detector, train, _ = fitted
    risk_model = fit_risk(train, (train[:, 0] > 0).astype(int), FEATURE_KEYS)
    now = datetime.now(timezone.utc)
    registry = ModelRegistry()
    registry._models["payment-service"] = ({"model_version": "fixture-only", "ordered_features": list(FEATURE_KEYS),
        "provenance": {"source_profile": "fixture"}, "risk_thresholds": {"watch": .5, "degrading": .8}},
        detector, risk_model, "fixture://not-approved")
    feature = {"schema_version": "1.0", "service": "payment-service", "window_start": timestamp(now - timedelta(minutes=5)),
        "window_end": timestamp(now), "features": dict(zip(FEATURE_KEYS, train[0].tolist()))}
    result = registry.infer(feature, source_profile="fixture", now=now)
    assert result["risk"]["risk_score"] == float(risk_model.predict_proba([train[0]])[0, 1])
    with TestClient(create_app(model_registry=registry)) as client:
        response = client.post("/v1/risk", json=feature, headers={"X-SentinelOps-Source-Profile": "fixture"})
        assert response.status_code == 200
        assert response.json()["model_name"] == "xgboost" and response.json()["model_version"] == "fixture-only"


def test_event_metrics_keep_duplicates_misses_and_real_emission_delays():
    runs = [{"run_id": "a", "eligible_healthy_service_seconds": 3600, "events": [{"service": "payment", "onset_at": "2026-01-01T00:02:00Z",
              "deadline_at": "2026-01-01T00:05:00Z", "gradual_precursor_verified": True, "precursor_start_at": "2026-01-01T00:01:00Z"}],
              "alerts": [{"service": "payment", "emitted_at": "2026-01-01T00:02:10Z"}] * 2,
              "risk_warnings": [{"service": "payment", "emitted_at": "2026-01-01T00:01:10Z"}]},
             {"run_id": "b", "events": [{"service": "payment", "onset_at": "2026-01-01T01:00:00Z", "deadline_at": "2026-01-01T01:05:00Z"}], "alerts": []}]
    result = event_metrics(runs)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 1)
    assert result["f1"] == .5 and result["detection_delays_seconds"] == [10.]
    assert result["measured_leads_seconds"] == [50.] and result["false_incidents_per_healthy_service_hour"] == 1


def test_self_attested_dataset_cannot_enter_fit_or_read_arbitrary_evidence(tmp_path):
    from sentinelops.ml.corpus import load_training_rows
    from sentinelops.public_data.policy import load_policy
    from sentinelops.telemetry.core import stable_id
    report = {'dataset_version': 'gamma-verified-training/v1', 'source': load_policy()['sources']['gamma'],
              'training_data_policy_hash': stable_id(load_policy()), 'training_ready': True,
              'health_labels_available': True, 'source_reference_is_healthy_certificate': True,
              'source_reference_traffic_verified': True, 'independence_established': True,
              'risk_target_available': True, 'source_audit_refs': ['forged://exists-is-not-proof']}
    class NoRead:
        def read(self, ref):
            pytest.fail('self-attestation must be rejected before any corpus/evidence read')
    with pytest.raises(DatasetNotReady, match='self-attested'):
        load_training_rows(NoRead(), report)
    store = ArtifactStore(tmp_path / 'store')
    ref = store.write('manifests', report)
    with pytest.raises(DatasetNotReady, match='self-attested'):
        train_gamma(store, ref, tmp_path / 'models')
    assert not (tmp_path / 'models').exists()


@pytest.mark.parametrize('seconds', [-300, 0, 60, 299, 301, 600])
def test_real_inference_rejects_wrong_duration_before_predict(fitted, seconds):
    now = datetime.now(timezone.utc)
    registry = ModelRegistry()
    registry._models['payment-service'] = ({'model_version': 'fixture-only', 'ordered_features': list(FEATURE_KEYS),
        'provenance': {'source_profile': 'fixture'}}, fitted[0], None, 'fixture://not-approved')
    feature = {'schema_version': '1.0', 'service': 'payment-service',
               'window_start': timestamp(now - timedelta(seconds=seconds)), 'window_end': timestamp(now),
               'features': {k: 1. for k in FEATURE_KEYS}}
    with pytest.raises(ValueError, match='precede|300-second'):
        registry.infer(feature, source_profile='fixture', now=now)


@pytest.mark.parametrize('service,features', [('foreign-service', list(FEATURE_KEYS)),
    ('payment-service', ['cpu_cores', 'memory_bytes', 'network_bytes'])])
def test_registry_rejects_wrong_route_or_feature_semantics_even_with_version_string(tmp_path, monkeypatch, service, features):
    from sentinelops.ml import models
    store = ArtifactStore(tmp_path)
    manifest = {'origin': 'admitted-gamma', 'model_version': 'fixture', 'ordered_features': features,
                'provenance': {'service': service, 'feature_set_version': 'http-red-5m/v1'}}
    monkeypatch.setattr(models, 'load_bundle', lambda *a, **k: (manifest, None, None))
    evidence = store.write('manifests', {'fixture': True})
    validation = store.write('manifests', {'model_version': 'fixture', 'G2': 'PASS', 'G3': 'PASS',
        'source_parity': 'PASS', 'activation_allowed': True, 'evaluation_refs': [evidence]})
    release = store.write('manifests', {'release_version': 'validated-model-release/v1', 'models': [
        {'bundle_directory': 'fixture', 'manifest_sha256': 'fixture', 'validation_ref': validation}]})
    with pytest.raises(IncompatibleFeatures):
        ModelRegistry.load(store, release)
