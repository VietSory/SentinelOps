from copy import deepcopy

import pytest

from sentinelops.ml.model_benchmark_forecast import MODELS
from sentinelops.ml.model_benchmark_forecast_export import forecast_tables
from sentinelops.ml.model_benchmark_forecast_export import write_forecast_artifacts
from sentinelops.ml.model_benchmark_reports import summarize_sealed_forecasts
from test_model_benchmark_reports import THRESHOLDS, fixture


def test_forecast_ledgers_match_events_and_keep_unmatched_alerts():
    rows, events = fixture((60,))
    schedule = {'start': '2026-01-01T00:05:00Z', 'stop': '2026-01-01T00:07:00Z',
                'cadence_seconds': 60, 'cutoff_delay_seconds': 30}
    breach = {'run_id': '0', 'service': 's', 'group_id': '0', 'outcome': 'breach',
              'evidence_id': 'oracle', 'score_schedule': schedule}
    def no_breach(run, group, kind):
        copied = [{**row, 'run_id': run, 'group_id': group, 'risk_label': 0,
                   'source_available_at': row['prediction_cutoff']} for row in rows]
        truth = {'run_id': run, 'service': 's', 'group_id': group, 'outcome': 'no-breach',
                 'evidence_id': 'oracle', 'healthy_hours': 2 / 60, 'score_schedule': schedule}
        return {'run_id': run, 'group_id': group, 'kind': kind, 'policy_sha256': 'policy',
                'rows': copied, 'events': [], 'truth': [truth]}
    cases = [{'run_id': '0', 'group_id': '0', 'kind': 'payment-gradual-high', 'policy_sha256': 'policy',
              'rows': rows, 'events': events, 'truth': [breach]},
             no_breach('low', 'g1', 'payment-gradual-low'), no_breach('healthy', 'g2', 'healthy')]
    scored = {'scope': 'sealed-local-latency-holdout', 'clock': 'recorded-source-availability-replay',
              'scorer_commit': 'code', 'capture_commit': 'capture', 'candidates_sha256': 'models',
              'seal_sha256': 'seal', 'activation_allowed': False, 'cases': cases,
              'thresholds': {'thresholds': THRESHOLDS, 'policy_identity': 'frozen'}}
    summary = summarize_sealed_forecasts(scored)
    tables = forecast_tables(scored, summary)
    assert len(tables['predictions']) == 24 and len(tables['events']) == 1
    assert sum(row['status'] == 'matched' for row in tables['matches']) == len(MODELS)
    assert sum(row['status'] == 'unmatched' for row in tables['matches']) == 2 * len(MODELS)
    assert all(row['lead_seconds'] == 60 for row in tables['matches'] if row['status'] == 'matched')
    moved = deepcopy(scored)
    for case in moved['cases']:
        for row in case['rows']:
            row['source_feature_ref'] = 'stable-source-feature'
    stable = forecast_tables(moved, summarize_sealed_forecasts(moved))
    for case in moved['cases']:
        for row in case['rows']:
            row['feature_ref'] = 'different-batch-derived-feature'
    assert forecast_tables(moved, summarize_sealed_forecasts(moved)) == stable
    broken = deepcopy(summary)
    broken['reports']['gradual']['models']['slope']['episodes']['alerts'] += 1
    with pytest.raises(ValueError, match='alert count differs'):
        forecast_tables(scored, broken)


def test_forecast_artifacts_are_complete_utc_and_immutable(tmp_path):
    import csv
    import json
    import pyarrow.parquet as pq

    base_rows, base_events = fixture((60,))
    kinds = ('healthy', 'payment-abrupt', 'inventory-abrupt', 'payment-gradual-high',
             'inventory-gradual-high', 'payment-gradual-low', 'inventory-gradual-low')
    schedule = {'start': '2026-01-01T00:05:00Z', 'stop': '2026-01-01T00:07:00Z',
                'cadence_seconds': 60, 'cutoff_delay_seconds': 30}
    cases = []
    for ordinal in range(1, 6):
        group = f'bench-final-{ordinal:02d}'
        for kind in kinds:
            run = group + '-' + kind
            breach = kind.endswith('-high')
            rows = [{**row, 'run_id': run, 'group_id': group, 'risk_label': int(breach),
                     'source_available_at': row['prediction_cutoff'],
                     'graph_provenance': {'neighbor_count': int(row['model'] == 'ltfp_graph')}
                        if row['model'].startswith('ltfp_') else None} for row in base_rows]
            truth = {'run_id': run, 'service': 's', 'group_id': group,
                     'outcome': 'breach' if breach else 'no-breach', 'evidence_id': 'oracle',
                     'score_schedule': schedule}
            if not breach:
                truth['healthy_hours'] = 2 / 60
            events = [{**base_events[0], 'event_id': run, 'run_id': run, 'group_id': group,
                       'confirmed_at': '2026-01-01T00:08:00Z', 'kind': 'latency-slo',
                       'oracle_ref': 'oracle', 'eligible': True, 'reason': None}] if breach else []
            cases.append({'run_id': run, 'group_id': group, 'slot_id': run, 'kind': kind,
                          'policy_sha256': 'policy', 'rows': rows, 'events': events, 'truth': [truth]})
    scored = {'scope': 'sealed-local-latency-holdout', 'clock': 'recorded-source-availability-replay',
              'scorer_commit': 'code', 'capture_commit': 'capture', 'candidates_sha256': 'models',
              'registration_sha256': 'registration', 'seal_sha256': 'seal', 'activation_allowed': False,
              'cases': cases, 'thresholds': {'thresholds': THRESHOLDS, 'policy_identity': 'frozen'}}
    manifest = write_forecast_artifacts(tmp_path, scored)
    assert manifest['runs'] == 35 and len(manifest['groups']) == 5
    assert manifest['row_counts']['predictions'] == 280 and manifest['q07_status'] == 'FAIL'
    predictions = pq.read_table(tmp_path / 'score/a/predictions.parquet')
    assert predictions.num_rows == 280 and str(predictions.schema.field('bucket_end').type) == 'timestamp[us, tz=UTC]'
    with (tmp_path / 'report/forecast.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert {row['model_id'] for row in rows} == set(MODELS)
    graph_row = next(row for row in rows if row['model_id'] == 'ltfp_graph')
    assert float(graph_row['delta_recall_vs_xgboost']) == 0
    assert float(graph_row['delta_recall_graph_vs_self']) == 0
    assert json.loads((tmp_path / 'report/forecast.json').read_bytes())['q07']['status'] == 'FAIL'
    assert write_forecast_artifacts(tmp_path, scored) == manifest
    assert write_forecast_artifacts(tmp_path, scored, verify_only=True) == manifest
    improved = deepcopy(scored)
    for case in improved['cases']:
        for row in case['rows']:
            if row['model'] == 'ltfp_graph':
                row['score'] = .9 if case['kind'].endswith('-high') else .1
    assert summarize_sealed_forecasts(improved)['q07']['status'] == 'PASS'
    for case in improved['cases']:
        case['events'] = []
    assert summarize_sealed_forecasts(improved)['q07']['status'] == 'INCONCLUSIVE'
    (tmp_path / 'report/forecast.csv').write_text('changed')
    with pytest.raises(ValueError, match='forecast verification artifact differs'):
        write_forecast_artifacts(tmp_path, scored, verify_only=True)
    with pytest.raises(ValueError, match='different forecast artifact'):
        write_forecast_artifacts(tmp_path, scored)
    missing = tmp_path / 'report/forecast.csv'
    missing.unlink()
    with pytest.raises(ValueError, match='forecast verification artifact missing'):
        write_forecast_artifacts(tmp_path, scored, verify_only=True)
    assert not missing.exists()
