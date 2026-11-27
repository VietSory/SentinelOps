"""Pure forecast reports over caller-certified truth; no fitting or calibration."""
import math
import random
from sentinelops.contracts import utc_time

from sentinelops.ml.model_benchmark_forecast import MODELS, project_latency_truth, schedule_cutoffs, summarize_forecasts
from sentinelops.telemetry.core import BUSINESS_ROUTES


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


def summarize_sealed_forecasts(scored):
    """Keep gradual, healthy and abrupt holdout denominators separate."""
    if (scored.get('scope') != 'sealed-local-latency-holdout' or scored.get('activation_allowed') is not False
            or not all(scored.get(key) for key in ('scorer_commit', 'capture_commit', 'candidates_sha256', 'seal_sha256'))):
        raise ValueError('sealed forecast provenance is incomplete')
    kinds = {'healthy': 'healthy', 'payment-abrupt': 'abrupt', 'inventory-abrupt': 'abrupt',
             'payment-gradual-high': 'gradual', 'inventory-gradual-high': 'gradual',
             'payment-gradual-low': 'gradual', 'inventory-gradual-low': 'gradual'}
    subsets = {name: {'rows': [], 'events': [], 'truth': []} for name in ('gradual', 'healthy', 'abrupt')}
    run_ids, policies = set(), set()
    for case in scored['cases']:
        run_id, group_id, kind = case['run_id'], case['group_id'], case['kind']
        if not run_id or not group_id or run_id in run_ids or kind not in kinds or not case.get('policy_sha256'):
            raise ValueError('duplicate or invalid sealed forecast case')
        run_ids.add(run_id)
        policies.add(case['policy_sha256'])
        subset = subsets[kinds[kind]]
        for key in ('rows', 'events', 'truth'):
            for item in case[key]:
                if item['run_id'] != run_id or item['group_id'] != group_id:
                    raise ValueError('forecast row belongs to another sealed case')
                value = dict(item)
                if key == 'truth' and kind != 'healthy':
                    value.pop('healthy_hours', None)
                subset[key].append(value)
    if len(policies) != 1:
        raise ValueError('sealed forecast cases must use one pinned health policy')
    provenance = {'code': scored['scorer_commit'], 'config': next(iter(policies)),
                  'data': scored['seal_sha256'], 'models': scored['candidates_sha256']}
    rules = scored['thresholds']
    reports = {name: forecast_report(items['rows'], items['events'], truth=items['truth'],
               thresholds=rules['thresholds'], policy_identity=rules['policy_identity'], provenance=provenance)
               for name, items in subsets.items()}
    gradual = reports['gradual']
    gradual_cases = [case for case in scored['cases'] if kinds[case['kind']] == 'gradual']
    no_breach, unknown = [], 0
    for case in gradual_cases:
        truth = {item['service']: item for item in case['truth']}
        if len(truth) != len(case['truth']):
            raise ValueError('duplicate service truth in sealed forecast case')
        if any(item['outcome'] == 'breach' for item in truth.values()):
            continue
        if set(truth) != set(BUSINESS_ROUTES) or any(item['outcome'] != 'no-breach' for item in truth.values()):
            unknown += 1
            continue
        no_breach.append(case['run_id'])
    gradual['run_no_breach'] = {}
    for model in MODELS:
        model_result = gradual['models'][model]
        coverage = {(item['run_id'], item['service']): item for item in model_result['no_breach']['coverage']}
        complete = bool(no_breach) and model_result['episodes']['status'] == 'ready' and all(
            coverage.get((run, service), {}).get('complete') and
            coverage.get((run, service), {}).get('warnings') is not None
            for run in no_breach for service in BUSINESS_ROUTES)
        warned = sum(any(coverage[run, service]['warnings'] > 0 for service in BUSINESS_ROUTES)
                     for run in no_breach) if complete else None
        gradual['run_no_breach'][model] = {'runs': len(no_breach), 'warned_runs': warned,
            'unknown_runs': unknown, 'missing_reason': None if complete else
                'no-verified-no-breach-runs' if not no_breach else 'incomplete-scored-exposure'}
    graph, slope = gradual['models']['ltfp_graph'], gradual['models']['slope']
    recall, average_precision = graph['lead']['recall_ge60'], graph['average_precision']
    prevalence, slope_precision = graph['prevalence'], slope['average_precision']
    available = (gradual['bootstrap']['groups'] >= 5 and graph['lead']['events'] > 0
                 and all(value is not None for value in (recall, average_precision, prevalence, slope_precision)))
    criteria = {'recall_ge60_at_least_070': recall >= .70 if recall is not None else None,
                'average_precision_above_prevalence': average_precision > prevalence
                    if average_precision is not None and prevalence is not None else None,
                'average_precision_above_slope': average_precision > slope_precision
                    if average_precision is not None and slope_precision is not None else None}
    decision = {'status': 'INCONCLUSIVE' if not available else 'PASS' if all(criteria.values()) else 'FAIL',
                'criteria': criteria, 'eligible_events': graph['lead']['events'],
                'groups': gradual['bootstrap']['groups'],
                'reason': None if available else 'insufficient-groups-events-or-labelled-common-windows'}
    return {'schema_version': 'benchmark-forecast-report/sealed-v1', 'scope': scored['scope'],
            'clock': scored['clock'], 'provenance': provenance, 'cases': len(run_ids),
            'reports': reports, 'q07': decision, 'activation_allowed': False}


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
        for key in certified:
            coverage[key]['warnings'] = (per_run[key]['models'][model]['episodes'].get('alerts', 0)
                                         if ready and coverage[key]['complete'] else None)
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
    intervals, paired_lead = {}, {}
    rng = random.Random(42)
    draws = [[rng.choice(declared_groups) for _ in declared_groups] for _ in range(1000)] if declared_groups else []
    by_event = {e['event_id']: e['group_id'] for e in events}
    for left, right in pairs:
        ready = all(output[m]['episodes']['status'] == 'ready' for m in (left, right))
        lead_maps = {model: {match['event_id']: match['lead_seconds']
                            for match in output[model]['episodes'].get('matches', [])} for model in (left, right)}
        common_events = sorted(event_id for event_id in by_event
                               if all(lead_maps[model].get(event_id) is not None for model in (left, right))) if ready else []
        lead_delta = [lead_maps[left][event_id] - lead_maps[right][event_id] for event_id in common_events]
        paired_lead[left + '_minus_' + right] = {'common_matched_events': len(common_events),
            'delta_p10_seconds': _quantile(lead_delta, .1), 'delta_p50_seconds': _quantile(lead_delta, .5),
            'left_misses': sum(lead_maps[left].get(event_id) is None for event_id in by_event) if ready else None,
            'right_misses': sum(lead_maps[right].get(event_id) is None for event_id in by_event) if ready else None,
            'missing_reason': None if common_events else 'no-common-matched-events' if ready else 'episodes-not-ready'}
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
            'paired_lead': paired_lead,
            'limitations': ['Caller certifies truth, identities and cutoff clocks.',
                            'Healthy exposure supports whole verified no-breach run/service timelines only.',
                            'Five declared groups is a computational guard, not a scientific threshold or evidence of IID support.']}
