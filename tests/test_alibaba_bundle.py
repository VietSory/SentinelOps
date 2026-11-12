"""ADR-016 v3 bundle round trip; synthetic fits are never released."""
import hashlib
import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xgboost")

from sentinelops.ml.alibaba_bundle import package_fit
from sentinelops.ml.alibaba_training import load_config
from sentinelops.ml.models import fit_detector, fit_risk, load_bundle
from sentinelops.telemetry.core import stable_id


def synthetic_fit(tmp_path):
    import joblib
    from sentinelops.ml.models import fit_exceedance_risk
    config = load_config()
    rng = np.random.default_rng(1)
    width = len(config['features'])
    train, cal = rng.normal(size=(400, width)), rng.normal(size=(150, width))
    detector = fit_detector(train, cal, config['features'], detector_features=config['detector']['features'])
    labels = (train[:, 0] + train[:, 5] > 1).astype(int)
    risk = fit_exceedance_risk(train, labels, np.ones(len(labels)), config['features'],
                               monotone_constraints=config['risk']['monotone_constraints'],
                               estimator={'n_estimators': 20, 'max_depth': 3, 'learning_rate': .1})
    fit = tmp_path / 'fit'
    fit.mkdir()
    joblib.dump(detector, fit / 'detector.joblib')
    risk.save_model(fit / 'risk.json')
    report = {'report_version': 'alibaba-relative-development/v2', 'activation_allowed': False, 'config': config,
              'config_sha256': stable_id(config), 'fitting_source_commit': 'a' * 40, 'thresholds': detector.thresholds,
              'risk_thresholds': {'watch': .5, 'degrading': .8}, 'development_fit': False,
              'fault_shape_gate': {'passed': True}, 'evaluation': {'fixture': True},
              'source_archives': [{'archive': 'MCRRTUpdate_0.tar.gz', 'archive_sha256': 'b' * 64, 'parquet_sha256': 'c' * 64}],
              'counts': {}}
    (fit / 'report.json').write_text(json.dumps(report, default=float))
    return fit


def test_v3_bundle_round_trips_through_the_registry_loader(tmp_path):
    result = package_fit(synthetic_fit(tmp_path), tmp_path / 'bundle')
    manifest, detector, risk = load_bundle(tmp_path / 'bundle', expected_manifest_sha256=result['manifest_sha256'])
    assert manifest['origin'] == 'admitted-alibaba-v2022' and manifest['feature_set_version'] == 'http-relative-5m/v2'
    assert manifest['activation_allowed'] is False and risk is not None
    assert list(detector.ordered_features) == load_config()['features']


def test_tampered_v3_bundle_is_rejected(tmp_path):
    result = package_fit(synthetic_fit(tmp_path), tmp_path / 'bundle')
    path = tmp_path / 'bundle' / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['provenance']['config']['window_minutes'] = 10   # config no longer matches its hash
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_bundle(tmp_path / 'bundle', expected_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(FileExistsError):
        package_fit(tmp_path / 'fit', tmp_path / 'bundle')


def test_local_epoch_pins_ali_bundle_and_rejects_changed_artifact(tmp_path):
    from sentinelops.experiments.local_campaign import register_alibaba_epoch
    from sentinelops.experiments.local_corpus import ALIBABA_PLAN, load_plan, read_alibaba_epoch
    from sentinelops.telemetry.store import ArtifactStore

    bundle = tmp_path / 'bundle'
    packaged = package_fit(synthetic_fit(tmp_path), bundle)
    store = ArtifactStore(tmp_path / 'epoch')
    plan = load_plan(ALIBABA_PLAN)
    digest = packaged['manifest_sha256']
    wrong = {**plan, 'candidate': {'model_version': 'f' * 64, 'manifest_sha256': digest}}
    with pytest.raises(ValueError, match='different candidate'):
        register_alibaba_epoch(store, wrong, 'a' * 40, bundle, digest)
    plan = {**plan, 'candidate': {'model_version': packaged['model_version'], 'manifest_sha256': digest}}
    pinned = register_alibaba_epoch(store, plan, 'a' * 40, bundle, digest)
    reference, epoch = read_alibaba_epoch(store, plan)
    assert reference == pinned
    assert epoch['model_version'] == packaged['model_version']
    assert epoch['manifest_sha256'] == digest
    (bundle / 'risk.json').write_bytes(b'changed')
    with pytest.raises(ValueError, match='risk.json differs from its pin'):
        register_alibaba_epoch(store, plan, 'a' * 40, bundle, digest)


@pytest.mark.parametrize('change', ['development', 'gate-failed', 'no-evaluation'])
def test_incomplete_or_gate_failed_fit_never_becomes_a_bundle(tmp_path, change):
    fit = synthetic_fit(tmp_path)
    report = json.loads((fit / 'report.json').read_text())
    if change == 'development':
        report['development_fit'] = True
    elif change == 'gate-failed':
        report['fault_shape_gate'] = {'passed': False}
    else:
        report['evaluation'] = None
    (fit / 'report.json').write_text(json.dumps(report, default=float))
    with pytest.raises(ValueError):
        package_fit(fit, tmp_path / 'bundle')
    assert not (tmp_path / 'bundle').exists()


def test_loaded_risk_scores_only_breach_relative_latency(tmp_path):
    result = package_fit(synthetic_fit(tmp_path), tmp_path / 'bundle')
    _, detector, risk = load_bundle(tmp_path / 'bundle', expected_manifest_sha256=result['manifest_sha256'])
    row = np.zeros((1, len(load_config()['features'])))
    other = row.copy(); other[0, 2] = 5.; other[0, 3] = -3.          # traffic features only
    assert risk.predict_proba(row)[0, 1] == pytest.approx(risk.predict_proba(other)[0, 1])
    closer = row.copy(); closer[0, 5] = 1.                             # breach level nearer the baseline
    assert risk.predict_proba(closer)[0, 1] >= risk.predict_proba(row)[0, 1]
    assert detector.predict(other)['isolation_forest']['raw_score'][0] == pytest.approx(
        detector.predict(row)['isolation_forest']['raw_score'][0])
