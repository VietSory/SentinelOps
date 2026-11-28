"""Lane B scoring and evaluation on caller-admitted, already captured timelines."""
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import timedelta
import math
import random

from sentinelops.contracts import utc_time
from sentinelops.ml.evaluation import event_metrics
from sentinelops.ml.local_evaluation import persistent_states, risk_curve
from sentinelops.ml.model_benchmark_detector_combo import (
    COMBO_MODEL, COMPONENT_AVAILABILITIES, KEY_FIELDS, append_combo_predictions,
)
from sentinelops.ml.model_benchmark_reports import _quantile
from sentinelops.telemetry.core import stable_id, timestamp


MODELS = ('median_mad', 'isolation_forest', 'adsketch')
PAIRS = ((COMBO_MODEL, 'isolation_forest'), ('median_mad', 'isolation_forest'),
         (COMBO_MODEL, 'median_mad'), ('adsketch', 'isolation_forest'), ('adsketch', 'median_mad'))
CLOCKS = ('replay', 'arrival-aware')
PATTERNS = ('known', 'novel')
STEP_SECONDS = 60
DEADLINE_SECONDS = 300
BOOTSTRAP_REPLICATES = 1000
BOOTSTRAP_SEED = 42


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _key(row):
    return tuple(row[field] for field in KEY_FIELDS)


def _grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def _windows(windows, clock):
    if clock not in CLOCKS:
        raise ValueError('invalid detector clock')
    indexed, owners = {}, {}
    for row in windows:
        if not all(_text(row[field]) for field in KEY_FIELDS):
            raise ValueError('detector window identity is required')
        end = utc_time(row['bucket_end'])
        if end.second or end.microsecond:
            raise ValueError('detector window bucket must close on a UTC minute')
        available = utc_time(row['available_at']) if row.get('available_at') is not None else None
        decision = utc_time(row['decision_at']) if row.get('decision_at') is not None else None
        if clock == 'replay':
            if decision is not None and decision != end:
                raise ValueError('replay decision must equal bucket end')
            decision = end
        if decision is None or decision < end or available is not None and available < end:
            raise ValueError('invalid detector availability or decision clock')
        eligible, label, healthy = row['eligible'], row['y'], row['healthy']
        reason = row.get('eligibility_reason', row.get('reason'))
        label_reason = row.get('label_reason')
        if (type(eligible) is not bool or type(healthy) is not bool
                or label is not None and (type(label) is not int or label not in (0, 1))
                or healthy and label != 0 or not eligible and not _text(reason)
                or label_reason is not None and not _text(label_reason)):
            raise ValueError('invalid independent detector window evidence')
        window = {**row, 'bucket_end': timestamp(end), 'decision_at': timestamp(decision),
                  'available_at': timestamp(available) if available is not None else None,
                  'clock': clock, 'eligibility_reason': reason if not eligible else None,
                  'label_reason': (label_reason or 'oracle-unknown') if label is None else None}
        key = _key(window)
        if key in indexed:
            raise ValueError('duplicate detector window key')
        if owners.setdefault(key[2], key[:2]) != key[:2]:
            raise ValueError('detector run belongs to multiple groups or datasets')
        indexed[key] = window
    if not indexed or len({key[0] for key in indexed}) != 1:
        raise ValueError('detector evaluation requires windows from one dataset')
    ordered = dict(sorted(indexed.items()))
    for series in _grouped(ordered.values(), lambda row: _key(row)[:-1]).values():
        decisions = [utc_time(row['decision_at']) for row in series]
        if any(next_decision < decision for decision, next_decision in zip(decisions, decisions[1:])):
            raise ValueError('detector decision clocks decrease within a series')
    return ordered


def _availability_reason(window):
    relative_minute = window.get('relative_minute')
    warmup = (window['dataset'] == 'alibaba-proxy' and type(relative_minute) is int
              and 259 <= relative_minute < 324)
    if not window['eligible'] and not warmup:
        return window['eligibility_reason']
    if window['clock'] == 'arrival-aware':
        if window['available_at'] is None:
            return 'bucket-availability-unrecorded'
        if utc_time(window['available_at']) > utc_time(window['decision_at']):
            return 'bucket-arrived-after-decision'
    return None


def _prediction(window, model, *, score=None, reason=None, pattern_kind=None,
                pattern_id=None, inference_seconds=None, component_availability=None):
    fields = KEY_FIELDS + ('available_at', 'decision_at', 'clock', 'eligible', 'eligibility_reason',
                           'y', 'label_reason', 'healthy')
    return {**{field: window[field] for field in fields},
            'raw_ref': window.get('raw_ref'), 'oracle_ref': window.get('oracle_ref'),
            'model_id': model, 'score': score,
            'status': 'available' if score is not None else 'unavailable', 'reason': reason,
            'pattern_kind': pattern_kind, 'pattern_id': pattern_id, 'inference_seconds': inference_seconds,
            'graph_neighbor_count': None, 'component_availability': component_availability}


def score_detector_windows(frozen, windows, *, clock, measure_inference=False):
    """Score the base models using raw latency in the window's ``value`` field.

    Past ineligible anchors may provide predictor history when they carry a valid
    observation. Arrival-aware replay requires their recorded observation arrival.
    """
    from sentinelops.ml.model_benchmark_detector import predict_detectors
    from sentinelops.ml.model_benchmark_detector_fit import BASELINE_LOOKBACK, DetectorMinute, project_detector_run

    indexed = _windows(windows, clock)
    if frozen.config.bucket_seconds != STEP_SECONDS:
        raise ValueError('detector bundle cadence differs from benchmark windows')
    lookback = (BASELINE_LOOKBACK + frozen.config.window_length - 1) * STEP_SECONDS
    results = {}
    for series in _grouped(indexed.values(), lambda row: _key(row)[:-1]).values():
        run_id, service = series[0]['run_id'], series[0]['service']
        ends = [int(utc_time(row['bucket_end']).timestamp()) for row in series]
        arrivals = [utc_time(row['available_at']) if row['available_at'] is not None else None for row in series]
        values = [row.get('value') for row in series]
        if any(value is not None and (not _number(value) or value < 0) for value in values):
            raise ValueError('detector values must be nonnegative finite latency in milliseconds')

        def predict(minutes, *, target_only=False):
            projected = project_detector_run(minutes, run_id=run_id)
            if target_only:
                projected = projected[-frozen.config.window_length:]
            options = {'measure_inference': True} if measure_inference else {}
            predictions = {}
            for pred in predict_detectors(frozen, projected, **options):
                key = pred.timestamp, pred.model_id
                if pred.run_id != run_id or pred.service != service or pred.model_id not in MODELS or key in predictions:
                    raise ValueError('detector core returned invalid prediction identities')
                predictions[key] = pred
            return predictions

        replay = predict(DetectorMinute(service, end, value) for end, value in zip(ends, values)) if clock == 'replay' else {}
        for index, window in enumerate(series):
            end = ends[index]
            reason = _availability_reason(window)
            if reason is None and values[index] is None:
                reason = 'bucket-value-unavailable'
            scores = replay
            if reason is None and clock == 'arrival-aware':
                cutoff = utc_time(window['decision_at'])
                first = bisect_left(ends, end - lookback, hi=index)
                minutes = (DetectorMinute(service, ends[pos], values[pos] if arrivals[pos] is not None
                                          and arrivals[pos] <= cutoff else None)
                           for pos in range(first, index + 1))
                scores = predict(minutes, target_only=True)
            for model in MODELS:
                pred = scores.get((end, model)) if reason is None else None
                if pred is None:
                    row = _prediction(window, model, reason=reason or 'missing-model-prediction')
                else:
                    row = _prediction(window, model, score=pred.score, reason=pred.reason,
                                      pattern_kind=pred.pattern_kind, pattern_id=pred.pattern_id,
                                      inference_seconds=getattr(pred, 'inference_seconds', None))
                    if pred.status != row['status']:
                        raise ValueError('detector core status differs from its score')
                results[_key(window), model] = row
    return [results[key, model] for key in indexed for model in MODELS]


def _audit_predictions(windows, predictions, models, component_thresholds):
    indexed = {}
    for prediction in predictions:
        key_fields = {*KEY_FIELDS, 'model_id'}
        if not all(_text(prediction[field]) for field in key_fields):
            raise ValueError('detector prediction identity is required')
        key = (*_key(prediction)[:-1], timestamp(utc_time(prediction['bucket_end'])))
        model = prediction['model_id']
        if key not in windows or model not in models or (key, model) in indexed:
            raise ValueError('duplicate detector prediction or undeclared window/model')
        window = windows[key]
        score, reason = prediction.get('score'), prediction.get('reason')
        timing = prediction.get('inference_seconds')
        pattern, pattern_id = prediction.get('pattern_kind'), prediction.get('pattern_id')
        components = prediction.get('component_availability')
        unavailable = _availability_reason(window)
        if (score is None and (not _text(reason) or pattern is not None or pattern_id is not None or timing is not None
                               or unavailable is not None and reason != unavailable)
                or score is not None and (not _number(score) or reason is not None or unavailable is not None)
                or timing is not None and (not _number(timing) or timing < 0)
                or model == COMBO_MODEL and (components not in COMPONENT_AVAILABILITIES or timing is not None)
                or model != COMBO_MODEL and components is not None
                or pattern is not None and (model != 'adsketch' or pattern not in ('normal', *PATTERNS))
                or pattern_id is not None and (pattern is None or not _text(pattern_id))):
            raise ValueError('invalid or noncausal detector prediction')
        canonical = _prediction(window, model, score=score, reason=reason, pattern_kind=pattern,
                                pattern_id=pattern_id, inference_seconds=timing, component_availability=components)
        for field, value in canonical.items():
            if field not in prediction:
                continue
            supplied = prediction[field]
            if supplied is not None and field in ('bucket_end', 'decision_at', 'available_at'):
                supplied = timestamp(utc_time(supplied))
            if supplied != value:
                raise ValueError(f'detector prediction disagrees with declared window: {field}')
        indexed[key, model] = canonical
    base = [indexed.get((key, model)) or _prediction(window, model, reason=_availability_reason(window)
                                                     or 'missing-model-prediction')
            for key, window in windows.items() for model in MODELS]
    if COMBO_MODEL not in models:
        return base
    rows = append_combo_predictions(base, {'status': 'ready', 'threshold': 1.0, 'thresholds': component_thresholds})
    for row in rows[len(base):]:
        supplied = indexed.get((_key(row), COMBO_MODEL))
        if supplied is not None and any(supplied[field] != row[field] for field in ('score', 'component_availability')):
            raise ValueError('combo prediction disagrees with component scores or availability')
    return rows


def _audit_events(windows, events, event_kind):
    dataset = next(iter(windows))[0]
    groups = {key[1] for key in windows}
    owners = {key[2]: key[1] for key in windows}
    event_ids, event_keys, output = set(), set(), []
    for event in events:
        if not all(_text(event[field]) for field in ('event_id', 'group_id', 'run_id', 'service', 'kind')):
            raise ValueError('detector oracle event identity is required')
        group, run, service = (event[field] for field in ('group_id', 'run_id', 'service'))
        if event.get('dataset', dataset) != dataset or group not in groups or owners.setdefault(run, group) != group:
            raise ValueError('detector oracle event has an undeclared dataset or group, or a conflicting run')
        eligible, reason = event['eligible'], event.get('reason')
        onset = utc_time(event['onset_at']) if event.get('onset_at') is not None else None
        confirmed = utc_time(event['confirmed_at']) if event.get('confirmed_at') is not None else None
        censor_until = utc_time(event['censor_until']) if event.get('censor_until') is not None else None
        if (type(eligible) is not bool or eligible and (onset is None or confirmed is None)
                or not eligible and not _text(reason) or confirmed is not None and (onset is None or confirmed < onset)
                or censor_until is not None and (onset is None or censor_until < onset or eligible)
                or event.get('pattern_kind') not in (None, *PATTERNS)):
            raise ValueError('invalid independent detector oracle event')
        identity = run, service, onset, event['kind']
        if event['event_id'] in event_ids or identity in event_keys:
            raise ValueError('duplicate detector oracle event')
        event_ids.add(event['event_id'])
        event_keys.add(identity)
        output.append({**event, 'dataset': dataset, 'oracle_ref': event.get('oracle_ref'),
                       **({'censor_until': timestamp(censor_until)} if censor_until is not None else {}),
                       'onset_at': timestamp(onset) if onset is not None else None,
                       'confirmed_at': timestamp(confirmed) if confirmed is not None else None, 'reason': reason,
                       'in_scope': eligible and event['kind'] == event_kind,
                       'deadline_at': timestamp(onset + timedelta(seconds=DEADLINE_SECONDS)) if onset is not None else None})
    return sorted(output, key=lambda row: (row['group_id'], row['run_id'], row['service'],
                                           utc_time(row['onset_at']).timestamp() if row['onset_at'] else -math.inf,
                                           row['event_id']))


def _episodes(rows, threshold, persistence):
    alerts = []
    for series in _grouped(rows, lambda row: _key(row)[:-1]).values():
        flags = [{'window_end': row['bucket_end'], 'decision_at': row['decision_at'],
                  'flag': row['score'] >= threshold if row['score'] is not None else None} for row in series]
        episode = None
        for row, state in zip(series, persistent_states(flags, 'flag', persistence)):
            if not state['active']:
                episode = None
            if state['entered']:
                episode = stable_id([row['clock'], *_key(row), row['model_id']])
                if row['eligible']:
                    alerts.append(row)
            row.update(state='unavailable' if not state['available'] else 'incident' if state['active'] else 'normal',
                       episode_id=episode, emitted_at=row['decision_at'] if state['entered'] and row['eligible'] else None)
    return alerts


def _ratio_metrics(tp, fp, fn, healthy_fp, healthy_scored_minutes):
    exposure = healthy_scored_minutes * STEP_SECONDS / 3600
    denominators = {'precision': tp + fp, 'recall': tp + fn, 'f1': 2 * tp + fp + fn,
                    'false_incidents_per_healthy_service_hour': exposure}
    numerators = {'precision': tp, 'recall': tp, 'f1': 2 * tp,
                  'false_incidents_per_healthy_service_hour': healthy_fp}
    metrics = {key: numerators[key] / denom if denom else None for key, denom in denominators.items()}
    reasons = {'precision': 'no-alerts', 'recall': 'no-eligible-events', 'f1': 'no-alerts-or-eligible-events',
               'false_incidents_per_healthy_service_hour': 'no-eligible-healthy-exposure'}
    return {**metrics, 'tp': tp, 'fp': fp, 'fn': fn, 'miss': fn, 'eligible_events': tp + fn,
            'healthy_false_incidents': healthy_fp, 'healthy_scored_service_hours': exposure,
            'denominators': denominators,
            'missing_reasons': {metric: reasons[metric] for metric in metrics if metrics[metric] is None}}


def _scope(row):
    return row['run_id'], row['service']


def _supported_events(rows, events):
    decisions = _grouped((row for row in rows if row['eligible'] and row['score'] is not None), _scope)
    return {event['event_id'] for event in events
            if any(utc_time(event['onset_at']) <= utc_time(row['decision_at']) <= utc_time(event['deadline_at'])
                   for row in decisions[_scope(event)])}


def _match_audit(alerts, events, censored_events, supported, model, clock):
    candidates = _grouped(events, _scope)
    exclusions = _grouped(censored_events, _scope)
    unmatched = {event['event_id'] for event in events}
    matches = []

    def audit(status, *, event=None, alert=None, reason=None):
        source = event if event is not None else alert
        emission = alert['emitted_at'] if alert is not None else None
        onset = event['onset_at'] if event is not None else None
        return {**{key: source[key] for key in KEY_FIELDS[:-1]}, 'model_id': model, 'clock': clock,
                'event_id': event['event_id'] if event is not None else None,
                'event_pattern_kind': event.get('pattern_kind') if event is not None else None,
                'onset_at': onset, 'deadline_at': event['deadline_at'] if event is not None else None,
                'event_oracle_ref': event['oracle_ref'] if event is not None else None,
                'bucket_end': alert['bucket_end'] if alert is not None else None,
                'raw_ref': alert['raw_ref'] if alert is not None else None,
                'oracle_ref': alert['oracle_ref'] if alert is not None else None,
                'episode_id': alert['episode_id'] if alert is not None else None,
                'emitted_at': emission, 'detection_delay_seconds':
                    (utc_time(emission) - utc_time(onset)).total_seconds() if emission and onset else None,
                'pattern_kind': alert['pattern_kind'] if alert is not None else None,
                'pattern_id': alert['pattern_id'] if alert is not None else None,
                'status': status, 'reason': reason}

    for alert in sorted(alerts, key=lambda row: (utc_time(row['emitted_at']), _key(row))):
        emission = utc_time(alert['emitted_at'])
        event = next((candidate for candidate in candidates[_scope(alert)]
                      if candidate['event_id'] in unmatched
                      and utc_time(candidate['onset_at']) <= emission <= utc_time(candidate['deadline_at'])), None)
        if event is None:
            exclusion = next((candidate for candidate in exclusions[_scope(alert)]
                              if utc_time(candidate['onset_at']) <= emission <= utc_time(candidate['censor_until'])), None)
            if exclusion is None:
                matches.append(audit('unmatched', alert=alert, reason='no-unmatched-eligible-event-in-window'))
            else:
                matches.append(audit('censored', event=exclusion, alert=alert, reason='ineligible-event-censor-window'))
            continue
        unmatched.remove(event['event_id'])
        matches.append(audit('matched', event=event, alert=alert))
    for event in events:
        if event['event_id'] not in unmatched:
            continue
        reason = 'no-matching-alert' if event['event_id'] in supported else 'no-available-score-in-window'
        matches.append(audit('miss', event=event, reason=reason))
    return matches


def _event_report(rows, alerts, events):
    runs = {}
    for row in rows:
        key = row['run_id'], row['service']
        run = runs.setdefault(key, {'run_id': stable_id(key), 'events': [], 'alerts': [],
                                   'eligible_healthy_service_seconds': 0})
        run['eligible_healthy_service_seconds'] += STEP_SECONDS * (row['eligible'] and row['healthy'])
    for event in events:
        key = event['run_id'], event['service']
        runs.setdefault(key, {'run_id': stable_id(key), 'events': [], 'alerts': []})['events'].append(event)
    for alert in alerts:
        runs[alert['run_id'], alert['service']]['alerts'].append(alert)
    metrics = event_metrics(list(runs.values()))
    return metrics['tp'], metrics['fp'], metrics['fn']


def _window_ranking(rows, common=None):
    selected = [row for row in rows if row['eligible'] and row['score'] is not None and row['y'] is not None
                and (common is None or _key(row) in common)]
    return risk_curve([row['y'] for row in selected], [row['score'] for row in selected])


def _cohort_counts(rows, alerts, events, matches, legacy_rate):
    tp, fp, fn = _event_report(rows, alerts, events)
    healthy = {_key(row) for row in rows if row['eligible'] and row['healthy']}
    healthy_scored = sum(row['score'] is not None and _key(row) in healthy for row in rows)
    healthy_fp = sum(row['status'] == 'unmatched' and _key(row) in healthy for row in matches)
    trace = Counter(row['status'] for row in matches)
    if (tp, fp, fn) != (trace['matched'], trace['unmatched'], trace['miss']):
        raise ValueError('detector event metrics disagree with the one-to-one audit')
    return (tp, fp, fn, fp if legacy_rate else healthy_fp, len(healthy) if legacy_rate else healthy_scored)


def _window_availability(rows):
    eligible = [row for row in rows if row['eligible']]
    unavailable = [row for row in eligible if row['score'] is None]
    warmup = [row for row in rows if row['dataset'] == 'alibaba-proxy' and not row['eligible']
              and row['eligibility_reason'] == 'causal-warmup']
    return {'windows': len(eligible), 'available_windows': len(eligible) - len(unavailable),
            'unavailable_windows': len(unavailable),
            'unavailable_reasons': dict(sorted(Counter(row['reason'] for row in unavailable).items())),
            'warmup_windows': len(warmup), 'warmup_scored': sum(row['score'] is not None for row in warmup)}


def _cohort_metrics(rows, event_counts, common):
    return {**_ratio_metrics(*event_counts),
            'eligible_healthy_service_hours': sum(row['eligible'] and row['healthy'] for row in rows) / 60,
            **_window_availability(rows),
            'window_ranking': _window_ranking(rows), 'paired_window_ranking': _window_ranking(rows, common)}


def _model_report(rows, alerts, events, matches, common, legacy_rate):
    groups = sorted({row['group_id'] for row in rows})
    censored = {row['episode_id'] for row in matches if row['status'] == 'censored'}
    alerts = [row for row in alerts if row['episode_id'] not in censored]
    scored_matches = [row for row in matches if row['status'] != 'censored']
    by_group = [_grouped(items, lambda row: row['group_id']) for items in (rows, alerts, events, scored_matches)]
    counts = {group: _cohort_counts(*(items[group] for items in by_group), legacy_rate) for group in groups}
    report = _ratio_metrics(*(sum(values) for values in zip(*counts.values())))
    available = [row for row in rows if row['eligible'] and row['score'] is not None]
    healthy = [row for row in rows if row['eligible'] and row['healthy']]
    delays = [row['detection_delay_seconds'] for row in matches if row['status'] == 'matched']
    timings = [row['inference_seconds'] for row in available if row['inference_seconds'] is not None]
    report['denominators'].update(mttd_p50_seconds=len(delays), mttd_p90_seconds=len(delays),
                                  inference_p95_seconds=len(timings))
    missing = report['missing_reasons']
    if not delays:
        missing['mttd'] = 'no-matched-events'
    if not timings:
        missing['inference_p95_seconds'] = 'no-timed-predictions'
    patterns = {}
    for pattern in PATTERNS:
        independent = [row for row in scored_matches if row['event_pattern_kind'] == pattern]
        matched = sum(row['status'] == 'matched' for row in independent)
        patterns[pattern] = {'eligible_events': len(independent), 'matched_events': matched,
                             'recall': matched / len(independent) if independent else None,
                             'missing_reason': None if independent else 'no-independent-pattern-events',
                             'alert_patterns': sum(row['pattern_kind'] == pattern for row in alerts),
                             'matched_alert_patterns': sum(row['status'] == 'matched' and row['pattern_kind'] == pattern
                                                           for row in matches)}
    return {**report, **_window_availability(rows),
            'eligible_healthy_service_hours': len(healthy) / 60, 'censored_alerts': len(censored),
            'scored_healthy_service_hours': sum(row['score'] is not None for row in healthy) * STEP_SECONDS / 3600,
            'unavailable_event_misses': sum(row['reason'] == 'no-available-score-in-window' for row in matches),
            'mttd_p50_seconds': _quantile(delays, .5), 'mttd_p90_seconds': _quantile(delays, .9),
            'inference_p95_seconds': _quantile(timings, .95), 'timed_predictions': len(timings),
            'window_ranking': _window_ranking(rows), 'paired_window_ranking': _window_ranking(rows, common),
            'patterns': patterns, 'groups': {
                group: _cohort_metrics(by_group[0][group], counts[group], common) for group in groups},
            'services': {service: _cohort_metrics(items[0], _cohort_counts(*items, legacy_rate), common)
                         for service, items in _service_cohorts(rows, alerts, events, scored_matches)}}, counts


def _service_cohorts(*items):
    grouped = [_grouped(records, lambda row: row['service']) for records in items]
    for service in sorted(set().union(*(set(group) for group in grouped))):
        yield service, [group[service] for group in grouped]


def _bootstrap(group_counts, models, pairs, event_groups):
    groups = sorted(group_counts[MODELS[0]])
    rng = random.Random(BOOTSTRAP_SEED)
    draws = [Counter(rng.choices(groups, k=len(groups))) for _ in range(BOOTSTRAP_REPLICATES)] if len(groups) >= 5 else []
    metrics = ('precision', 'recall', 'f1', 'false_incidents_per_healthy_service_hour')
    sampled = {model: [_ratio_metrics(*(sum(group_counts[model][group][index] * times for group, times in draw.items())
                                       for index in range(5))) for draw in draws] for model in models}
    intervals = {}
    for candidate, baseline in pairs:
        intervals[f'{candidate}_minus_{baseline}'] = {}
        for metric in metrics:
            values = [left[metric] - right[metric] for left, right in zip(sampled[candidate], sampled[baseline])
                      if left[metric] is not None and right[metric] is not None]
            lower, upper = _quantile(values, .025), _quantile(values, .975)
            intervals[f'{candidate}_minus_{baseline}'][metric] = {
                'lower': lower, 'upper': upper, 'valid_replicates': len(values),
                'undefined_replicates': len(draws) - len(values),
                'denominators': {model: models[model]['denominators'][metric] for model in (candidate, baseline)},
                'missing_reason': (None if values else 'at-least-five-declared-groups-required'
                                   if not draws else 'no-defined-paired-replicates')}
    return {'group_ids': groups, 'groups': len(groups), 'event_groups': event_groups, 'seed': BOOTSTRAP_SEED,
            'replicates': BOOTSTRAP_REPLICATES, 'attempted_replicates': len(draws), 'intervals': intervals,
            'method': 'paired-whole-group-percentile-linear'}


def _pair_coverage(healthy, truth, scored, supported, pairs):
    coverage = {}
    for candidate, baseline in pairs:
        healthy_both = len(healthy & scored[candidate] & scored[baseline])
        events_both = len(supported[candidate] & supported[baseline])
        coverage[f'{candidate}_minus_{baseline}'] = {
            'healthy_minutes': len(healthy), 'healthy_both_scored': healthy_both,
            'events': len(truth), 'events_both_scored': events_both,
            'coverage': min(healthy_both / len(healthy), events_both / len(truth)) if healthy and truth else None,
        }
    return coverage


def _adsketch_increment(rows, model_reports, matches, truth, exclusions, points, clock, persistence):
    base = [row for row in rows if row['model_id'] in MODELS]
    mad_point = {'status': 'ready', 'threshold': 1.0, 'thresholds': {**points, 'adsketch': None}}
    counterfactual = append_combo_predictions(base, mad_point)[len(base):]
    model = 'mad_at_combo_threshold'
    for row in counterfactual:
        row['model_id'] = model
    alerts = _episodes(counterfactual, 1.0, persistence)
    supported = _supported_events(counterfactual, truth)
    audit = _match_audit(alerts, truth, exclusions, supported, model, clock)
    censored = {row['episode_id'] for row in audit if row['status'] == 'censored'}
    control = _ratio_metrics(*_cohort_counts(counterfactual, [row for row in alerts if row['episode_id'] not in censored],
                                             truth, audit, False))
    matched = defaultdict(set)
    for row in (*matches, *audit):
        if row['status'] == 'matched':
            matched[row['model_id']].add(row['event_id'])
    combo, solo = model_reports[COMBO_MODEL], model_reports['median_mad']
    combo_events, mad_events = matched[COMBO_MODEL], matched['median_mad']
    metric = 'false_incidents_per_healthy_service_hour'
    return {'combo_only_matched_events': sorted(combo_events - mad_events),
            'mad_only_matched_events': sorted(mad_events - combo_events),
            'delta_tp': combo['tp'] - solo['tp'], 'delta_fp': combo['fp'] - solo['fp'],
            f'delta_{metric}': combo[metric] - solo[metric] if combo[metric] is not None and solo[metric] is not None else None,
            model: {key: control[key] for key in ('tp', 'fp', 'fn', 'healthy_false_incidents',
                                                   'healthy_scored_service_hours', metric)},
            'attributable_matched_events': sorted(combo_events - matched[model]),
            'attributable_false_incidents': combo['fp'] - control['fp']}


def evaluate_detectors(windows, events, predictions, *, clock, thresholds, policy_identity, persistence,
                       event_kind='latency', component_thresholds=None):
    """Apply pinned operating points and report independent event and window denominators."""
    has_combo = COMBO_MODEL in thresholds
    active_models = (*MODELS, COMBO_MODEL) if has_combo else MODELS
    if (not _text(policy_identity) or event_kind not in ('latency', 'alibaba-proxy')
            or set(thresholds) != set(active_models) or not all(_number(value) for value in thresholds.values())
            or has_combo and (thresholds[COMBO_MODEL] != 1.0 or component_thresholds is None)
            or persistence.get('step_seconds') != STEP_SECONDS
            or any(type(persistence.get(field)) is not int or persistence[field] < 1
                   for field in ('entry_windows', 'exit_windows'))):
        raise ValueError('detector evaluation requires pinned operating points with any combo fixed at 1.0')
    ordered = _windows(windows, clock)
    rows = _audit_predictions(ordered, predictions, active_models, component_thresholds)
    events = _audit_events(ordered, events, event_kind)
    truth = [event for event in events if event['in_scope']]
    exclusions = [event for event in events if event['kind'] == event_kind == 'alibaba-proxy'
                  and not event['eligible'] and event.get('censor_until') is not None]
    score_counts = Counter(_key(row) for row in rows if row['eligible'] and row['score'] is not None)
    common = {key for key, count in score_counts.items() if count == len(active_models)}
    eligible = [window for window in ordered.values() if window['eligible']]
    coverage = {'windows': len(ordered), 'eligible_windows': len(eligible),
                'unknown_windows': sum(window['y'] is None for window in eligible),
                'labelled_windows': sum(window['y'] is not None for window in eligible),
                'common_windows': len(common), 'common_labelled_windows': sum(ordered[key]['y'] is not None for key in common),
                'oracle_events': len(truth), 'excluded_oracle_events': len(events) - len(truth)}
    models, group_counts, matches = {}, {}, []
    by_model = _grouped(rows, lambda row: row['model_id'])
    scored, supported = {}, {}
    for model in active_models:
        model_rows = by_model[model]
        scored[model] = {_key(row) for row in model_rows if row['eligible'] and row['score'] is not None}
        supported[model] = _supported_events(model_rows, truth)
        alerts = _episodes(model_rows, thresholds[model], persistence)
        audit = _match_audit(alerts, truth, exclusions, supported[model], model, clock)
        models[model], group_counts[model] = _model_report(
            model_rows, alerts, truth, audit, common, legacy_rate=not has_combo and event_kind == 'latency')
        matches.extend(audit)
    pairs = [(candidate, baseline) for candidate, baseline in PAIRS if candidate in models and baseline in models]
    healthy = {_key(window) for window in eligible if window['healthy']}
    coverage['pairs'] = _pair_coverage(healthy, truth, scored, supported, pairs)
    result = {'dataset': next(iter(ordered))[0], 'clock': clock, 'event_kind': event_kind,
            'policy_identity': policy_identity, 'persistence': dict(persistence), 'thresholds': dict(thresholds),
            'predictions': rows, 'events': events, 'matches': matches, 'models': models, 'coverage': coverage,
            'bootstrap': _bootstrap(group_counts, models, pairs, len({event['service'] for event in truth}))}
    if has_combo:
        result['adsketch_increment'] = _adsketch_increment(
            rows, models, matches, truth, exclusions, component_thresholds, clock, persistence)
    return result
