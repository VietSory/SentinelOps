"""Artifact-backed fixture caller checks; no fitted-release claim or activation."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest
pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xgboost")
torch = pytest.importorskip("torch", reason="optional CPU research dependency; required in ltfp-cpu CI")

from sentinelops.ml.alibaba_bundle import package_fit
from sentinelops.ml.alibaba_live import project_graph_window
from sentinelops.ml.alibaba_training import load_config
from sentinelops.ml.ltfp_model import Forecast, export, load, metadata, predict
from sentinelops.ml.model_benchmark_runner import score_batch
from sentinelops.ml.models import fit_exceedance_risk
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from test_alibaba_bundle import synthetic_fit
from test_health_source import POLICY, capture
from test_local_labels import START, derive, fixture_oracle


@pytest.fixture
def batch_case(tmp_path):
    store = ArtifactStore(tmp_path / 'history')
    worker = HealthWorker(None, store, POLICY, endpoint='http://127.0.0.1:9090',
                          environment='fixture', configuration_hash='a'*64, source_profile=SOURCE_PROFILE_V2)
    previous = None
    for minute in range(-64, 6):
        end = START + timedelta(minutes=minute)
        raws = [capture(s, end, errors=0, profile=SOURCE_PROFILE_V2) for s in BUSINESS_ROUTES]
        previous = store.write('manifests', {'stream_id': worker.stream_id, 'identity': worker.identity,
            'bucket_end': timestamp(end), 'evaluated_at': timestamp(end+timedelta(seconds=30)),
            'previous_batch_ref': previous, 'current_raw_refs': [store.write('raw', raw) for raw in raws]})
    as_of = end + timedelta(seconds=30)
    source = {'service': 'payment-service', 'window_start': timestamp(START),
              'window_end': timestamp(end), 'as_of': timestamp(as_of), 'fixture_source': True}
    config = load_config()
    topology = {'identity': 'fixture-topology', 'observed_at': timestamp(START),
                'directed_edges': [('checkout-service', 'payment-service'),
                                   ('checkout-service', 'inventory-service')]}
    graph = project_graph_window(store, previous, node_ids=list(BUSINESS_ROUTES),
                                 topology=topology, as_of=as_of, config=config)
    bundle = tmp_path / 'bundle'
    packaged = package_fit(synthetic_fit(tmp_path), bundle)
    models = {}
    for name, variant in (('ltfp_graph', False), ('ltfp_self_only', True)):
        identity = metadata(config_sha256=graph['config_sha256'], graph_sha256='fit-topology-fixture',
                            self_only=variant, seed=42, train_split='fixture-train', calibration_split='fixture-cal')
        torch.manual_seed(42)
        digest = export(Forecast(), identity, tmp_path / name)
        models[name] = dict(directory=tmp_path/name, metadata_sha256=digest, identity=identity)
    return store, dict(batch_ref=previous, anchor=timestamp(end), as_of=timestamp(as_of),
        service='payment-service', source_feature_ref=store.write('features', source),
        node_ids=list(BUSINESS_ROUTES), topology=topology, expected_graph_sha256=graph['graph_sha256'],
        config=config, bundle=bundle, manifest_sha256=packaged['manifest_sha256'],
        oracle=derive(fixture_oracle()), health_policy=POLICY, run_id='fixture-run', group_id='fixture-group',
        models=models, fixture_scope=True)


def test_four_scores_shared_frame_and_future_suffix(batch_case):
    store, args = batch_case
    before = deepcopy(args)
    rows = score_batch(store, **args)
    assert len(rows) == 4 and all(row['risk_label'] == 0 for row in rows)
    assert len({r['feature_ref'] for r in rows}) == 1
    assert rows[0]['feature_ref'] != args['source_feature_ref']
    assert all(row['source_feature_ref'] == args['source_feature_ref'] for row in rows)
    assert all(row['score'] is not None and not row['activation_allowed'] for row in rows)
    assert all(row['model_provenance']['scope'] == 'fixture' for row in rows if row['model'].startswith('ltfp'))
    frame = store.read(rows[0]['feature_ref'])
    for name, spec in args['models'].items():
        graph = project_graph_window(store, args['batch_ref'], node_ids=args['node_ids'],
            topology=args['topology'], as_of=START+timedelta(minutes=5, seconds=30),
            config=args['config'], self_only=spec['identity']['self_only'])
        index = graph['node_ids'].index(args['service'])
        assert graph['values'][index][-1] == [frame['features'][key] for key in args['config']['features']]
        model = load(spec['directory'], metadata_sha256=spec['metadata_sha256'], expected_identity=spec['identity'])
        expected = predict(model, spec['identity'], graph, expected_graph_sha256=args['expected_graph_sha256'])
        assert next(r for r in rows if r['model'] == name)['score'] == expected[args['service']]
    anchor = store.read(args['batch_ref'])
    store.write('manifests', {**anchor, 'bucket_end': timestamp(START+timedelta(minutes=6)),
                             'previous_batch_ref': args['batch_ref']})
    assert score_batch(store, **args) == rows
    assert args == before


def test_missing_models_untrained_and_missing_history(batch_case):
    store, args = batch_case
    rows = score_batch(store, **{**args, 'models': {}})
    assert all(r['score'] is None and r['score_missing_reason'] == 'model-artifact-unavailable'
               for r in rows if r['model'].startswith('ltfp'))
    rows = score_batch(store, **{**args, 'fixture_scope': False})
    assert all(r['score'] is None for r in rows if r['model'].startswith('ltfp'))
    xgboost, = (row for row in rows if row['model'] == 'xgboost')
    assert xgboost['score'] is None and xgboost['score_missing_reason'] == 'benchmark-refit-required'
    anchor = store.read(args['batch_ref'])
    late = store.write('manifests', {**anchor, 'evaluated_at': timestamp(START+timedelta(minutes=6))})
    rows = score_batch(store, **{**args, 'batch_ref': late})
    assert len(rows) == 4 and all(r['score'] is None for r in rows)
    assert all(r['feature_ref'] == args['source_feature_ref'] and r['risk_label'] == 0 for r in rows)


def test_xgboost_uses_the_pinned_paired_refit(batch_case, tmp_path):
    store, args = batch_case
    config = args['config']
    values = [[float(index) / 10, 0.0, 0.0, 0.0, float(index) / 10, 0.0] for index in range(40)]
    risk = fit_exceedance_risk(values, [int(index >= 20) for index in range(40)], [1.0] * 40,
                               config['features'], monotone_constraints=[1, 1, 1],
                               estimator={'n_estimators': 4, 'max_depth': 2, 'learning_rate': .1})
    directory = tmp_path / 'refit'
    directory.mkdir()
    risk.save_model(directory / 'risk.json')
    model_sha = hashlib.sha256((directory / 'risk.json').read_bytes()).hexdigest()
    from sentinelops.telemetry.core import stable_id
    manifest = {'scope': 'development-train-only', 'activation_allowed': False,
                'config_sha256': stable_id(config), 'model_sha256': model_sha,
                'paired_fit_input_sha256': 'a' * 64}
    content = json.dumps(manifest, sort_keys=True).encode()
    (directory / 'manifest.json').write_bytes(content)
    spec = {'directory': directory, 'manifest_sha256': hashlib.sha256(content).hexdigest(),
            'fit_input_sha256': 'a' * 64}
    rows = score_batch(store, **{**args, 'fixture_scope': False, 'xgboost_refit': spec})
    row, = (item for item in rows if item['model'] == 'xgboost')
    frame = store.read(row['feature_ref'])
    expected = risk.predict_proba([[frame['features'][name] for name in config['features']]])[0][1]
    assert row['score'] == pytest.approx(expected, abs=1e-12)
    assert row['model_provenance']['scope'] == 'development-refit'
    assert row['model_provenance']['model_sha256'] == model_sha
    with pytest.raises(ValueError, match='manifest SHA256'):
        score_batch(store, **{**args, 'xgboost_refit': {**spec, 'manifest_sha256': '0' * 64}})
    with pytest.raises(ValueError, match='paired training identity'):
        score_batch(store, **{**args, 'xgboost_refit': {**spec, 'fit_input_sha256': 'b' * 64}})
    (directory / 'risk.json').write_bytes(b'changed')
    with pytest.raises(ValueError, match='paired training identity'):
        score_batch(store, **{**args, 'xgboost_refit': spec})


@pytest.mark.parametrize('bad', ['graph', 'config', 'metadata', 'variant', 'source-cutoff', 'anchor'])
def test_pins_and_clocks_rejected(batch_case, bad):
    store, args = batch_case
    args = deepcopy(args)
    if bad == 'graph': args['expected_graph_sha256'] = 'wrong'
    elif bad == 'config': args['config']['features'].reverse()
    elif bad == 'metadata': args['models']['ltfp_graph']['metadata_sha256'] = 'wrong'
    elif bad == 'variant': args['models']['ltfp_graph']['identity']['self_only'] = True
    elif bad == 'source-cutoff': args['as_of'] = timestamp(START+timedelta(minutes=5, seconds=31))
    else: args['anchor'] = timestamp(START+timedelta(minutes=6))
    with pytest.raises(ValueError): score_batch(store, **args)


def test_advanced_cutoff_preserves_unknown_and_unavailable(batch_case):
    store, args = batch_case
    source = store.read(args['source_feature_ref'])
    source['as_of'] = timestamp(START+timedelta(minutes=6))
    args = {**args, 'as_of': source['as_of'], 'source_feature_ref': store.write('features', source)}
    rows = score_batch(store, **args)
    assert len(rows) == 4 and all(r['score'] is None and r['risk_label'] is None for r in rows)


@pytest.mark.parametrize('missing_service', ['inventory-service', 'payment-service'])
def test_missing_neighbor_or_target(batch_case, missing_service):
    store, args = batch_case
    batch = store.read(args['batch_ref'])
    refs = []
    for ref in batch['current_raw_refs']:
        raw = store.read(ref)
        if raw['service'] == missing_service:
            raw['collected_at'] = timestamp(START+timedelta(minutes=5, seconds=31))
            ref = store.write('raw', raw)
        refs.append(ref)
    changed = store.write('manifests', {**batch, 'current_raw_refs': refs})
    rows = score_batch(store, **{**args, 'batch_ref': changed})
    assert len(rows) == 4
    assert all((row['score'] is None) == (missing_service == args['service']) for row in rows)
    assert all(row['risk_label'] == 0 for row in rows)
    for row in rows:
        if row['model'].startswith('ltfp'):
            assert row['graph_provenance']['raw_refs']
            assert row['graph_provenance']['input_sha256']
