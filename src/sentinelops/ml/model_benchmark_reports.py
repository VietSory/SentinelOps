"""Pure forecast reports over caller-certified truth; no fitting or calibration."""
import math
import random
from sentinelops.contracts import utc_time

from sentinelops.ml.model_benchmark_forecast import MODELS, project_latency_truth, schedule_cutoffs, summarize_forecasts


def forecast_report_from_oracles(store, rows, cases, *, thresholds=None, policy_identity=None, provenance):
    """Build report denominators for all admitted run/services, including unscored ones."""
    truth, events = [], []
    for case in cases:
        for service, schedule in case['score_schedules'].items():
            record, projected_events = project_latency_truth(store, case, service, schedule)
            truth.append(record)
            events.extend(projected_events)
    return forecast_report(rows, events, thresholds=thresholds, policy_identity=policy_identity,
                           truth=truth, provenance=provenance)


def _quantile(values, probability):
    """Linear interpolation at (n - 1) * probability."""
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * probability
    lower = int(index)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (index - lower)


def _coverage(record, rows):
    """Require every declared minute anchor and its exact decision clock."""
    schedule = record.get('score_schedule')
    if schedule is None:
        return {'complete': False, 'expected_windows': None, 'scored_windows': 0,
                'scored_service_hours': 0, 'missing_reason': 'score-schedule-required'}
    start, stop = utc_time(schedule['start']), utc_time(schedule['stop'])
    cadence = schedule['cadence_seconds']
    cutoffs = schedule_cutoffs(schedule)
    seconds = (stop - start).total_seconds()
    if (record.get('healthy_hours') is not None and
            not math.isclose(record['healthy_hours'], seconds / 3600, rel_tol=1e-12, abs_tol=1e-12)):
        raise ValueError('invalid minute score schedule or inconsistent exposure')
    expected = int(seconds / cadence)
    anchors = {}
    invalid = False
    for row in rows:
        anchor = utc_time(row['window_end'])
        if anchor in anchors or not start <= anchor < stop or (anchor - start).total_seconds() % cadence:
            invalid = True
        anchors[anchor] = row
    scored = sum(row['score'] is not None and cutoffs[anchor] is not None and
                 utc_time(row['prediction_cutoff']) == cutoffs[anchor] for anchor, row in anchors.items()
                 if start <= anchor < stop and not (anchor - start).total_seconds() % cadence)
    complete = not invalid and len(anchors) == expected and scored == expected
    return {'complete': complete, 'expected_windows': expected, 'scored_windows': scored,
            'scored_service_hours': scored * cadence / 3600,
            'missing_reason': None if complete else 'incomplete-scored-exposure'}


def forecast_report(rows, events, *, thresholds=None, policy_identity=None,
                    truth=None, provenance):
    """Aggregate explicit run/service truth and independently eligible events.

    Every row/event requires group_id. truth is optional, containing unique
    run_id/service/group_id records with outcome breach/no-breach/UNKNOWN,
    evidence_id, and optional healthy_hours. Healthy exposure is accepted only
    for a wholly verified no-breach run/service timeline (not partial intervals).
    score_schedule declares start/stop (exclusive), cadence_seconds=60 and
    either cutoff_delay_seconds or exact recorded cutoffs. Rates require full
    score coverage on these clocks.
    provenance supplies code/config/data/model identities. These identities and
    truth are caller attestations, not evidence verified by this pure function.
    """
    if not isinstance(provenance, dict) or any(not provenance.get(k) for k in ('code', 'config', 'data', 'models')):
        raise ValueError('code/config/data/models provenance required')
    groups, runs = {}, {}
    for item in [*rows, *events, *(truth or [])]:
        key = (item['run_id'], item['service'])
        group = item['group_id']
        if not group or key in groups and groups[key] != group:
            raise ValueError('inconsistent run/service group')
        groups[key] = group
    for item in truth or []:
        key = (item['run_id'], item['service'])
        if key in runs or item['outcome'] not in ('breach', 'no-breach', 'UNKNOWN') or not item.get('evidence_id'):
            raise ValueError('duplicate or invalid run truth')
        hours = item.get('healthy_hours')
        if hours is not None and (type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0
                                  or item['outcome'] != 'no-breach'):
            raise ValueError('healthy exposure requires verified no-breach truth and finite nonnegative hours')
        runs[key] = item
    for event in events:
        record = runs.get((event['run_id'], event['service']))
        if record and record['outcome'] != 'breach':
            raise ValueError('eligible event conflicts with run truth')
    summary = summarize_forecasts(rows, events, thresholds=thresholds, policy_identity=policy_identity)
    per_run = {}
    for key in sorted(groups):
        per_run[key] = summarize_forecasts(
            [r for r in rows if (r['run_id'], r['service']) == key],
            [e for e in events if (e['run_id'], e['service']) == key],
            thresholds=thresholds, policy_identity=policy_identity)
    output = {}
    for model in MODELS:
        metrics = dict(summary['models'][model])
        episodes = metrics['episodes']
        ready = episodes['status'] == 'ready'
        matches = episodes.get('matches', [])
        leads = [m['lead_seconds'] for m in matches if m['lead_seconds'] is not None]
        count = len(events)
        metrics['lead'] = {'events': count, 'ge60': sum(x >= 60 for x in leads) if ready else None,
                           'ge180': sum(x >= 180 for x in leads) if ready else None,
                           'matched': len(leads) if ready else None,
                           'late': sum(x < 60 for x in leads) if ready else None,
                           'miss': count - len(leads) if ready else None,
                           'p10': _quantile(leads, .1), 'p50': _quantile(leads, .5),
                           'quantile_rule': 'linear interpolation at (n-1)*p',
                           'missing_reason': None if ready and count else 'no-eligible-events' if ready else episodes['missing_reason']}
        for seconds in (60, 180):
            metrics['lead']['recall_ge' + str(seconds)] = sum(x >= seconds for x in leads) / count if ready and count else None
        certified = [key for key, record in runs.items() if record['outcome'] == 'no-breach']
        coverage = {key: _coverage(runs[key], [r for r in rows if r['model'] == model and
                    (r['run_id'], r['service']) == key]) for key in certified}
        available = ready and bool(certified) and all(value['complete'] for value in coverage.values())
        alerts = [per_run[key]['models'][model]['episodes'].get('alerts', 0) for key in certified]
        exposures = [key for key in certified if runs[key].get('healthy_hours') is not None]
        hours = sum(runs[key]['healthy_hours'] for key in exposures)
        metrics['no_breach'] = {'runs': len(certified), 'warned_runs': sum(x > 0 for x in alerts) if available and certified else None,
                                'warnings': sum(alerts) if available and certified else None,
                                'score_available_windows': sum(r['score'] is not None for r in rows if r['model'] == model and (r['run_id'], r['service']) in certified),
                                'score_unavailable_windows': sum(r['score'] is None for r in rows if r['model'] == model and (r['run_id'], r['service']) in certified),
                                'runs_without_rows': sum(not any((r['run_id'], r['service']) == key for r in rows) for key in certified),
                                'coverage': [dict(run_id=key[0], service=key[1], **coverage[key]) for key in sorted(coverage)],
                                'missing_reason': None if available else 'no-certified-no-breach-runs' if not certified else 'episodes-not-ready' if not ready else 'incomplete-scored-exposure'}
        exposure_available = ready and bool(exposures) and all(coverage[key]['complete'] for key in exposures)
        warnings = sum(per_run[key]['models'][model]['episodes'].get('alerts', 0) for key in exposures)
        metrics['healthy_exposure'] = {'runs': len(exposures), 'service_hours': hours if exposures else None,
                                      'scored_service_hours': sum(coverage[key]['scored_service_hours'] for key in exposures),
                                      'coverage_complete': exposure_available,
                                      'false_warnings': warnings if exposure_available else None,
                                      'per_service_hour': warnings / hours if exposure_available and hours else None,
                                      'missing_reason': None if exposure_available and hours else 'no-positive-certified-healthy-exposure' if not hours else 'episodes-not-ready' if not ready else 'incomplete-scored-exposure'}
        output[model] = metrics
    declared_groups = sorted(set(groups.values()))
    pairs = [(m, 'xgboost') for m in MODELS if m != 'xgboost'] + [('ltfp_graph', 'ltfp_self_only')]
    intervals = {}
    rng = random.Random(42)
    draws = [[rng.choice(declared_groups) for _ in declared_groups] for _ in range(1000)] if declared_groups else []
    by_event = {e['event_id']: e['group_id'] for e in events}
    for left, right in pairs:
        ready = all(output[m]['episodes']['status'] == 'ready' for m in (left, right))
        samples = []
        if ready and len(declared_groups) >= 5:
            counts = {g: sum(e['group_id'] == g for e in events) for g in declared_groups}
            successes = {m: {g: sum(match['status'] == 'matched' and by_event[match['event_id']] == g
                                   for match in output[m]['episodes']['matches']) for g in declared_groups} for m in (left, right)}
            samples = [(sum(successes[left][g] - successes[right][g] for g in draw) / sum(counts[g] for g in draw))
                       for draw in draws if sum(counts[g] for g in draw)]
        intervals[left + '_minus_' + right] = {'lower': _quantile(samples, .025), 'upper': _quantile(samples, .975),
            'valid_replicates': len(samples), 'undefined_replicates': 1000 - len(samples),
            'missing_reason': None if samples else 'episodes-not-ready' if not ready else 'at-least-five-declared-groups-required' if len(declared_groups) < 5 else 'no-event-bearing-replicates'}
    return {'summary': summary, 'models': output, 'provenance': dict(provenance),
            'bootstrap': {'groups': len(declared_groups), 'group_ids': declared_groups,
                          'event_groups': len({e['group_id'] for e in events}), 'replicates': 1000, 'seed': 42, 'intervals': intervals,
                          'method': 'paired-whole-group-percentile-linear'},
            'limitations': ['Caller certifies truth, identities and cutoff clocks.',
                            'Healthy exposure supports whole verified no-breach run/service timelines only.',
                            'Five declared groups is a computational guard, not a scientific threshold or evidence of IID support.']}
