from copy import deepcopy

import pytest

from sentinelops.ml.model_benchmark_forecast import MODELS
from sentinelops.ml.model_benchmark_forecast_export import forecast_tables
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
    broken = deepcopy(summary)
    broken['reports']['gradual']['models']['slope']['episodes']['alerts'] += 1
    with pytest.raises(ValueError, match='alert count differs'):
        forecast_tables(scored, broken)
