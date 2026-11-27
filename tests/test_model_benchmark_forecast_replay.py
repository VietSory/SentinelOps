import asyncio
from datetime import datetime, timezone

import pytest

from scripts import check_ltfp_graph_parity
from sentinelops.ml import model_benchmark_forecast_replay as forecast_replay
from sentinelops.ml.model_benchmark_forecast_replay import replay_batches


def test_worker_replay_keeps_gaps_and_never_uses_partial_capture(tmp_path, monkeypatch):
    calls = []
    class Replay:
        def __init__(self, root, captures, *, environment, policy):
            self.store, self.captures = root, captures
        async def tick(self, bucket, *, now):
            calls.append((bucket, now, self.captures[bucket]))
            return {'status': 'COMMITTED', 'manifest_ref': 'artifact://manifests/' + 'a' * 64 + '.json'}
    monkeypatch.setattr(check_ltfp_graph_parity, 'WorkerReplay', Replay)
    rows = [
        {'bucket_end': '2026-01-01T00:01:00Z', 'cutoff': None, 'missing_reason': 'incomplete-source-bucket',
         'captures': [('partial', {})]},
        {'bucket_end': '2026-01-01T00:02:00Z', 'cutoff': '2026-01-01T00:02:30Z',
         'missing_reason': None, 'captures': [('complete', {})]},
        {'bucket_end': '2026-01-01T00:03:00Z', 'cutoff': None,
         'missing_reason': 'source-clock-outside-anchor-minute', 'captures': [('late', {})]},
        {'bucket_end': '2026-01-01T00:04:00Z', 'cutoff': '2026-01-01T00:04:32Z',
         'missing_reason': None, 'captures': [('complete-2', {})]},
    ]
    store, batches = asyncio.run(replay_batches(rows, output_root=tmp_path, policy={'maximum_lag_seconds': 60}))
    assert store == tmp_path and batches[0]['batch_ref'] is None
    assert batches[2]['prediction_cutoff'] is None and batches[2]['missing_reason'] == 'source-clock-outside-anchor-minute'
    assert calls[1] == ('2026-01-01T00:03:00Z', datetime(2026, 1, 1, 0, 4, 1, tzinfo=timezone.utc), [])
    assert calls[0][2] == [('complete', {})] and calls[2][2] == [('complete-2', {})]
    with pytest.raises(ValueError, match='duplicate minutes'):
        asyncio.run(replay_batches(rows + rows[-1:], output_root=tmp_path, policy={'maximum_lag_seconds': 60}))


def test_replay_uses_real_health_worker_for_gap(tmp_path):
    from datetime import timedelta
    from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
    from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
    from test_health_source import POLICY, START, capture

    rows = []
    for minute in (1, 2, 3):
        end = START + timedelta(minutes=minute)
        captures = [] if minute == 2 else [capture(service, end, errors=0, profile=SOURCE_PROFILE_V2)
                                           for service in BUSINESS_ROUTES]
        rows.append({'bucket_end': timestamp(end), 'cutoff': timestamp(end + timedelta(seconds=30)) if captures else None,
                     'missing_reason': None if captures else 'incomplete-source-bucket',
                     'captures': [('artifact://raw/' + stable_id(raw) + '.json', raw) for raw in captures]})
    async def bounded():
        return await asyncio.wait_for(replay_batches(rows, output_root=tmp_path, policy=POLICY), timeout=5)
    store, batches = asyncio.run(bounded())
    first, gap, last = [store.read(row['batch_ref']) for row in batches]
    assert first['source_status'] == last['source_status'] == 'queried'
    assert gap['source_status'] == 'missed-decision-deadline' and gap['current_raw_refs'] == []
    assert gap['previous_batch_ref'] == batches[0]['batch_ref']
    assert last['previous_batch_ref'] == batches[1]['batch_ref']


def test_scoring_keeps_every_service_and_missing_clock(tmp_path, monkeypatch):
    from sentinelops.ml.model_benchmark_forecast import MODELS, summarize_forecasts
    from sentinelops.telemetry.core import stable_id
    from sentinelops.telemetry.store import ArtifactStore

    store = ArtifactStore(tmp_path)
    end, cutoff = '2026-01-01T00:02:00Z', '2026-01-01T00:02:30Z'
    feature = store.write('features', {'service': 'payment-service', 'window_end': end, 'as_of': cutoff})
    normalization = store.write('manifests', {'feature_refs': [feature]})
    batch_ref = store.write('manifests', {'bucket_end': end, 'evaluated_at': cutoff,
                                          'normalization_manifest_ref': normalization})
    batches = [{'bucket_end': end, 'prediction_cutoff': cutoff, 'batch_ref': batch_ref, 'missing_reason': None},
               {'bucket_end': '2026-01-01T00:03:00Z', 'prediction_cutoff': None, 'batch_ref': None,
                'source_available_at': '2026-01-01T00:03:44Z', 'missing_reason': 'incomplete-source-bucket'}]
    calls = []
    def score(store, **kwargs):
        calls.append(kwargs['service'])
        return [{'group_id': kwargs['group_id'], 'run_id': kwargs['run_id'], 'service': kwargs['service'],
                 'window_end': kwargs['anchor'], 'prediction_cutoff': kwargs['as_of'], 'feature_ref': feature,
                 'oracle_sha256': stable_id(kwargs['oracle']), 'health_policy_sha256': stable_id(kwargs['health_policy']),
                 'risk_label': 0, 'risk_missing_reason': None, 'slo_onset_at': None,
                 'model': name, 'score': .5} for name in MODELS]
    monkeypatch.setattr(forecast_replay, 'score_batch', score)
    monkeypatch.setattr(forecast_replay, 'latency_forecast_labels', lambda frame, oracle, policy: {
        'risk_label': 0, 'risk_missing_reason': None, 'prediction_cutoff': frame['as_of'],
        'risk_forecast_anchor_at': frame['window_end'], 'slo_onset_at': None, 'future_confirmation_tail': None})
    case = {'run_id': 'run-one', 'group_id': 'group-one'}
    run = {'run_id': 'run-one', 'session_id': 'group-one', 'warmup_end_at': '2026-01-01T00:01:10Z',
           'ended_at': '2026-01-01T00:04:59Z'}
    graph = {'node_ids': [], 'topology': {}, 'graph_sha256': 'graph'}
    models = {'config': {}, 'bundle': tmp_path, 'manifest_sha256': 'manifest', 'models': {}, 'xgboost_refit': {}}
    result = forecast_replay.score_batches(store, batches, case=case, run=run, graph=graph, models=models,
                                            oracle={'oracle': 'fixture'}, policy={'policy': 'fixture'})
    assert calls == ['payment-service'] and len(result['rows']) == 24
    missing = [row for row in result['rows'] if row['score'] is None]
    assert len(missing) == 20
    assert sum(row['prediction_cutoff'] is None for row in missing) == 12
    assert all(row['source_available_at'] == '2026-01-01T00:03:44Z'
               for row in missing if row['prediction_cutoff'] is None)
    assert result['score_schedule']['cutoffs']['2026-01-01T00:03:00Z'] is None
    summary = summarize_forecasts(result['rows'], [])
    assert (summary['windows'], summary['unknown_windows'], summary['common_windows']) == (6, 3, 1)
    assert all(summary['models'][name]['unavailable_windows'] == 5 for name in MODELS)
    with pytest.raises(ValueError, match='cutoffs do not cover'):
        forecast_replay.score_batches(store, batches[:1], case=case, run=run, graph=graph, models=models,
                                      oracle={'oracle': 'fixture'}, policy={'policy': 'fixture'})
