"""Pinned artifact inference and accepted-history causality; fitting only disposable test estimators."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest
pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xgboost")

from sentinelops.ml.model_benchmark_scoring import baseline_scores
from sentinelops.ml.models import load_bundle
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from test_health_source import POLICY, START, capture

BUNDLE = Path('/home/viet/.local/share/sentinelops/transport/alibaba-de38fc6b-for-B/bundle')


@pytest.fixture
def portable_bundle(tmp_path):
    from test_alibaba_bundle import synthetic_fit
    from sentinelops.ml.alibaba_bundle import package_fit
    bundle = tmp_path / 'bundle'
    package_fit(synthetic_fit(tmp_path), bundle)
    return bundle


@pytest.mark.parametrize('real_bundle', [False, True], ids=['portable', 'de38fc6b'])
def test_pinned_baseline_history_and_suffix(tmp_path, portable_bundle, real_bundle):
    bundle = BUNDLE if real_bundle else portable_bundle
    if not bundle.exists():
        pytest.skip('local de38fc6b development bundle unavailable')
    digest = hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest()
    manifest, _, risk = load_bundle(bundle, expected_manifest_sha256=digest)
    store = ArtifactStore(tmp_path)
    worker = HealthWorker(None, store, POLICY, endpoint='http://127.0.0.1:9090',
                          environment='fixture', configuration_hash='a'*64, source_profile=SOURCE_PROFILE_V2)
    previous = None
    for minute in range(66):
        end = START + timedelta(minutes=minute)
        raws = [capture(s, end, profile=SOURCE_PROFILE_V2) for s in BUSINESS_ROUTES]
        previous = store.write('manifests', {'stream_id': worker.stream_id, 'identity': worker.identity,
            'bucket_end': timestamp(end), 'evaluated_at': timestamp(end+timedelta(seconds=40)),
            'previous_batch_ref': previous, 'current_raw_refs': [store.write('raw', r) for r in raws]})
    anchor = previous
    cutoff = end + timedelta(seconds=40)
    def score(ref=anchor, at=cutoff):
        return baseline_scores(store, ref, 'payment-service', as_of=at,
                               bundle=bundle, manifest_sha256=digest)
    result = score()
    frame = store.read(result['feature_ref'])
    values = [frame['features'][k] for k in manifest['ordered_features']]
    assert result['scores']['xgboost']['score'] == float(risk.predict_proba([values])[0][1])
    assert result['scores']['slope']['score'] == max(0., frame['features']['rt_slope_rel_per_min'])
    before = store.read(anchor)
    store.write('manifests', {**before, 'bucket_end': timestamp(end+timedelta(minutes=1)),
                             'previous_batch_ref': anchor})
    assert score() == result and store.read(anchor) == before
    late = store.write('manifests', {**before, 'evaluated_at': timestamp(cutoff+timedelta(seconds=1))})
    assert score(late)['feature_ref'] is None
    assert score(at=end+timedelta(minutes=1))['scores']['slope']['score'] is None
    with pytest.raises(ValueError, match='checksum'):
        baseline_scores(store, anchor, 'payment-service', as_of=cutoff,
                        bundle=bundle, manifest_sha256='0'*64)
    path = store.root / result['feature_ref'].removeprefix('artifact://')
    path.write_text(json.dumps({**frame, 'service': 'tampered'}))
    with pytest.raises(ValueError, match='checksum'):
        store.read(result['feature_ref'])


@pytest.mark.parametrize('bad', ['config', 'order'])
def test_wrong_config_and_order_rejected(tmp_path, monkeypatch, bad):
    from sentinelops.ml import model_benchmark_scoring as scoring
    from sentinelops.ml.alibaba_training import load_config
    from sentinelops.telemetry.core import stable_id
    config = load_config()
    manifest = {'provenance': {'config': config, 'config_sha256': stable_id(config)},
                'ordered_features': list(config['features'])}
    if bad == 'config':
        config['window_minutes'] = 6
    else:
        manifest['ordered_features'].reverse()
    monkeypatch.setattr(scoring, 'load_bundle', lambda *a, **k: (manifest, None, object()))
    with pytest.raises(ValueError, match='config or feature order'):
        scoring.baseline_scores(ArtifactStore(tmp_path), 'unused', 'payment-service',
                                as_of=START, bundle=tmp_path, manifest_sha256='0'*64)


@pytest.mark.parametrize('probability', [float('nan'), float('inf'), -0.1, 1.1])
def test_invalid_probability_rejected(tmp_path, portable_bundle, monkeypatch, probability):
    from types import SimpleNamespace
    from sentinelops.ml import model_benchmark_scoring as scoring
    digest = hashlib.sha256((portable_bundle / 'manifest.json').read_bytes()).hexdigest()
    manifest, detector, _ = load_bundle(portable_bundle, expected_manifest_sha256=digest)
    fake = SimpleNamespace(predict_proba=lambda _: [[0., probability]])
    monkeypatch.setattr(scoring, 'load_bundle', lambda *a, **k: (manifest, detector, fake))
    config = manifest['provenance']['config']
    monkeypatch.setattr(scoring, 'project_relative_window', lambda *a, **k: {
        'feature_set_version': config['feature_set_version'],
        'features': dict.fromkeys(config['features'], 0.)})
    store = ArtifactStore(tmp_path / 'source')
    ref = store.write('manifests', {'bucket_end': timestamp(START)})
    with pytest.raises(ValueError, match='probability'):
        scoring.baseline_scores(store, ref, 'payment-service', as_of=START,
                                bundle=portable_bundle, manifest_sha256=digest)


def test_missing_risk_estimator_rejected(tmp_path, portable_bundle, monkeypatch):
    from sentinelops.ml import model_benchmark_scoring as scoring
    digest = hashlib.sha256((portable_bundle / 'manifest.json').read_bytes()).hexdigest()
    manifest, detector, _ = load_bundle(portable_bundle, expected_manifest_sha256=digest)
    monkeypatch.setattr(scoring, 'load_bundle', lambda *a, **k: (manifest, detector, None))
    with pytest.raises(ValueError, match='risk estimator'):
        scoring.baseline_scores(ArtifactStore(tmp_path), 'unused', 'payment-service', as_of=START,
                                bundle=portable_bundle, manifest_sha256=digest)
