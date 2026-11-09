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
    config = load_config()
    rng = np.random.default_rng(1)
    train, cal = rng.normal(size=(400, 5)), rng.normal(size=(150, 5))
    detector = fit_detector(train, cal, config['features'])
    labels = (train[:, 0] > 1).astype(int)
    risk = fit_risk(train, labels, config['features'])
    fit = tmp_path / 'fit'
    fit.mkdir()
    joblib.dump(detector, fit / 'detector.joblib')
    risk.save_model(fit / 'risk.json')
    report = {'report_version': 'alibaba-relative-development/v1', 'activation_allowed': False, 'config': config,
              'config_sha256': stable_id(config), 'fitting_source_commit': 'a' * 40, 'thresholds': detector.thresholds,
              'source_archives': [{'archive': 'MCRRTUpdate_0.tar.gz', 'archive_sha256': 'b' * 64, 'parquet_sha256': 'c' * 64}],
              'counts': {}}
    (fit / 'report.json').write_text(json.dumps(report, default=float))
    return fit


def test_v3_bundle_round_trips_through_the_registry_loader(tmp_path):
    result = package_fit(synthetic_fit(tmp_path), tmp_path / 'bundle')
    manifest, detector, risk = load_bundle(tmp_path / 'bundle', expected_manifest_sha256=result['manifest_sha256'])
    assert manifest['origin'] == 'admitted-alibaba-v2022' and manifest['feature_set_version'] == 'http-relative-5m/v1'
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
