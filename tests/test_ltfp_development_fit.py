"""Development sample selection must be deterministic and remain train-only."""
import json
import csv
import io

import pytest

torch = pytest.importorskip('torch')
pd = pytest.importorskip('pandas')
np = pytest.importorskip('numpy')
pytest.importorskip('xgboost')

from scripts.fit_ltfp_development import MAX_SAMPLES, select_records
from scripts import fit_xgboost_development
from scripts.fit_xgboost_development import train_features, verify_existing
from scripts import calibrate_ltfp_development
from scripts.calibrate_ltfp_development import operating_points, score_graph, select_anchors
from scripts.score_ltfp_development import project_cached, summarize
from scripts.verify_ltfp_development_score import ledger_rows
from scripts.report_ltfp_development import forecast_csv, write_once
from sentinelops.ml.alibaba_training import load_config, split_of
from sentinelops.ml.ltfp_model import predict
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id
from test_ltfp_model import fixture as model_fixture


def test_positive_retention_and_seeded_negative_selection_are_order_independent():
    positives = [{'record_id': f'p-{index}', 'label': 1} for index in range(7)]
    negatives = [{'record_id': f'n-{index}', 'label': 0} for index in range(4000)]
    candidates = positives + negatives
    selected = select_records(candidates)
    assert selected == select_records(list(reversed(candidates)))
    assert len(selected) == MAX_SAMPLES
    assert {row['record_id'] for row in positives}.issubset({row['record_id'] for row in selected})
    assert sum(row['label'] == 0 for row in selected) == MAX_SAMPLES - len(positives)
    with pytest.raises(ValueError, match='duplicate'):
        select_records(candidates + positives[:1])
    with pytest.raises(ValueError, match='both classes'):
        select_records(negatives)


def test_baseline_features_match_causal_projection_and_reject_changed_labels():
    service = next(f'MS_{index}' for index in range(100) if split_of(f'MS_{index}') == 'train')
    data = pd.DataFrame([{'msname': service, 'minute': minute, 'http_rt_ms': 10.0,
                          'http_mcr_sum': 1.0} for minute in range(180)])
    record = {'record_id': f'{service}:162:K16', 'service': service, 'minute': 162, 'label': 0}
    values = train_features(data, [record], load_config(), (0, 179), (162, 172), 16.0)
    assert values.shape == (1, 6)
    assert values[0, :5] == pytest.approx([0.0] * 5)
    assert values[0, 5] == pytest.approx(-2.772588722239781)
    with pytest.raises(ValueError, match='differs from the causal train cohort'):
        train_features(data, [{**record, 'label': 1}], load_config(), (0, 179), (162, 172), 16.0)


def test_calibration_anchor_selection_is_one_per_service_and_label_independent():
    rows = [{'record_id': f'{service}:{minute}:K16', 'service': service, 'minute': minute,
             'risk_label': minute % 2} for service in ('MS_1', 'MS_2') for minute in range(324, 329)]
    selected = select_anchors(rows)
    relabelled = [{**row, 'risk_label': None} for row in reversed(rows)]
    assert len(selected) == 2
    assert [row['record_id'] for row in selected] == [row['record_id'] for row in select_anchors(relabelled)]
    with pytest.raises(ValueError, match='duplicate'):
        select_anchors(rows + rows[:1])


def test_calibration_keeps_baseline_score_when_ltfp_sequence_is_missing(monkeypatch):
    graph = {'node_ids': ['MS_1'], 'target_available': [False], 'attention_mask': [[0]],
             'validity': [[False, True, True, True, True]], 'values': [[[0.0, .2] * 3] * 5],
             'graph_sha256': 'graph'}
    monkeypatch.setattr(calibrate_ltfp_development, 'predict', lambda *_args, **_kwargs: {'MS_1': None})
    scores, vector, neighbors = score_graph(graph, 'MS_1', {'graph': None, 'self_only': None},
        {'graph': {'identity': {}}, 'self_only': {'identity': {}}})
    assert scores == {'ltfp_graph': None, 'ltfp_self_only': None, 'slope': .2}
    assert vector == [0.0, .2] * 3
    assert neighbors is None
    assert 'self_only' not in graph


def test_operating_points_keep_missing_ltfp_in_the_calibration_denominator():
    predictions = []
    for index in range(101):
        scores = {'slope': .01, 'xgboost': .1, 'ltfp_self_only': .1, 'ltfp_graph': .1}
        reasons = dict.fromkeys(scores)
        if index == 0:
            scores['ltfp_graph'] = None
            reasons['ltfp_graph'] = 'incomplete-feature-sequence'
        predictions.append({'record_id': str(index), 'risk_label': 0,
                            'scores': scores, 'missing_reasons': reasons})
    points = operating_points(predictions, 'fixture')
    assert points['ltfp_graph']['negative_windows'] == 100
    assert points['ltfp_graph']['eligible_negative_windows'] == 101
    assert points['ltfp_graph']['missing_scores'] == 1
    assert points['xgboost']['negative_windows'] == 101


def test_cached_one_hop_projection_matches_existing_model_inference():
    model, identity, graph = model_fixture()
    features = {node: dict(zip(graph['step_minutes'], values))
                for node, values in zip(graph['node_ids'], graph['values'])}
    minute = graph['step_minutes'][-1]
    values, mask, _, index, available, _, count = project_cached(
        'a', minute, features, {'a': {'b'}, 'b': {'a'}}, {('a', 'b'), ('b', 'a')})
    with torch.no_grad():
        score = float(torch.sigmoid(model(torch.tensor(values), torch.tensor(mask)))[index])
    expected = predict(model, identity, graph, expected_graph_sha256=graph['graph_sha256'])['a']
    assert available and count == 1
    assert score == pytest.approx(expected, abs=1e-6)
    del features['a'][minute - 2]
    _, _, _, _, available, last, count = project_cached(
        'a', minute, features, {'a': {'b'}, 'b': {'a'}}, {('a', 'b'), ('b', 'a')})
    assert not available and last is not None and count is None


def test_development_summary_reports_common_cohort_and_unavailable_scores():
    thresholds = {model: {'threshold': .5} for model in ('slope', 'xgboost', 'ltfp_self_only', 'ltfp_graph')}
    rows = [
        {'risk_label': 1, 'scores': {model: .9 for model in thresholds}},
        {'risk_label': 0, 'scores': {model: .5 for model in thresholds}},
        {'risk_label': 0, 'scores': {**dict.fromkeys(thresholds, .1), 'ltfp_graph': None}},
        {'risk_label': None, 'scores': dict.fromkeys(thresholds, .1)},
    ]
    result = summarize(rows, thresholds)
    assert (result['windows'], result['labelled_windows'], result['common_windows']) == (4, 3, 2)
    assert result['models']['ltfp_graph']['labelled_unavailable'] == 1
    assert result['models']['xgboost']['negative_flags'] == 1
    assert result['models']['slope']['negative_flags'] == 0


def test_score_ledger_rejects_missing_models_and_inconsistent_labels():
    service = next(f'MS_{index}' for index in range(100) if split_of(f'MS_{index}') == 'calibration')
    rows = [{'dataset': 'alibaba-proxy-development', 'group_id': service, 'run_id': service,
             'service': service, 'relative_minute': 324, 'model_id': model, 'risk_label': 0,
             'score': .2, 'score_missing_reason': None, 'graph_neighbor_count': 1,
             'clock': 'relative-minute-replay'}
            for model in ('slope', 'xgboost', 'ltfp_self_only', 'ltfp_graph')]
    assert ledger_rows(pd.DataFrame(rows))[0]['risk_label'] == 0
    with pytest.raises(ValueError, match='model cohort'):
        ledger_rows(pd.DataFrame(rows[:-1]))
    rows[-1]['risk_label'] = 1
    with pytest.raises(ValueError, match='labels differ'):
        ledger_rows(pd.DataFrame(rows))


def test_xgboost_retry_verifies_original_predictions_and_rejects_changed_bytes(tmp_path, monkeypatch):
    records = [{'record_id': 'r0', 'label': 0}, {'record_id': 'r1', 'label': 1}]
    values = np.asarray([[1.0], [2.0]])
    config = {'features': ['feature'], 'risk': {'estimator': {'n_estimators': 1}}}
    protocol = {'selection_sha256': 'selection', 'fit_report_sha256': 'data', 'config_sha256': 'config'}
    receipt = {'input_sha256': 'fit'}
    (tmp_path / 'risk.json').write_text('model')
    (tmp_path / 'predictions.json').write_text(json.dumps([
        {'record_id': 'r0', 'label': 0, 'score': .2}, {'record_id': 'r1', 'label': 1, 'score': .8}]))
    manifest = {'scope': 'development-train-only', 'activation_allowed': False,
        'selection_sha256': 'selection', 'paired_fit_input_sha256': 'fit',
        'input_features_sha256': stable_id(values.tolist()), 'fit_report_sha256': 'data',
        'config_sha256': 'config', 'rows': 2, 'positive_rows': 1, 'seed': 42,
        'estimator': config['risk']['estimator'], 'model_sha256': file_sha256(tmp_path / 'risk.json'),
        'predictions_sha256': file_sha256(tmp_path / 'predictions.json')}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))

    class FakeModel:
        def load_model(self, _path):
            pass

    class FakeRisk:
        def __init__(self, _model, _features):
            pass

        def predict_proba(self, _values):
            return np.asarray([[.8, .2], [.2, .8]])

    monkeypatch.setattr(fit_xgboost_development, 'XGBClassifier', FakeModel)
    monkeypatch.setattr(fit_xgboost_development, 'ExceedanceRisk', FakeRisk)
    assert verify_existing(tmp_path, protocol, receipt, records, values, config) == manifest
    (tmp_path / 'risk.json').write_text('changed')
    with pytest.raises(ValueError, match='baseline identity differs'):
        verify_existing(tmp_path, protocol, receipt, records, values, config)


def test_forecast_report_keeps_event_metrics_empty_and_refuses_changed_output(tmp_path):
    thresholds = {model: {'threshold': .5} for model in ('slope', 'xgboost', 'ltfp_self_only', 'ltfp_graph')}
    rows = [{'risk_label': 1, 'scores': dict.fromkeys(thresholds, .9)},
            {'risk_label': 0, 'scores': dict.fromkeys(thresholds, .1)}]
    output = forecast_csv(summarize(rows, thresholds))
    table = list(csv.DictReader(io.StringIO(output.decode())))
    assert len(table) == 4 and all(row['common_windows'] == '2' for row in table)
    assert all(row['eligible_events'] == '' and row['false_warnings_per_healthy_service_hour'] == '' for row in table)
    target = tmp_path / 'forecast.csv'
    write_once(target, output)
    write_once(target, output)
    with pytest.raises(ValueError, match='existing report differs'):
        write_once(target, b'different')
    assert target.read_bytes() == output
