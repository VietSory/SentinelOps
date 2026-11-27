"""Shared outcomes never depend on model availability or intervention labels."""
from copy import deepcopy

import pytest
pytest.importorskip("sklearn")

from sentinelops.ml.model_benchmark_forecast import MODELS, forecast_rows
from sentinelops.telemetry.store import ArtifactStore
from test_latency_forecast import inputs, set_latency, POLICY, SERVICE


def fixture(tmp_path):
    frame, oracle = inputs()
    store = ArtifactStore(tmp_path)
    row = {**frame, 'prediction_cutoff': frame['as_of'], 'run_id': 'abrupt', 'group_id': 'g',
           'feature_ref': store.write('features', frame)}
    scores = {model: {'score': .8, 'missing_reason': None} for model in MODELS}
    return store, row, oracle, scores


@pytest.mark.parametrize('mode,label', [('abrupt', 1), ('errors', 0), ('gap', None), ('confirmation', None), ('cutoff', None)])
def test_shared_outcomes_and_no_mutation(tmp_path, mode, label):
    store, row, oracle, scores = fixture(tmp_path)
    if mode != 'errors':
        set_latency(oracle, 240, [800., 900.])
    if mode in ('gap', 'confirmation'):
        keys = sorted(oracle['services'][SERVICE]['buckets'], key=int)
        del oracle['services'][SERVICE]['buckets'][keys[7 if mode == 'gap' else 10]]
    if mode == 'errors':
        for bucket in oracle['services'][SERVICE]['buckets'].values():
            bucket.update(error_rate=.9, breach=True)
    if mode == 'cutoff':
        frame = store.read(row['feature_ref'])
        frame['as_of'] = frame['as_of'].replace('05:30', '06:00')
        row.update(feature_ref=store.write('features', frame), prediction_cutoff=frame['as_of'])
    scores['slope']['score'] = 2.5
    scores['ltfp_graph'] = {'score': None, 'missing_reason': 'graph-unavailable'}
    before = deepcopy((row, oracle, scores))
    result = forecast_rows(store, row, oracle, POLICY, scores)
    assert [r['model'] for r in result] == list(MODELS)
    assert all(r['risk_label'] == label for r in result)
    assert all(r['feature_ref'] == row['feature_ref'] for r in result)
    assert result[-1]['score'] is None and result[-1]['score_missing_reason'] == 'graph-unavailable'
    assert (row, oracle, scores) == before


@pytest.mark.parametrize('bad', ['cutoff', 'service', 'feature_ref', 'score', 'reason', 'models', 'policy'])
def test_invalid_boundary_rejected(tmp_path, bad):
    store, row, oracle, scores = fixture(tmp_path)
    policy = deepcopy(POLICY)
    if bad == 'cutoff': row['prediction_cutoff'] = row['window_end']
    if bad == 'service': row['service'] = 'foreign'
    if bad == 'feature_ref': row['feature_ref'] = row['feature_ref'].replace('/features/', '/raw/')
    if bad == 'score': scores['slope']['score'] = float('nan')
    if bad == 'reason': scores['slope']['score'] = None
    if bad == 'models': del scores['slope']
    if bad == 'policy': policy['entry_buckets'] = 3
    with pytest.raises(ValueError): forecast_rows(store, row, oracle, policy, scores)


def timeline(tmp_path, states):
    from datetime import timedelta
    from sentinelops.telemetry.core import timestamp
    from test_local_labels import START
    store, row, oracle, scores = fixture(tmp_path)
    base = forecast_rows(store, row, oracle, POLICY, scores)
    rows = []
    for minute, score, label in states:
        for original in base:
            item = {**original, 'window_end': timestamp(START + timedelta(minutes=minute)),
                    'prediction_cutoff': timestamp(START + timedelta(minutes=minute, seconds=30)),
                    'risk_label': label, 'risk_missing_reason': 'unknown' if label is None else None,
                    'score': score, 'score_missing_reason': 'missing' if score is None else None}
            rows.append(item)
    return rows


def test_common_cohort_unknown_and_missing_thresholds(tmp_path):
    from sentinelops.ml.model_benchmark_forecast import summarize_forecasts
    rows = timeline(tmp_path, [(5, .8, 1), (6, .1, 0), (7, .4, None), (8, .2, 0)])
    rows[-1].update(score=None, score_missing_reason='graph-missing')
    before = deepcopy(rows)
    result = summarize_forecasts(rows, [])
    assert (result['windows'], result['unknown_windows'], result['labelled_windows'], result['common_windows']) == (4, 1, 3, 2)
    for model in MODELS:
        assert result['models'][model]['average_precision'] == 1
        assert result['models'][model]['prevalence'] == .5
        assert result['models'][model]['episodes']['recall'] is None
    assert result['models']['ltfp_graph']['labelled_unavailable_windows'] == 1
    assert summarize_forecasts(list(reversed(rows)), []) == result
    assert rows == before


def test_missing_source_clock_remains_unavailable_without_fabricating_time(tmp_path):
    from sentinelops.ml.model_benchmark_forecast import summarize_forecasts

    rows = timeline(tmp_path, [(5, None, None)])
    for row in rows:
        row['prediction_cutoff'] = None
        row['score_missing_reason'] = 'incomplete-source-bucket'
    result = summarize_forecasts(rows, [])
    assert result['windows'] == result['unknown_windows'] == 1
    assert all(result['models'][name]['unavailable_windows'] == 1 for name in MODELS)
    rows[0]['score'] = .5
    with pytest.raises(ValueError, match='invalid cutoff'):
        summarize_forecasts(rows, [])


def test_frozen_slope_strict_comparison_preserves_boundary(tmp_path):
    from sentinelops.ml.model_benchmark_forecast import summarize_forecasts

    rows = timeline(tmp_path, [(5, .05, 0), (6, .05, 0)])
    thresholds = {model: {'watch': .05, 'degrading': .05} for model in MODELS}
    thresholds['slope']['comparison'] = '>'
    result = summarize_forecasts(rows, [], thresholds=thresholds, policy_identity='frozen')
    assert result['models']['slope']['episodes']['alerts'] == 0
    assert result['models']['xgboost']['episodes']['alerts'] == 1
    thresholds['slope']['comparison'] = '='
    with pytest.raises(ValueError, match='invalid watch/degrading thresholds'):
        summarize_forecasts(rows, [], thresholds=thresholds, policy_identity='frozen')


def test_forecast_rules_are_derived_from_verified_freezes(tmp_path, monkeypatch):
    import hashlib
    import json
    from sentinelops.ml import model_benchmark_holdout
    from sentinelops.ml.model_benchmark_forecast import thresholds_from_freeze
    from sentinelops.telemetry.core import stable_id

    protocol = {'scoring': {'fixed_slope': {'threshold': .05, 'comparison': '>'}}}
    points = {model: {'status': 'ready', 'budget': .01, 'comparison': '>=', 'threshold': threshold,
                      'eligible_negative_windows': 100, 'negative_windows': 100, 'negative_flags': 1,
                      'calibration_identity': 'shared-cohort'}
              for model, threshold in (('xgboost', .8), ('ltfp_self_only', .2), ('ltfp_graph', .3))}
    calibration = tmp_path / 'preflight/development-k16-calibration-v5/manifest.json'
    calibration.parent.mkdir(parents=True)
    calibration.write_text(json.dumps({'points': points}))
    candidates = {'lanes': {'a': {'slope_comparison': '>', 'calibration_sha256': hashlib.sha256(calibration.read_bytes()).hexdigest(),
        'thresholds': {'slope': .05, 'xgboost': .8, 'ltfp_self_only': .2, 'ltfp_graph': .3}}}}
    directory = tmp_path / 'freeze'
    directory.mkdir()
    (directory / 'protocol.json').write_text(json.dumps({'protocol': protocol}))
    path = directory / 'candidates.json'
    path.write_text(json.dumps(candidates))
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(model_benchmark_holdout, 'verify_candidate_freeze',
                        lambda *args: {'protocol_sha256': stable_id(protocol)})
    result = thresholds_from_freeze(tmp_path, pin)
    assert result['policy_identity'] == pin
    assert result['thresholds']['slope'] == {'watch': .05, 'degrading': .05, 'comparison': '>'}
    assert result['thresholds']['xgboost']['comparison'] == '>='
    assert result['calibration_budget'] == .01 and result['calibration_cohort'] == 'shared-cohort'
    points['ltfp_graph']['negative_flags'] = 2
    calibration.write_text(json.dumps({'points': points}))
    with pytest.raises(ValueError, match='changed reference'):
        thresholds_from_freeze(tmp_path, pin)
    candidates['lanes']['a']['calibration_sha256'] = hashlib.sha256(calibration.read_bytes()).hexdigest()
    path.write_text(json.dumps(candidates))
    changed_pin = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='common false-positive budget'):
        thresholds_from_freeze(tmp_path, changed_pin)
    points['ltfp_graph']['negative_flags'] = 1
    calibration.write_text(json.dumps({'points': points}))
    candidates['lanes']['a']['calibration_sha256'] = hashlib.sha256(calibration.read_bytes()).hexdigest()
    path.write_text(json.dumps(candidates))
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    protocol['scoring']['fixed_slope']['comparison'] = '>='
    (directory / 'protocol.json').write_text(json.dumps({'protocol': protocol}))
    with pytest.raises(ValueError, match='differ from the protocol'):
        thresholds_from_freeze(tmp_path, pin)


def test_episode_tracker_parity_matching_and_unavailable_denominator(tmp_path):
    from datetime import timedelta
    from sentinelops.telemetry.core import timestamp
    from test_local_labels import START
    from sentinelops.ml.model_benchmark_forecast import summarize_forecasts
    from sentinelops.workers.risk import EpisodeTracker
    sequence = [(5, .5, 1), (6, .8, 1), (7, .1, 0), (8, .1, 0),
                (10, .8, 1), (12, .8, 1), (13, .8, 1),
                *[(minute, None, None) for minute in range(14, 19)], (19, .5, 0), (20, .8, 0)]
    rows = timeline(tmp_path, sequence)
    thresholds = {model: {'watch': .4, 'degrading': .7} for model in MODELS}
    tracker = EpisodeTracker()
    starts = []
    for minute, score, _ in sequence:
        state = 'unavailable' if score is None else 'degrading' if score >= .7 else 'watch' if score >= .4 else 'normal'
        if tracker.update('payment-service', START + timedelta(minutes=minute), state)['event'] == 'started':
            starts.append(minute)
    assert starts == [6, 13, 20]
    events = [{'event_id': 'first', 'run_id': 'abrupt', 'service': SERVICE,
               'onset_at': timestamp(START + timedelta(minutes=7, seconds=30))},
              {'event_id': 'second', 'run_id': 'abrupt', 'service': SERVICE,
               'onset_at': timestamp(START + timedelta(minutes=7, seconds=31))},
              {'event_id': 'late', 'run_id': 'abrupt', 'service': SERVICE,
               'onset_at': timestamp(START + timedelta(minutes=14, seconds=29))}]
    before = deepcopy((rows, events, thresholds))
    result = summarize_forecasts(rows, events, thresholds=thresholds, policy_identity='fixture-only')
    episodes = result['models']['slope']['episodes']
    assert episodes['alerts'] == len(starts)
    assert episodes['unmatched_alerts'] == 1
    assert [match['status'] for match in episodes['matches']] == ['matched', 'miss', 'late']
    assert [match['lead_seconds'] for match in episodes['matches']] == [60, None, 59]
    assert episodes['recall'] == 2 / 3
    assert episodes['lead_qualified_recall'] == 1 / 3
    assert (rows, events, thresholds) == before
    with pytest.raises(ValueError, match='duplicate oracle event'):
        summarize_forecasts(rows, events + events[:1], thresholds=thresholds, policy_identity='fixture-only')
    for row in rows:
        if row['model'] == 'ltfp_graph': row.update(score=None, score_missing_reason='graph-missing')
    missing = summarize_forecasts(rows, events, thresholds=thresholds, policy_identity='fixture-only')
    assert missing['models']['ltfp_graph']['episodes']['events'] == 3
    assert missing['models']['ltfp_graph']['episodes']['recall'] == 0
    assert (events, thresholds) == before[1:]


@pytest.mark.parametrize('offset,matched', [(0, False), (300, True), (301, False)])
def test_episode_onset_bounds(tmp_path, offset, matched):
    from datetime import timedelta
    from sentinelops.telemetry.core import timestamp
    from test_local_labels import START
    from sentinelops.ml.model_benchmark_forecast import summarize_forecasts
    rows = timeline(tmp_path, [(5, .5, 0), (6, .8, 1)])
    events = [{'event_id': 'event', 'run_id': 'abrupt', 'service': SERVICE,
               'onset_at': timestamp(START + timedelta(minutes=6, seconds=30 + offset))}]
    result = summarize_forecasts(rows, events, thresholds={model: {'watch': .4, 'degrading': .7} for model in MODELS}, policy_identity='fixture')
    assert result['models']['slope']['episodes']['recall'] == int(matched)


def test_summary_rejects_duplicates_and_reports_single_class_na(tmp_path):
    from sentinelops.ml.model_benchmark_forecast import summarize_forecasts
    rows = timeline(tmp_path, [(5, .8, 1)])
    assert summarize_forecasts(rows, [])['models']['slope']['average_precision'] is None
    with pytest.raises(ValueError, match='duplicate'): summarize_forecasts(rows + rows[:1], [])
    shifted = {**rows[0], 'prediction_cutoff': rows[0]['prediction_cutoff'].replace(':30Z', ':31Z')}
    with pytest.raises(ValueError, match='duplicate'): summarize_forecasts(rows + [shifted], [])
    with pytest.raises(ValueError, match='missing model'): summarize_forecasts(rows[:-1], [])
    altered = deepcopy(rows)
    altered[-1]['risk_label'] = 0
    with pytest.raises(ValueError, match='outcomes'): summarize_forecasts(altered, [])
