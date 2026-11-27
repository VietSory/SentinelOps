import hashlib
import json
from datetime import datetime, timezone

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.ml import model_benchmark_forecast_inputs as inputs


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sealed_forecast_inputs_check_all_metadata_before_sources(tmp_path, monkeypatch):
    from sentinelops.ml import model_benchmark_forecast
    draft = draft_matrix()
    matrix = draft['matrix']
    candidate_pin, registration_pin = 'a' * 64, None
    registration = {'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': candidate_pin,
                    'capture_commit': 'b' * 40}
    registration_pin = write_json(tmp_path / 'capture/registration.json', registration)
    write_json(tmp_path / 'capture/matrix.json', matrix)
    monkeypatch.setattr(inputs, 'verify_candidate_freeze', lambda *args: {'protocol_sha256': 'c' * 64})
    monkeypatch.setattr(inputs, 'verify_capture_registration', lambda *args, **kwargs: registration)
    touched = []
    monkeypatch.setattr(inputs, 'load_frozen_models', lambda *args, **kwargs: touched.append('models') or {})
    monkeypatch.setattr(model_benchmark_forecast, 'thresholds_from_freeze',
                        lambda *args, **kwargs: touched.append('thresholds') or {})
    def replay(root, matrix, receipts, paths, **kwargs):
        touched.append('sources')
        return [{'group_id': group['group_id'], 'run_id': row['run_id'],
                 'source_result_ref': 'result', 'oracle_ref': 'oracle'}
                for group in matrix['groups'] for row in receipts[group['group_id']]['slots']]
    monkeypatch.setattr(inputs, 'verify_frozen_capture_sources', replay)
    with pytest.raises(ValueError, match='missing reference'):
        inputs.load_sealed_inputs(tmp_path, candidates_sha256=candidate_pin,
                                  registration_sha256=registration_pin, seal_sha256='d' * 64)
    assert touched == []
    pins = []
    for group in matrix['groups']:
        rows = []
        for index, slot in enumerate(group['slots']):
            number = group['ordinal'] * 100 + index
            rows.append({'slot_id': slot['slot_id'], 'run_id': f'run-{number:032x}', 'status': 'completed',
                'source_manifest_sha256': 'e' * 64, 'oracle_certificate_sha256': 'f' * 64,
                'raw_refs': [f'artifact://raw/{number:064x}.json']})
        receipt = {'schema_version': 'benchmark-holdout-group/v1', 'role': 'sealed-final',
                   'group_id': group['group_id'], 'matrix_sha256': draft['matrix_sha256'],
                   'candidates_sha256': candidate_pin, 'source_profile': matrix['source_profile'],
                   'clock': 'arrival-aware', 'slots': rows}
        pin = write_json(tmp_path / 'capture/groups' / group['group_id'] / 'receipt.json', receipt)
        pins.append({'group_id': group['group_id'], 'receipt_sha256': pin})
    seal = {'schema_version': 'benchmark-holdout-seal/v1', 'registration_sha256': registration_pin,
            'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': candidate_pin,
            'capture_commit': registration['capture_commit'], 'groups': pins, 'slot_count': 35,
            'capture_sealed': True, 'activation_allowed': False}
    seal_pin = write_json(tmp_path / 'capture/sealed.json', seal)
    result = inputs.load_sealed_inputs(tmp_path, candidates_sha256=candidate_pin,
                                       registration_sha256=registration_pin, seal_sha256=seal_pin)
    assert len(result['cases']) == 35 and touched == ['models', 'thresholds', 'sources']
    assert [case['kind'] for case in result['cases'][:7]] == [slot['kind'] for slot in matrix['groups'][0]['slots']]
    touched.clear()
    def reject_model(*args, **kwargs):
        raise ValueError('changed model')
    monkeypatch.setattr(inputs, 'load_frozen_models', reject_model)
    with pytest.raises(ValueError, match='changed model'):
        inputs.load_sealed_inputs(tmp_path, candidates_sha256=candidate_pin,
                                  registration_sha256=registration_pin, seal_sha256=seal_pin)
    assert touched == []
    (tmp_path / 'capture/groups/bench-final-05/receipt.json').write_text('{}')
    with pytest.raises(ValueError, match='changed reference'):
        inputs.load_sealed_inputs(tmp_path, candidates_sha256=candidate_pin,
                                  registration_sha256=registration_pin, seal_sha256=seal_pin)
    assert touched == []


def test_source_topology_uses_pinned_commit_and_excludes_unobserved_nodes(tmp_path, monkeypatch):
    from sentinelops.ml.ltfp_graph import build_graph_input
    from sentinelops.telemetry.core import stable_id

    commit = 'a' * 40
    data = b'version: 1\ndependencies:\n  checkout-service: [payment-service, inventory-service]\n  payment-service: [rds]\n  inventory-service: [rds]\n  rds: []\n'
    calls = []
    def git(args, **kwargs):
        calls.append(args)
        return 'b' * 40 + '\n' if kwargs.get('text') else data
    monkeypatch.setattr(inputs.subprocess, 'check_output', git)
    run = {'source_commit': commit, 'dirty_worktree': False, 'environment': 'local',
           'started_at': '2026-01-01T00:00:00Z'}
    projection = inputs.source_topology(run, project=tmp_path, capture_commit=commit)
    assert projection['topology']['directed_edges'] == [
        ['checkout-service', 'inventory-service'], ['checkout-service', 'payment-service']]
    assert projection['excluded_edges'] == [['inventory-service', 'rds'], ['payment-service', 'rds']]
    assert projection['topology']['identity']['sha256'] == hashlib.sha256(data).hexdigest()
    assert all(args[-1] == commit + ':configs/topology.yaml' for args in calls)
    config = {'window_minutes': 5, 'baseline': {'max_minutes': 60, 'min_minutes': 30},
              'features': ['a', 'b', 'c', 'd', 'e', 'f']}
    point = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
    graph = build_graph_input(projection['node_ids'], {}, anchor=point, as_of=point, config=config,
                              topology=projection['topology'], source_identity={'scope': 'fixture'})
    assert graph['graph_sha256'] == projection['graph_sha256'] == stable_id(graph['topology'])
    with pytest.raises(ValueError, match='registered clean capture source'):
        inputs.source_topology({**run, 'dirty_worktree': True}, project=tmp_path, capture_commit=commit)


def test_frozen_models_bind_bundle_refit_and_both_ltfp_variants(tmp_path, monkeypatch):
    pytest.importorskip('torch', reason='optional CPU research dependency; required in ltfp-cpu CI')
    from sentinelops.ml import ltfp_model, models
    from sentinelops.telemetry.core import stable_id
    import xgboost

    config = {'features': ['one', 'two']}
    config_sha = stable_id(config)
    identities = {name: {'weights_status': 'development-fitted', 'self_only': name == 'self_only',
                         'config_sha256': config_sha} for name in ('graph', 'self_only')}
    metadata = {'graph': '1' * 64, 'self_only': '2' * 64}
    fit = {'input_sha256': '3' * 64, 'models': {name: {'identity': identities[name],
           'metadata_sha256': metadata[name]} for name in identities}}
    fit_sha = write_json(tmp_path / 'preflight/development-k16-fit-v5/receipt.json', fit)
    xgb_dir = tmp_path / 'preflight/development-k16-xgboost-v5'
    xgb_dir.mkdir(parents=True)
    weights = b'frozen-xgboost-fixture'
    (xgb_dir / 'risk.json').write_bytes(weights)
    weight_sha = hashlib.sha256(weights).hexdigest()
    manifest = {'scope': 'development-train-only', 'activation_allowed': False, 'model_sha256': weight_sha,
                'paired_fit_input_sha256': fit['input_sha256'], 'config_sha256': config_sha}
    manifest_sha = write_json(xgb_dir / 'manifest.json', manifest)
    candidate = {'lanes': {'a': {'fit_sha256': fit_sha, 'ltfp_metadata_sha256': metadata,
                                 'xgboost_sha256': weight_sha}},
                 'baseline_bundle': {'manifest_sha256': '4' * 64, 'model_version': 'baseline'}}
    candidate_sha = write_json(tmp_path / 'freeze/candidates.json', candidate)
    monkeypatch.setattr(inputs, 'verify_candidate_freeze', lambda *args: {})
    monkeypatch.setattr(models, 'load_bundle', lambda *args, **kwargs: (
        {'model_version': 'baseline', 'ordered_features': config['features'],
         'provenance': {'config': config, 'config_sha256': config_sha}}, None, object()))
    loaded = []
    monkeypatch.setattr(ltfp_model, 'load', lambda path, **kwargs: loaded.append((path.name, kwargs['metadata_sha256'])))
    class XGB:
        def load_model(self, path):
            loaded.append(('xgb', path.name))
    monkeypatch.setattr(xgboost, 'XGBClassifier', XGB)
    result = inputs.load_frozen_models(tmp_path, candidates_sha256=candidate_sha)
    assert loaded == [('graph', metadata['graph']), ('self_only', metadata['self_only']), ('xgb', 'risk.json')]
    assert result['xgboost_refit']['manifest_sha256'] == manifest_sha
    assert result['models']['ltfp_graph']['fitted_verified'] is True
    (xgb_dir / 'risk.json').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed reference'):
        inputs.load_frozen_models(tmp_path, candidates_sha256=candidate_sha)


def test_capture_index_preserves_missing_minutes_and_late_cutoffs(tmp_path):
    from sentinelops.telemetry.core import BUSINESS_ROUTES
    from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
    from sentinelops.telemetry.store import ArtifactStore

    store = ArtifactStore(tmp_path)
    refs = []
    for minute in (1, 3):
        for index, service in enumerate(BUSINESS_ROUTES):
            observed = f'2026-01-01T00:0{minute}:3{index}Z'
            if minute == 3 and index == 2:
                observed = '2026-01-01T00:04:00Z'
            refs.append(store.write('raw', {'source_profile': SOURCE_PROFILE_V2, 'service': service,
                'bucket_end': f'2026-01-01T00:0{minute}:00Z', 'collected_at': observed}))
    def run_for(raw_refs):
        ref = store.write('manifests', {'source_profile': SOURCE_PROFILE_V2, 'raw_refs': raw_refs})
        return {'status': 'completed', 'declared_split': 'test', 'source_profile': SOURCE_PROFILE_V2,
                'source_manifest_ref': ref, 'started_at': '2026-01-01T00:00:15Z',
                'ended_at': '2026-01-01T00:03:17Z'}
    result = inputs.index_recorded_captures(store, run_for(refs))
    assert [row['bucket_end'] for row in result] == [f'2026-01-01T00:0{i}:00Z' for i in (1, 2, 3)]
    assert result[0]['cutoff'] == '2026-01-01T00:01:32Z'
    assert result[1]['missing_reason'] == 'incomplete-source-bucket' and result[1]['cutoff'] is None
    assert result[2]['missing_reason'] == 'source-clock-outside-anchor-minute' and result[2]['cutoff'] is None
    assert result[2]['observed_at'] == '2026-01-01T00:04:00Z'
    duplicate = {**store.read(refs[0]), 'extra': 'different-content'}
    with pytest.raises(ValueError, match='duplicates a service bucket'):
        inputs.index_recorded_captures(store, run_for(refs + [store.write('raw', duplicate)]))
