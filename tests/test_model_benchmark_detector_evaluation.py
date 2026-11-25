from datetime import datetime, timedelta, timezone

import pytest

from sentinelops.ml.model_benchmark_detector import DetectorConfig, DetectorRow, fit_detectors
from sentinelops.ml.model_benchmark_detector_evaluation import KEY_FIELDS, MODELS, evaluate_detectors, score_detector_windows
from sentinelops.telemetry.core import timestamp


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _at(seconds):
    return timestamp(START + timedelta(seconds=seconds))


def _window(seconds, *, run='run', service='api', group='group', label=1, **extras):
    return {'dataset': 'local-development', 'group_id': group, 'run_id': run, 'service': service,
            'bucket_end': _at(seconds), 'available_at': _at(seconds), 'eligible': True,
            'y': label, 'healthy': False, **extras}


def _event(window, seconds, *, kind='latency', **extras):
    return {**{field: window[field] for field in KEY_FIELDS[:-1]},
            'event_id': f"{window['run_id']}/{window['service']}/{seconds}/{kind}",
            'kind': kind, 'eligible': True, 'onset_at': _at(seconds), 'confirmed_at': _at(seconds + 120), **extras}


def _prediction(window, model, score, **extras):
    return {**{field: window[field] for field in KEY_FIELDS}, 'model_id': model, 'score': score, **extras}


def _scores(windows, scores):
    return [_prediction(window, model, score) for window, score in zip(windows, scores, strict=True) for model in MODELS]


def _evaluate(windows, events, predictions, *, clock='replay', entry=1):
    return evaluate_detectors(windows, events, predictions, clock=clock, thresholds=dict.fromkeys(MODELS, .5),
                              policy_identity='test-policy',
                              persistence={'step_seconds': 60, 'entry_windows': entry, 'exit_windows': 1})


def _matches(result, model='median_mad'):
    return [row for row in result['matches'] if row['model_id'] == model]


def test_b04_duplicate_alert_and_5xx_only_event_do_not_inflate_latency_tp():
    windows = [_window(0, raw_ref='raw/first', oracle_ref='oracle/window'), _window(60), _window(120),
               _window(60, run='5xx-only', label=0, raw_ref='raw/5xx'),
               _window(60, run='quiet', label=0, healthy=True)]
    latency = _event(windows[0], 0, oracle_ref='oracle/event')
    error = _event(windows[3], 60, kind='5xx', oracle_ref='oracle/5xx')
    result = _evaluate(windows, [error, latency], _scores(windows, [1, 0, 1, 1, 0]))

    assert result['coverage']['oracle_events'] == result['coverage']['excluded_oracle_events'] == 1
    assert {row['kind']: row['in_scope'] for row in result['events']} == {'5xx': False, 'latency': True}
    matched, error_alert, extra_alert = _matches(result)
    assert (matched['status'], matched['event_id'], matched['emitted_at']) == ('matched', latency['event_id'], _at(0))
    assert matched['detection_delay_seconds'] == 0
    assert (matched['raw_ref'], matched['oracle_ref'], matched['event_oracle_ref']) == (
        'raw/first', 'oracle/window', 'oracle/event')
    assert (error_alert['status'], error_alert['event_id'], error_alert['raw_ref']) == ('unmatched', None, 'raw/5xx')
    assert (extra_alert['status'], extra_alert['event_id'], extra_alert['emitted_at']) == ('unmatched', None, _at(120))
    assert extra_alert['episode_id'] != matched['episode_id']
    for model in result['models'].values():
        assert (model['tp'], model['fp'], model['fn']) == (1, 2, 0)
        assert model['precision'] == 1 / 3
        assert model['false_incidents_per_healthy_service_hour'] == 120


def test_h02_coverage_preserves_unknown_and_missing_predictions_and_rejects_duplicate_keys():
    windows = [_window(0, label=None, raw_ref='raw/unknown', oracle_ref='oracle/unknown'),
               _window(60), _window(120, label=0, healthy=True),
               _window(180, eligible=False, reason='archive-warmup'), _window(240, label=0, healthy=True)]
    predictions = _scores(windows[:3], [.1, 1, .1]) + _scores(windows[4:], [.1])
    predictions.remove(_prediction(windows[2], 'isolation_forest', .1))
    predictions.remove(_prediction(windows[4], 'adsketch', .1))
    predictions.append(_prediction(windows[4], 'adsketch', None, reason='insufficient_history'))
    event = _event(windows[1], 60)
    result = _evaluate(windows, [event], predictions)

    assert len(result['predictions']) == 15
    assert result['coverage'] == {'windows': 5, 'eligible_windows': 4, 'unknown_windows': 1, 'labelled_windows': 3,
                                  'common_windows': 2, 'common_labelled_windows': 1,
                                  'oracle_events': 1, 'excluded_oracle_events': 0}
    unknown = [row for row in result['predictions'] if row['bucket_end'] == _at(0)]
    assert all((row['status'], row['y'], row['label_reason'], row['graph_neighbor_count']) ==
               ('available', None, 'oracle-unknown', None) for row in unknown)
    assert all((row['raw_ref'], row['oracle_ref']) == ('raw/unknown', 'oracle/unknown') for row in unknown)
    forest = result['models']['isolation_forest']
    assert forest['unavailable_reasons'] == {'archive-warmup': 1, 'missing-model-prediction': 1}
    assert forest['eligible_healthy_service_hours'] == 2 / 60
    assert forest['scored_healthy_service_hours'] == 1 / 60
    assert result['models']['adsketch']['unavailable_reasons'] == {'archive-warmup': 1, 'insufficient_history': 1}
    assert forest['window_ranking']['average_precision'] == 1
    assert forest['paired_window_ranking']['average_precision'] is None
    with pytest.raises(ValueError, match='duplicate detector window key'):
        _evaluate(windows + windows[:1], [event], predictions)
    with pytest.raises(ValueError, match='duplicate detector prediction'):
        _evaluate(windows, [event], predictions + predictions[:1])
    with pytest.raises(ValueError, match='disagrees with declared window: raw_ref'):
        _evaluate(windows, [event], [{**predictions[0], 'raw_ref': 'wrong'}, *predictions[1:]])


def test_h03_event_matching_and_mttd_reconcile_at_the_inclusive_detector_deadline():
    windows = [_window(0, run='first'), _window(60, run='first'), _window(120, run='first'),
               _window(180, run='first'), _window(120, run='second'), _window(300, run='deadline'),
               _window(360, run='missed'), _window(0, run='missed', service='other'),
               _window(0, run='no-alert')]
    events = [_event(windows[index], 0) for index in (0, 4, 5, 6, 8)]
    predictions = _scores(windows, [1, 1, 0, 1, 1, 1, 0, 0, 0])
    result = _evaluate(windows, events, predictions)

    model = result['models']['median_mad']
    assert (model['tp'], model['fp'], model['fn'], model['miss']) == (3, 1, 2, 2)
    assert (model['precision'], model['recall'], model['f1']) == (.75, .60, 2 / 3)
    assert model['denominators'] == {'precision': 4, 'recall': 5, 'f1': 9,
                                     'false_incidents_per_healthy_service_hour': 0,
                                     'mttd_p50_seconds': 3, 'mttd_p90_seconds': 3, 'inference_p95_seconds': 0}
    assert (model['mttd_p50_seconds'], model['mttd_p90_seconds']) == (120, 264)
    assert model['unavailable_event_misses'] == 1
    rows = _matches(result)
    assert {(row['run_id'], row['status'], row['emitted_at'], row['detection_delay_seconds'], row['reason']) for row in rows} == {
        ('first', 'matched', _at(0), 0, None), ('first', 'unmatched', _at(180), None, 'no-unmatched-eligible-event-in-window'),
        ('second', 'matched', _at(120), 120, None), ('deadline', 'matched', _at(300), 300, None),
        ('missed', 'miss', None, None, 'no-available-score-in-window'), ('no-alert', 'miss', None, None, 'no-matching-alert')}
    expected_fields = (*KEY_FIELDS[:-1], 'model_id', 'clock', 'event_id', 'event_pattern_kind', 'onset_at', 'deadline_at',
                       'event_oracle_ref', 'bucket_end', 'raw_ref', 'oracle_ref', 'episode_id', 'emitted_at',
                       'detection_delay_seconds', 'pattern_kind', 'pattern_id', 'status', 'reason')
    assert all(tuple(row) == expected_fields for row in result['matches'])
    first_episode = [row for row in result['predictions'] if row['run_id'] == 'first' and row['model_id'] == 'median_mad']
    assert first_episode[1]['episode_id'] == first_episode[0]['episode_id']
    assert first_episode[1]['emitted_at'] is None
    for duplicate in (events[0], {**events[0], 'event_id': 'duplicate-with-new-id'}):
        with pytest.raises(ValueError, match='duplicate detector oracle event'):
            _evaluate(windows, [*events, duplicate], predictions)


def test_h03_arrival_emission_before_onset_and_after_deadline_cannot_match():
    windows = [_window(0, decision_at=_at(59)), _window(60, decision_at=_at(60)),
               _window(360, decision_at=_at(361)), _window(120, service='other', decision_at=_at(120)),
               _window(180, run='other', decision_at=_at(180))]
    event = _event(windows[1], 60)
    result = _evaluate(windows, [event], _scores(windows, [1, 0, 1, 1, 1]), clock='arrival-aware')
    assert [(row['status'], row['emitted_at'], row['reason']) for row in _matches(result)] == [
        ('unmatched', _at(59), 'no-unmatched-eligible-event-in-window'),
        ('unmatched', _at(120), 'no-unmatched-eligible-event-in-window'),
        ('unmatched', _at(180), 'no-unmatched-eligible-event-in-window'),
        ('unmatched', _at(361), 'no-unmatched-eligible-event-in-window'),
        ('miss', None, 'no-matching-alert')]
    assert result['models']['median_mad']['mttd_p90_seconds'] is None


def test_h04_event_on_unscored_service_remains_in_full_recall():
    windows = [_window(0, label=0), _window(60), _window(60, service='consumer')]
    events = [_event(windows[1], 60), _event(windows[2], 60, oracle_ref='oracle/consumer')]
    predictions = _scores(windows[:2], [0, 1])
    predictions.extend(_prediction(windows[2], model, 1) for model in MODELS if model != 'adsketch')
    result = _evaluate(windows, events, predictions)

    forest, sketch = (result['models'][model] for model in ('isolation_forest', 'adsketch'))
    assert forest['recall'] == 1 and sketch['recall'] == .5
    assert forest['denominators']['recall'] == sketch['denominators']['recall'] == 2
    assert result['coverage']['common_labelled_windows'] == 2
    assert forest['window_ranking']['windows'] == 3 and sketch['window_ranking']['windows'] == 2
    assert forest['paired_window_ranking'] == sketch['paired_window_ranking']
    assert sketch['paired_window_ranking']['average_precision'] == 1
    consumer = sketch['services']['consumer']
    assert consumer['available_windows'] == 0 and consumer['fn'] == consumer['denominators']['recall'] == 1
    assert sketch['unavailable_event_misses'] == 1
    miss, = [row for row in _matches(result, 'adsketch') if row['status'] == 'miss']
    assert (miss['reason'], miss['event_oracle_ref'], miss['raw_ref'], miss['episode_id']) == (
        'no-available-score-in-window', 'oracle/consumer', None, None)


def test_h05_single_class_and_zero_exposure_have_explicit_undefined_metrics():
    windows = [_window(0, run='negatives', group='negatives', label=0, healthy=True),
               _window(0, run='positives', group='positives')]
    result = _evaluate(windows, [], _scores(windows, [.1, .2]))
    model = result['models']['median_mad']
    assert model['window_ranking']['average_precision'] == 1
    assert model['false_incidents_per_healthy_service_hour'] == 0
    assert model['mttd_p50_seconds'] is model['mttd_p90_seconds'] is model['inference_p95_seconds'] is None
    assert model['missing_reasons']['mttd'] == 'no-matched-events'
    assert model['missing_reasons']['inference_p95_seconds'] == 'no-timed-predictions'
    for group in model['groups'].values():
        for field in ('precision', 'recall', 'f1'):
            assert group['denominators'][field] == 0 and group[field] is None and group['missing_reasons'][field]
        assert group['window_ranking']['average_precision'] is None
        assert group['window_ranking']['missing_reason'] == 'both-target-classes-required'
    positives = model['groups']['positives']
    field = 'false_incidents_per_healthy_service_hour'
    assert positives['denominators'][field] == 0 and positives[field] is None
    assert positives['missing_reasons'][field] == 'no-eligible-healthy-exposure'
    assert result['bootstrap']['attempted_replicates'] == 0


def test_h06_missing_group_is_retained_in_deterministic_paired_bootstrap():
    windows, events, predictions = [], [], []
    for index in range(1, 6):
        group = f'group-{index}'
        pair = [_window(0, group=group, run=group, label=0), _window(60, group=group, run=group)]
        windows.extend(pair)
        events.append(_event(pair[1], 60))
        predictions.extend(row for row in _scores(pair, [0, index % 2])
                           if index != 5 or row['model_id'] != 'adsketch')
    result = _evaluate(windows, events, predictions)
    assert _evaluate(reversed(windows), reversed(events), reversed(predictions)) == result
    models = result['models']
    missing = models['adsketch']['groups']['group-5']
    assert (missing['windows'], missing['available_windows'], missing['eligible_events'], missing['fn']) == (2, 0, 1, 1)
    assert models['adsketch']['unavailable_reasons'] == {'missing-model-prediction': 2}
    assert models['adsketch']['recall'] == .4 and models['median_mad']['recall'] == .6
    assert result['coverage']['common_windows'] == 8
    bootstrap = result['bootstrap']
    assert bootstrap['group_ids'] == list(models['adsketch']['groups'])
    assert (bootstrap['groups'], bootstrap['seed'], bootstrap['attempted_replicates']) == (5, 42, 1000)
    equal = bootstrap['intervals']['isolation_forest_minus_median_mad']['recall']
    assert (equal['lower'], equal['upper'], equal['valid_replicates']) == (0, 0, 1000)
    recall = bootstrap['intervals']['adsketch_minus_median_mad']['recall']
    assert recall['denominators'] == {'adsketch': 5, 'median_mad': 5}
    assert (recall['lower'], recall['upper']) == pytest.approx((-.6, 0))
    assert recall['valid_replicates'] == 1000 and recall['undefined_replicates'] == 0
    precision = bootstrap['intervals']['adsketch_minus_median_mad']['precision']
    assert 0 < precision['undefined_replicates'] < 1000
    assert precision['valid_replicates'] + precision['undefined_replicates'] == 1000


def test_frozen_scoring_preserves_sources_and_censors_late_history():
    config = DetectorConfig(60, 3, normal_patterns=2, anomalous_patterns=2, max_fit_windows=24,
                            forest_trees=3, forest_sample_size=8, discovery_quantile=.8)
    reference = [DetectorRow('train-ref', 'ref', 'train', index * 60, float(index % 4), 0) for index in range(20)]
    discovery = [DetectorRow('train-disc', 'disc', 'train', index * 60, float(value), int(value > 5))
                 for index, value in enumerate([0, 1, 2, 3] * 5 + [10, 16, 20, 14, 12, 9])]
    frozen = fit_detectors(reference, config, discovery_rows=discovery)
    windows = [_window(index * 60, decision_at=_at(index * 60), value=10 + index, raw_ref=f'raw/{index}',
                       oracle_ref='oracle/run') for index in range(12)]
    windows[8]['available_at'] = _at(10 * 60 + 1)
    windows[11]['available_at'] = None
    replay = score_detector_windows(frozen, windows, clock='replay')
    arrival = score_detector_windows(frozen, windows, clock='arrival-aware')

    assert len(replay) == len(arrival) == 12 * len(MODELS)
    assert all(row['score'] is not None for row in replay if row['bucket_end'] == _at(10 * 60))
    assert all(row['score'] is None for row in arrival if row['bucket_end'] == _at(10 * 60))
    assert {row['reason'] for row in arrival if row['bucket_end'] == _at(8 * 60)} == {'bucket-arrived-after-decision'}
    assert {row['reason'] for row in arrival if row['bucket_end'] == _at(11 * 60)} == {'bucket-availability-unrecorded'}
    expected_refs = {window['bucket_end']: (window['raw_ref'], window['oracle_ref']) for window in windows}
    assert all((row['raw_ref'], row['oracle_ref']) == expected_refs[row['bucket_end']] for row in arrival)
    assert _evaluate(windows, [], arrival, clock='arrival-aware')['coverage']['windows'] == 12
