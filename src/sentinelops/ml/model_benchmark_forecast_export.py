"""Build deterministic Lane A prediction, event and match ledgers."""
from sentinelops.contracts import utc_time
from sentinelops.ml.model_benchmark_forecast import MODELS, forecast_state
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.workers.risk import EpisodeTracker


def forecast_tables(scored, summary):
    """Rebuild alert identities and reconcile every event and unmatched alert."""
    if (summary.get('scope') != scored.get('scope') or summary.get('cases') != len(scored['cases'])
            or set(summary.get('reports', {})) != {'gradual', 'healthy', 'abrupt'}
            or summary.get('activation_allowed') is not False):
        raise ValueError('forecast summary differs from scored cases')
    kinds = {'healthy': 'healthy', 'payment-abrupt': 'abrupt', 'inventory-abrupt': 'abrupt',
             'payment-gradual-high': 'gradual', 'inventory-gradual-high': 'gradual',
             'payment-gradual-low': 'gradual', 'inventory-gradual-low': 'gradual'}
    cases = {case['run_id']: case for case in scored['cases']}
    if len(cases) != len(scored['cases']):
        raise ValueError('forecast export rejects duplicate runs')
    ordered = sorted((row for case in scored['cases'] for row in case['rows']),
                     key=lambda row: (row['run_id'], row['service'], row['model'], utc_time(row['window_end'])))
    tracker, predictions, alerts = EpisodeTracker(), [], {}
    for row in ordered:
        case = cases[row['run_id']]
        if row['group_id'] != case['group_id'] or row['model'] not in MODELS or case['kind'] not in kinds:
            raise ValueError('forecast prediction differs from its sealed case')
        model, service, run = row['model'], row['service'], row['run_id']
        state = forecast_state(row['score'], scored['thresholds']['thresholds'][model])
        episode = tracker.update((model, run, service), utc_time(row['window_end']), state)
        episode_id = (stable_id({'run_id': run, 'service': service, 'model_id': model,
                                 'started_at': episode['started_at']}) if episode['active'] else None)
        alert_id = None
        if episode['event'] == 'started':
            if row['prediction_cutoff'] is None:
                raise ValueError('forecast alert lacks a decision clock')
            decision = utc_time(row['prediction_cutoff'])
            key = (model, run, service, decision)
            if key in alerts:
                raise ValueError('duplicate forecast alert identity')
            alert_id = stable_id({'run_id': run, 'service': service, 'model_id': model,
                                  'decision_at': timestamp(decision)})
            alerts[key] = {'alert_id': alert_id, 'group_id': case['group_id'], 'run_id': run,
                           'service': service, 'model_id': model, 'decision_at': timestamp(decision),
                           'subset': kinds[case['kind']]}
        graph = row.get('graph_provenance') or {}
        predictions.append({'dataset': 'local-latency-holdout', 'group_id': case['group_id'], 'run_id': run,
            'service': service, 'bucket_end': row['window_end'], 'model_id': model,
            'available_at': row.get('source_available_at'), 'decision_at': row['prediction_cutoff'],
            'clock': scored['clock'], 'eligible': row['risk_label'] is not None,
            'reason': row['risk_missing_reason'] or row.get('score_missing_reason'), 'y': row['risk_label'],
            'score': row['score'], 'state': state, 'episode_id': episode_id, 'alert_id': alert_id,
            'graph_neighbor_count': graph.get('neighbor_count'), 'feature_ref': row.get('feature_ref')})
    events = []
    for case in scored['cases']:
        for event in case['events']:
            if event['group_id'] != case['group_id'] or event['run_id'] != case['run_id']:
                raise ValueError('forecast event differs from its sealed case')
            events.append({**event, 'dataset': 'local-latency-holdout', 'subset': kinds[case['kind']]})
    event_index = {event['event_id']: event for event in events}
    if len(event_index) != len(events):
        raise ValueError('duplicate forecast event identity')
    matches, used = [], set()
    for subset, report in summary['reports'].items():
        for model in MODELS:
            episodes = report['models'][model]['episodes']
            if episodes['status'] != 'ready':
                raise ValueError('forecast ledger requires verified frozen thresholds')
            expected_events = {event['event_id'] for event in events if event['subset'] == subset}
            if {match['event_id'] for match in episodes['matches']} != expected_events or len(episodes['matches']) != len(expected_events):
                raise ValueError('forecast matches do not cover the subset events')
            subset_alerts = {key for key, alert in alerts.items() if alert['subset'] == subset and key[0] == model}
            if episodes['alerts'] != len(subset_alerts):
                raise ValueError('forecast alert count differs from its report')
            before = len(used)
            for match in episodes['matches']:
                event = event_index[match['event_id']]
                if event['subset'] != subset:
                    raise ValueError('forecast match crosses a report subset')
                decision = utc_time(match['decision_at']) if match['decision_at'] is not None else None
                key = (model, event['run_id'], event['service'], decision)
                alert = alerts.get(key) if decision is not None else None
                if decision is not None and (alert is None or key in used):
                    raise ValueError('forecast match lacks a unique emitted alert')
                lead = (utc_time(event['onset_at']) - decision).total_seconds() if decision else None
                status = 'miss' if lead is None else 'matched' if lead >= 60 else 'late'
                if (match['lead_seconds'] != lead or match['status'] != status
                        or (lead is not None and not 0 < lead <= 300)):
                    raise ValueError('forecast match timing differs from its oracle event')
                if alert is not None:
                    used.add(key)
                matches.append({'dataset': 'local-latency-holdout', 'subset': subset, 'model_id': model,
                    'group_id': event['group_id'], 'run_id': event['run_id'], 'service': event['service'],
                    'event_id': event['event_id'], 'alert_id': alert['alert_id'] if alert else None,
                    'decision_at': timestamp(decision) if decision else None,
                    'lead_seconds': match['lead_seconds'], 'status': match['status']})
            if episodes['unmatched_alerts'] != len(subset_alerts) - (len(used) - before):
                raise ValueError('forecast unmatched-alert count differs from its report')
    for key, alert in alerts.items():
        if key not in used:
            matches.append({'dataset': 'local-latency-holdout', **alert, 'event_id': None,
                            'lead_seconds': None, 'status': 'unmatched'})
    predictions.sort(key=lambda row: (row['group_id'], row['run_id'], row['service'], row['bucket_end'], row['model_id']))
    events.sort(key=lambda row: (row['group_id'], row['run_id'], row['service'], row['onset_at'], row['event_id']))
    matches.sort(key=lambda row: (row['group_id'], row['run_id'], row['service'], row['model_id'],
                                  row['event_id'] or '', row['decision_at'] or ''))
    return {'predictions': predictions, 'events': events, 'matches': matches}
