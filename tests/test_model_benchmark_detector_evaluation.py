from datetime import datetime, timedelta, timezone
import math
from sys import float_info

import pytest

from sentinelops.ml.model_benchmark_detector import DetectorConfig, DetectorRow, fit_detectors
from sentinelops.ml.model_benchmark_detector_combo import COMBO_MODEL, append_combo_predictions
from sentinelops.ml.model_benchmark_detector_evaluation import (
    KEY_FIELDS, MODELS, PAIRS, evaluate_detectors, score_detector_windows,
)
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
    return [_prediction(window, model, score) for window, score in zip(windows, scores, strict=True)
            for model in MODELS]


def _evaluate(windows, events, predictions, *, clock='replay', entry=1, combo_threshold=None,
              mad_threshold=.5, ads_threshold=.5, event_kind='latency'):
    thresholds = dict.fromkeys(MODELS, .5)
    if combo_threshold is not None:
        thresholds[COMBO_MODEL] = combo_threshold
    return evaluate_detectors(windows, events, predictions, clock=clock,
                              thresholds=thresholds, event_kind=event_kind,
                              component_thresholds={'median_mad': mad_threshold, 'adsketch': ads_threshold},
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

    assert len(result['predictions']) == len(windows) * len(MODELS)
    assert {key: value for key, value in result['coverage'].items() if key != 'pairs'} == {
                                  'windows': 5, 'eligible_windows': 4, 'unknown_windows': 1, 'labelled_windows': 3,
                                  'common_windows': 2, 'common_labelled_windows': 1,
                                  'oracle_events': 1, 'excluded_oracle_events': 0}
    unknown = [row for row in result['predictions'] if row['bucket_end'] == _at(0)]
    assert all((row['status'], row['y'], row['label_reason'], row['graph_neighbor_count']) ==
               ('available', None, 'oracle-unknown', None) for row in unknown)
    assert all((row['raw_ref'], row['oracle_ref']) == ('raw/unknown', 'oracle/unknown') for row in unknown)
    forest = result['models']['isolation_forest']
    assert forest['unavailable_reasons'] == {'missing-model-prediction': 1}
    assert forest['eligible_healthy_service_hours'] == 2 / 60
    assert forest['scored_healthy_service_hours'] == 1 / 60
    assert result['models']['adsketch']['unavailable_reasons'] == {'insufficient_history': 1}
    for model in result['models'].values():
        assert model['windows'] == model['available_windows'] + model['unavailable_windows'] == 4
        assert sum(model['unavailable_reasons'].values()) == model['unavailable_windows']
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
    assert bootstrap['event_groups'] == 1
    equal = bootstrap['intervals']['median_mad_minus_isolation_forest']['recall']
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


def test_combo_is_derived_before_persistence_and_reports_added_matches_and_false_incidents():
    windows = [_window(0), _window(60), _window(120), _window(180, run='false', label=0, healthy=True),
               _window(240, run='false', label=0, healthy=True), _window(300, run='miss')]
    events = [_event(windows[0], 0), _event(windows[-1], 300)]
    predictions = [_prediction(windows[0], 'adsketch', 4), _prediction(windows[1], 'median_mad', .8),
                   _prediction(windows[2], 'median_mad', .6), _prediction(windows[3], 'adsketch', 5),
                   _prediction(windows[4], 'adsketch', 5)]
    result = _evaluate(windows, events, predictions, entry=2, combo_threshold=1.0, mad_threshold=.8, ads_threshold=4)
    assert set(result['models']) == {*MODELS, COMBO_MODEL}
    increment = result['adsketch_increment']
    assert increment['combo_only_matched_events'] == increment['mad_only_matched_events'] == []
    assert (increment['delta_tp'], increment['delta_fp']) == (0, 1)
    assert increment['attributable_matched_events'] == [events[0]['event_id']]
    assert increment['attributable_false_incidents'] == 1
    assert increment['mad_at_combo_threshold']['tp'] == 0
    combo, mad = (_matches(result, model) for model in (COMBO_MODEL, 'median_mad'))
    assert [(row['status'], row['emitted_at']) for row in combo] == [
        ('matched', _at(60)), ('unmatched', _at(240)), ('miss', None)]
    assert mad[0]['emitted_at'] == _at(120)
    assert combo[-1]['reason'] == 'no-available-score-in-window'
    combo_rows = {row['bucket_end']: row for row in result['predictions'] if row['model_id'] == COMBO_MODEL}
    assert combo_rows[_at(0)]['component_availability'] == 'adsketch_only'
    assert combo_rows[_at(60)]['component_availability'] == 'median_mad_only'
    assert combo_rows[_at(300)]['component_availability'] == 'none'
    assert combo_rows[_at(0)]['score'] == 1.0 and combo_rows[_at(120)]['score'] == pytest.approx(.75)
    assert combo_rows[_at(300)]['status'] == 'unavailable'
    result = _evaluate(windows, events, predictions[:2] + predictions[3:], entry=2, combo_threshold=1.0,
                       mad_threshold=.8, ads_threshold=4)
    increment = result['adsketch_increment']
    assert increment['combo_only_matched_events'] == [events[0]['event_id']]
    assert (increment['delta_tp'], increment['delta_fp']) == (1, 1)


def test_combo_threshold_and_audit_of_precomputed_predictions():
    window = _window(0)
    with pytest.raises(ValueError, match='combo fixed at 1.0'):
        _evaluate([window], [], [], combo_threshold=.5)
    with pytest.raises(ValueError, match='undeclared window/model'):
        _evaluate([window], [], [_prediction(window, COMBO_MODEL, 1)])
    points = {'status': 'ready', 'threshold': 1.0, 'thresholds': {'median_mad': .5, 'adsketch': .5}}
    base = _scores([window], [.5])
    derived = append_combo_predictions(base, points)
    assert _evaluate([window], [], derived, combo_threshold=1.0) == _evaluate([window], [], base, combo_threshold=1.0)
    with pytest.raises(ValueError, match='disagrees with component'):
        _evaluate([window], [], [*derived[:-1], {**derived[-1], 'score': 2}], combo_threshold=1.0)
    with pytest.raises(ValueError, match='invalid or noncausal'):
        _evaluate([window], [], [*derived[:-1], {**derived[-1], 'component_availability': 'unknown'}], combo_threshold=1.0)


def test_combo_ranking_handles_overflow_from_a_finite_normalized_input():
    windows = [_window(0, label=0), _window(60)]
    predictions = _scores(windows, [math.ulp(0.0), 1.0])
    result = _evaluate(windows, [], predictions, combo_threshold=1.0, mad_threshold=math.ulp(0.0), ads_threshold=None)
    scores = [row['score'] for row in result['predictions'] if row['model_id'] == COMBO_MODEL]
    assert scores == [1.0, float_info.max]
    assert result['models'][COMBO_MODEL]['window_ranking']['average_precision'] == 1.0


def test_bootstrap_false_incident_delta_uses_only_healthy_alerts_and_scored_exposure():
    windows, predictions = [], []
    for index in range(5):
        group = f'group-{index}'
        alert = _window(0, run=group, group=group, label=0, healthy=True)
        healthy = _window(60, run=group, group=group, label=0, healthy=True)
        windows.extend([alert, healthy])
        predictions.append(_prediction(alert, 'adsketch', 1))
        predictions.extend(_prediction(alert, model, 0) for model in ('isolation_forest', 'median_mad'))
    result = _evaluate(windows, [], predictions, combo_threshold=1.0)
    metric = 'false_incidents_per_healthy_service_hour'
    intervals = result['bootstrap']['intervals']
    assert set(intervals) == {f'{candidate}_minus_{baseline}' for candidate, baseline in PAIRS}
    assert all(metric in pair for pair in intervals.values())
    for baseline in ('isolation_forest', 'median_mad'):
        interval = intervals[f'{COMBO_MODEL}_minus_{baseline}'][metric]
        assert interval['lower'] == interval['upper'] == 60
        assert (interval['valid_replicates'], interval['undefined_replicates']) == (1000, 0)
        assert interval['denominators'] == {COMBO_MODEL: 5 / 60, baseline: 5 / 60}
    assert result['models'][COMBO_MODEL]['healthy_false_incidents'] == 5
    assert result['models'][COMBO_MODEL]['healthy_scored_service_hours'] == 5 / 60
    pair = result['coverage']['pairs'][f'{COMBO_MODEL}_minus_isolation_forest']
    assert pair == {'healthy_minutes': 10, 'healthy_both_scored': 5, 'events': 0,
                    'events_both_scored': 0, 'coverage': None}

    no_healthy = _evaluate([{**window, 'healthy': False} for window in windows], [], predictions, combo_threshold=1.0)
    interval = no_healthy['bootstrap']['intervals'][f'{COMBO_MODEL}_minus_median_mad'][metric]
    assert interval['lower'] is interval['upper'] is None
    assert (interval['valid_replicates'], interval['undefined_replicates']) == (0, 1000)


def _alibaba_window(minute, service='api', **extras):
    return _window(minute * 60, run=service, service=service, group=service,
                   dataset='alibaba-proxy', relative_minute=minute, **extras)


def test_pair_coverage_requires_two_scores_on_the_same_event():
    first, second = _alibaba_window(330), _alibaba_window(330, service='other')
    healthy = _alibaba_window(345, label=0, healthy=True)
    events = [_event(row, 330 * 60, kind='alibaba-proxy') for row in (first, second)]
    predictions = [_prediction(first, 'median_mad', .2), _prediction(second, 'isolation_forest', .2),
                   *_scores([healthy], [.2])]
    result = _evaluate([first, second, healthy], events, predictions, combo_threshold=1.0, event_kind='alibaba-proxy')
    primary = result['coverage']['pairs'][f'{COMBO_MODEL}_minus_isolation_forest']
    assert primary == {'healthy_minutes': 1, 'healthy_both_scored': 1, 'events': 2,
                       'events_both_scored': 0, 'coverage': 0}
    assert result['bootstrap']['event_groups'] == 2


def test_alibaba_censors_only_unmatched_alerts_and_uses_scored_healthy_exposure():
    windows = [_alibaba_window(minute, label=0, healthy=minute in (336, 338, 340))
               for minute in (324, 325, 326, 327, 328, 336, 337, 338, 339, 340)]
    eligible = _event(windows[0], 324 * 60, kind='alibaba-proxy')
    excluded = _event(windows[0], 323 * 60, kind='alibaba-proxy', eligible=False,
                      reason='onset-before-scoring-range', censor_until=_at(328 * 60))
    predictions = _scores(windows[:-1], [1, 0, 1, 0, 1, 0, 1, 0, 1])
    result = _evaluate(windows, [eligible, excluded], predictions, combo_threshold=1.0, event_kind='alibaba-proxy')
    audit = _matches(result, COMBO_MODEL)
    assert [row['status'] for row in audit] == ['matched', 'censored', 'censored', 'unmatched', 'unmatched']
    assert audit[1]['event_id'] == audit[2]['event_id'] == excluded['event_id']
    model = result['models'][COMBO_MODEL]
    assert (model['tp'], model['fp'], model['fn'], model['censored_alerts']) == (1, 2, 0, 2)
    assert (model['healthy_false_incidents'], model['healthy_scored_service_hours']) == (0, 2 / 60)
    assert model['false_incidents_per_healthy_service_hour'] == 0


@pytest.mark.parametrize('missing_minutes', [(), (320, 325)])
def test_alibaba_warmup_contributes_to_persistence_without_emitting_alerts(missing_minutes):
    windows = [_alibaba_window(minute, eligible=minute >= 324,
                               eligibility_reason='causal-warmup' if minute < 324 else None)
               for minute in range(320, 326)]
    predictions = _scores(windows, [1, 1, 0, 1, 1, 0])
    predictions = [row for row in predictions if row['bucket_end'] not in {_at(minute * 60) for minute in missing_minutes}]
    result = _evaluate(windows, [], predictions, combo_threshold=1.0, entry=2, event_kind='alibaba-proxy')
    assert [(row['status'], row['emitted_at']) for row in _matches(result, COMBO_MODEL)] == [('unmatched', _at(324 * 60))]
    assert result['coverage']['common_windows'] == (1 if missing_minutes else 2)
    for model in result['models'].values():
        cohorts = [model, model['groups']['api'], model['services']['api']]
        expected_available = 1 if missing_minutes else 2
        expected_warmup_scored = 3 if missing_minutes else 4
        for cohort in cohorts:
            assert cohort['windows'] == cohort['available_windows'] + cohort['unavailable_windows'] == 2
            assert cohort['available_windows'] == expected_available
            assert sum(cohort['unavailable_reasons'].values()) == cohort['unavailable_windows'] == 2 - expected_available
            assert (cohort['warmup_windows'], cohort['warmup_scored']) == (4, expected_warmup_scored)
            assert cohort['window_ranking']['windows'] == expected_available
