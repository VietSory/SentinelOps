"""Benchmark-only shared latency outcomes; no model fitting or calibration."""
import math
from datetime import timedelta

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id
from sentinelops.ml.alibaba_local_evaluation import latency_forecast_labels, metric_event_cases

MODELS = ('slope', 'xgboost', 'ltfp_self_only', 'ltfp_graph')


def project_latency_truth(store, case, service, schedule):
    """Project one independently admitted request timeline onto a frozen score schedule."""
    if not case['oracle_ref'].startswith('artifact://manifests/'):
        raise ValueError('forecast truth requires a request oracle certificate')
    certificate = store.read(case['oracle_ref'])
    if (certificate['certificate_version'] != 'local-http-oracle/v1'
            or certificate['source_result_ref'] != case['source_result_ref']):
        raise ValueError('forecast truth certificate differs from the admitted run')
    oracle = certificate['oracle']
    start, stop = (utc_time(schedule[key]) for key in ('start', 'stop'))
    delay = schedule['cutoff_delay_seconds']
    if (any(point.second or point.microsecond for point in (start, stop)) or stop <= start
            or schedule['cadence_seconds'] != 60 or type(delay) not in (int, float)
            or not math.isfinite(delay) or not 0 <= delay < 60):
        raise ValueError('forecast truth requires a fixed minute schedule')
    start_minute, stop_minute = (int(point.timestamp()) for point in (start, stop))
    buckets = oracle['services'][service]['buckets']
    if any(type(row['p95_latency_ms']) not in (int, float)
           or not math.isfinite(row['p95_latency_ms']) or row['p95_latency_ms'] < 0
           for row in buckets.values() if row['availability'] == 'VERIFIED'):
        raise ValueError('verified oracle bucket lacks finite p95 latency')
    projected, = metric_event_cases([{'oracle': oracle}], service, 'p95_latency_ms', 750, 2)
    latency = projected['oracle']['services'][service]
    onset = utc_time(latency['first_violation_at']) if latency['first_violation_at'] else None
    identity = {key: case[key] for key in ('group_id', 'run_id')}
    identity.update(service=service)
    truth = {**identity, 'evidence_id': case['oracle_ref'], 'score_schedule': dict(schedule)}
    event = None
    if onset is not None and start <= onset < stop:
        event = {**identity, 'onset_at': latency['first_violation_at'],
                 'confirmed_at': latency['confirmed_at'], 'kind': 'latency-slo',
                 'oracle_ref': case['oracle_ref'], 'eligible': True, 'reason': None}
        event['event_id'] = stable_id(event)
        truth.update(outcome='breach', reason=None)
    elif onset is not None and onset < start:
        truth.update(outcome='UNKNOWN', reason='latency-onset-before-schedule')
    elif (utc_time(oracle['ended_at']) < stop + timedelta(minutes=1)
          or any(buckets.get(str(minute), {}).get('availability') != 'VERIFIED'
                 for minute in range(start_minute, stop_minute + 60, 60))):
        truth.update(outcome='UNKNOWN', reason='incomplete-latency-oracle')
    else:
        truth.update(outcome='no-breach', reason=None, healthy_hours=(stop - start).total_seconds() / 3600)
    return truth, event


def forecast_rows(store, row, oracle, health_policy, scores):
    """Join four optional scores to one immutable feature frame and outcome.

    scores maps each MODELS name to {'score': float | None, 'missing_reason':
    str | None}. A missing score requires a reason; an available score forbids one.
    row supplies feature_ref, prediction_cutoff, service, window_start, window_end,
    run_id and group_id. The store verifies the referenced frame's content hash.
    """
    if not row['feature_ref'].startswith('artifact://features/'):
        raise ValueError('benchmark requires a feature artifact reference')
    frame = store.read(row['feature_ref'])
    for key in ('service', 'window_start', 'window_end'):
        if row[key] != frame[key]:
            raise ValueError('benchmark row differs from frozen feature frame')
    if utc_time(row['prediction_cutoff']) != utc_time(frame['as_of']):
        raise ValueError('benchmark cutoff differs from frozen feature frame')
    if (health_policy['latency_ms'], health_policy['entry_buckets']) != (750., 2):
        raise ValueError('benchmark requires latency 750 ms and two buckets')
    if set(scores) != set(MODELS):
        raise ValueError('benchmark requires exactly four model scores')
    for item in scores.values():
        score, reason = item['score'], item['missing_reason']
        if score is None:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('unavailable score requires a reason')
        elif (type(score) not in (int, float) or not math.isfinite(score)
              or reason is not None):
            raise ValueError('benchmark score must be a finite number without missing reason')
    labels = latency_forecast_labels(frame, oracle, health_policy)
    provenance = {key: row[key] for key in ('feature_ref', 'run_id', 'group_id', 'service',
                                          'window_start', 'window_end')}
    provenance.update(oracle_sha256=stable_id(oracle), health_policy_sha256=stable_id(health_policy))
    return [{**provenance, **labels, 'model': model, 'score': scores[model]['score'],
             'score_missing_reason': scores[model]['missing_reason']} for model in MODELS]


def summarize_forecasts(rows, events, *, thresholds=None, policy_identity=None):
    """Summarize adapter rows; events are independent eligible latency onsets.

    Events require event_id, run_id, service, onset_at. Thresholds map each model
    to watch/degrading. policy_identity identifies caller-frozen calibration;
    this helper does not certify calibration or open any data split.
    """
    from sentinelops.ml.local_evaluation import risk_curve
    from sentinelops.workers.risk import EpisodeTracker

    keyed = {}
    for row in rows:
        key = (row['run_id'], row['service'], utc_time(row['window_end']),
               utc_time(row['prediction_cutoff']))
        if row['model'] not in MODELS or (key, row['model']) in keyed:
            raise ValueError('unknown model or duplicate forecast key')
        if not key[2] <= key[3] < key[2] + timedelta(minutes=1):
            if row['risk_label'] is not None or row['score'] is not None:
                raise ValueError('available forecast has invalid cutoff')
        if row['score'] is not None and (type(row['score']) not in (int, float) or not math.isfinite(row['score'])):
            raise ValueError('forecast score must be finite')
        if row['risk_label'] is not None and (type(row['risk_label']) is not int or row['risk_label'] not in (0, 1)):
            raise ValueError('forecast target must be binary or UNKNOWN')
        keyed[key, row['model']] = row
    keys = sorted({key for key, _ in keyed})
    for key in keys:
        cohort = [keyed.get((key, model)) for model in MODELS]
        if any(row is None for row in cohort):
            raise ValueError('missing model row; use explicit unavailable score')
        fields = ('group_id', 'feature_ref', 'oracle_sha256', 'health_policy_sha256',
                  'risk_label', 'risk_missing_reason', 'slo_onset_at')
        if any(any(row[field] != cohort[0][field] for field in fields) for row in cohort[1:]):
            raise ValueError('forecast outcomes or provenance differ across models')
    labelled = [key for key in keys if keyed[key, MODELS[0]]['risk_label'] is not None]
    common = [key for key in labelled if all(keyed[key, model]['score'] is not None for model in MODELS)]
    event_keys, event_ids = set(), set()
    ordered_events = sorted(events, key=lambda event: (event['run_id'], event['service'], utc_time(event['onset_at'])))
    for event in ordered_events:
        key = (event['run_id'], event['service'], utc_time(event['onset_at']))
        if key in event_keys or event['event_id'] in event_ids:
            raise ValueError('duplicate oracle event')
        event_keys.add(key)
        event_ids.add(event['event_id'])
    models = {}
    for model in MODELS:
        selected = [keyed[key, model] for key in common]
        metrics = risk_curve([row['risk_label'] for row in selected], [row['score'] for row in selected])
        metrics.update(unavailable_windows=sum(keyed[key, model]['score'] is None for key in keys),
                       labelled_unavailable_windows=sum(keyed[key, model]['score'] is None for key in labelled))
        threshold = (thresholds or {}).get(model)
        if threshold is None or not policy_identity:
            metrics['episodes'] = {'status': 'not-ready', 'missing_reason': 'frozen-thresholds-and-policy-required',
                                   'recall': None, 'events': len(events)}
            models[model] = metrics
            continue
        watch, degrading = threshold['watch'], threshold['degrading']
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (watch, degrading))
            or watch > degrading):
            raise ValueError('invalid watch/degrading thresholds')
        tracker, alerts = EpisodeTracker(), []
        for key in keys:
            row = keyed[key, model]
            score = row['score']
            state = ('unavailable' if score is None else 'degrading' if score >= degrading
                     else 'watch' if score >= watch else 'normal')
            episode = tracker.update((key[0], key[1]), key[2], state)
            if episode['event'] == 'started':
                alerts.append((key[0], key[1], key[3]))
        used, matches = set(), []
        for event in ordered_events:
            onset = utc_time(event['onset_at'])
            candidates = [index for index, (run, service, decision) in enumerate(alerts)
                          if index not in used and (run, service) == (event['run_id'], event['service'])
                          and onset - timedelta(seconds=300) <= decision < onset]
            index = min(candidates, key=lambda index: alerts[index][2]) if candidates else None
            lead = (onset - alerts[index][2]).total_seconds() if index is not None else None
            if index is not None:
                used.add(index)
            matches.append({'event_id': event['event_id'], 'decision_at': alerts[index][2].isoformat() if index is not None else None,
                            'lead_seconds': lead, 'status': 'miss' if lead is None else 'matched' if lead >= 60 else 'late'})
        metrics['episodes'] = {'status': 'ready', 'policy_identity': policy_identity, 'events': len(events),
                               'alerts': len(alerts), 'unmatched_alerts': len(alerts) - len(used),
                               'recall': len(used) / len(events) if events else None,
                               'lead_qualified_recall': sum(match['status'] == 'matched' for match in matches) / len(events) if events else None,
                               'missing_reason': None if events else 'no-eligible-events', 'matches': matches}
        models[model] = metrics
    return {'windows': len(keys), 'unknown_windows': len(keys) - len(labelled),
            'labelled_windows': len(labelled), 'common_windows': len(common), 'models': models}
