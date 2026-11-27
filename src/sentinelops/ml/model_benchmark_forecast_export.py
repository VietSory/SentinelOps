"""Build deterministic Lane A prediction, event and match ledgers."""
from collections import Counter
import csv
import hashlib
import io
from pathlib import Path
import platform

from sentinelops.contracts import utc_time
from sentinelops.ml.model_benchmark_forecast import MODELS, forecast_state
from sentinelops.ml.model_benchmark_protocol import _bytes, publish_freeze
from sentinelops.ml.model_benchmark_reports import summarize_sealed_forecasts
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


def write_forecast_artifacts(root, scored):
    """Publish immutable local-only Lane A tables after a complete sealed replay."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = Path(root).resolve()
    expected_kinds = {'healthy', 'payment-abrupt', 'inventory-abrupt', 'payment-gradual-high',
                      'inventory-gradual-high', 'payment-gradual-low', 'inventory-gradual-low'}
    groups = {}
    for case in scored['cases']:
        groups.setdefault(case['group_id'], []).append(case['kind'])
    if (len(scored['cases']) != 35 or len(groups) != 5
            or any(len(kinds) != 7 or set(kinds) != expected_kinds for kinds in groups.values())):
        raise ValueError('forecast export requires all 35 registered slots across five groups')
    summary = summarize_sealed_forecasts(scored)
    tables = forecast_tables(scored, summary)
    time = pa.timestamp('us', tz='UTC')
    string = pa.string()
    schemas = {
        'predictions': pa.schema([(name, kind) for name, kind in (
            ('dataset', string), ('group_id', string), ('run_id', string), ('service', string),
            ('bucket_end', time), ('model_id', string), ('available_at', time), ('decision_at', time),
            ('clock', string), ('eligible', pa.bool_()), ('reason', string), ('y', pa.int8()),
            ('score', pa.float64()), ('state', string), ('episode_id', string), ('alert_id', string),
            ('graph_neighbor_count', pa.int16()), ('feature_ref', string))]),
        'events': pa.schema([(name, kind) for name, kind in (
            ('dataset', string), ('subset', string), ('event_id', string), ('group_id', string), ('run_id', string),
            ('service', string), ('onset_at', time), ('confirmed_at', time), ('kind', string),
            ('oracle_ref', string), ('eligible', pa.bool_()), ('reason', string))]),
        'matches': pa.schema([(name, kind) for name, kind in (
            ('dataset', string), ('subset', string), ('model_id', string), ('group_id', string), ('run_id', string),
            ('service', string), ('event_id', string), ('alert_id', string), ('decision_at', time),
            ('lead_seconds', pa.float64()), ('status', string))]),
    }
    payloads = {}
    for name, schema in schemas.items():
        converted = []
        for row in tables[name]:
            converted.append({field.name: (utc_time(row[field.name]) if pa.types.is_timestamp(field.type)
                              and row.get(field.name) is not None else row.get(field.name)) for field in schema})
        buffer = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(converted, schema=schema), buffer, compression='zstd')
        payloads[f'score/a/{name}.parquet'] = buffer.getvalue()
    gradual, healthy = summary['reports']['gradual'], summary['reports']['healthy']
    fieldnames = ('model_id', 'common_windows', 'unavailable_windows', 'average_precision', 'pr_auc_trapezoidal', 'prevalence',
                  'ranking_missing_reason',
                  'eligible_events', 'recall_ge60', 'recall_ge180', 'lead_p10_seconds', 'lead_p50_seconds',
                  'late', 'miss', 'lead_missing_reason', 'no_breach_runs', 'no_breach_warned_runs',
                  'healthy_service_hours', 'false_warnings', 'false_warnings_per_hour', 'exposure_missing_reason',
                  'delta_recall_vs_xgboost_ci_lower', 'delta_recall_vs_xgboost_ci_upper',
                  'delta_recall_graph_vs_self_ci_lower', 'delta_recall_graph_vs_self_ci_upper',
                  'paired_vs_xgboost_events', 'paired_vs_xgboost_lead_p50_seconds',
                  'paired_graph_vs_self_events', 'paired_graph_vs_self_lead_p50_seconds',
                  'graph_connected_windows', 'graph_isolated_windows', 'graph_unavailable_windows')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator='\n')
    writer.writeheader()
    gradual_runs = {case['run_id'] for case in scored['cases'] if '-gradual-' in case['kind']}
    for model in MODELS:
        metrics = gradual['models'][model]
        lead, no_breach = metrics['lead'], metrics['no_breach']
        exposure = healthy['models'][model]['healthy_exposure']
        intervals = gradual['bootstrap']['intervals']
        versus_xgb = intervals.get(model + '_minus_xgboost', {})
        versus_self = intervals.get('ltfp_graph_minus_ltfp_self_only', {}) if model == 'ltfp_graph' else {}
        paired = gradual['paired_lead']
        paired_xgb = paired.get(model + '_minus_xgboost', {})
        paired_self = paired.get('ltfp_graph_minus_ltfp_self_only', {}) if model == 'ltfp_graph' else {}
        model_rows = [row for row in tables['predictions'] if row['model_id'] == model and row['run_id'] in gradual_runs]
        graph_rows = [row for row in model_rows if row['graph_neighbor_count'] is not None]
        writer.writerow({'model_id': model, 'common_windows': gradual['summary']['common_windows'],
            'unavailable_windows': metrics['unavailable_windows'],
            'average_precision': metrics['average_precision'], 'pr_auc_trapezoidal': metrics['pr_auc_trapezoidal'],
            'prevalence': metrics['prevalence'], 'ranking_missing_reason': metrics['missing_reason'],
            'eligible_events': lead['events'],
            'recall_ge60': lead['recall_ge60'], 'recall_ge180': lead['recall_ge180'],
            'lead_p10_seconds': lead['p10'], 'lead_p50_seconds': lead['p50'], 'late': lead['late'], 'miss': lead['miss'],
            'lead_missing_reason': lead['missing_reason'],
            'no_breach_runs': no_breach['runs'], 'no_breach_warned_runs': no_breach['warned_runs'],
            'healthy_service_hours': exposure['service_hours'], 'false_warnings': exposure['false_warnings'],
            'false_warnings_per_hour': exposure['per_service_hour'], 'exposure_missing_reason': exposure['missing_reason'],
            'delta_recall_vs_xgboost_ci_lower': versus_xgb.get('lower'),
            'delta_recall_vs_xgboost_ci_upper': versus_xgb.get('upper'),
            'delta_recall_graph_vs_self_ci_lower': versus_self.get('lower'),
            'delta_recall_graph_vs_self_ci_upper': versus_self.get('upper'),
            'paired_vs_xgboost_events': paired_xgb.get('common_matched_events'),
            'paired_vs_xgboost_lead_p50_seconds': paired_xgb.get('delta_p50_seconds'),
            'paired_graph_vs_self_events': paired_self.get('common_matched_events'),
            'paired_graph_vs_self_lead_p50_seconds': paired_self.get('delta_p50_seconds'),
            'graph_connected_windows': sum(row['graph_neighbor_count'] > 0 for row in graph_rows) if model.startswith('ltfp_') else None,
            'graph_isolated_windows': sum(row['graph_neighbor_count'] == 0 for row in graph_rows) if model.startswith('ltfp_') else None,
            'graph_unavailable_windows': len(model_rows) - len(graph_rows) if model.startswith('ltfp_') else None})
    payloads['report/forecast.csv'] = stream.getvalue().encode()
    payloads['report/forecast.json'] = _bytes(summary)
    counts = {'predictions': len(tables['predictions']), 'events': len(tables['events']),
              'matches': len(tables['matches']), 'forecast_rows': len(MODELS)}
    artifacts = []
    for relative, data in sorted(payloads.items()):
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError('forecast artifact escapes its benchmark root')
        artifacts.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    manifest = {'schema_version': 'benchmark-forecast-artifacts/sealed-v1', 'dataset': 'local-latency-holdout',
        'clock': scored['clock'], 'scorer_commit': scored['scorer_commit'], 'capture_commit': scored['capture_commit'],
        'candidates_sha256': scored['candidates_sha256'], 'registration_sha256': scored['registration_sha256'],
        'seal_sha256': scored['seal_sha256'], 'groups': sorted(groups), 'runs': len(scored['cases']),
        'kind_counts': dict(sorted(Counter(case['kind'] for case in scored['cases']).items())),
        'row_counts': counts, 'artifacts': artifacts, 'q07_status': summary['q07']['status'],
        'python_version': platform.python_version(),
        'pyarrow_version': pa.__version__, 'activation_allowed': False}
    for relative, data in sorted(payloads.items()):
        publish_freeze(root / relative, data, conflict='different forecast artifact already exists')
    publish_freeze(root / 'report/forecast-manifest.json', _bytes(manifest),
                   conflict='different forecast manifest already exists')
    return manifest
