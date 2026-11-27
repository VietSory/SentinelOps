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
           'ended_at': '2026-01-01T00:05:59Z'}
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


def test_schedule_leaves_the_complete_oracle_tail_for_no_breach(tmp_path):
    from datetime import timedelta
    from sentinelops.ml.model_benchmark_forecast import project_latency_truth
    from sentinelops.telemetry.core import timestamp
    from sentinelops.telemetry.store import ArtifactStore
    from test_health_source import POLICY
    from test_local_labels import START, derive, fixture_oracle

    store, oracle = ArtifactStore(tmp_path), derive(fixture_oracle())
    source_ref = store.write('manifests', {'status': 'completed'})
    oracle_ref = store.write('manifests', {'certificate_version': 'local-http-oracle/v1',
                                           'source_result_ref': source_ref, 'oracle': oracle})
    case = {'run_id': 'run-one', 'group_id': 'group-one', 'source_result_ref': source_ref, 'oracle_ref': oracle_ref}
    run = {'run_id': 'run-one', 'session_id': 'group-one', 'warmup_end_at': timestamp(START + timedelta(minutes=5)),
           'ended_at': timestamp(START + timedelta(minutes=14))}
    batches = [{'bucket_end': timestamp(START + timedelta(minutes=minute)), 'prediction_cutoff': None,
                'batch_ref': None, 'missing_reason': 'incomplete-source-bucket'} for minute in range(5, 13)]
    result = forecast_replay.score_batches(store, batches, case=case, run=run, graph={}, models={},
                                            oracle=oracle, policy=POLICY)
    truth, events = project_latency_truth(store, case, 'payment-service', result['score_schedule'])
    assert truth['outcome'] == 'no-breach' and truth['healthy_hours'] == 8 / 60 and not events


def test_sealed_scoring_rejects_unverified_input_before_case_replay(tmp_path, monkeypatch):
    import json
    from sentinelops.telemetry.core import stable_id

    monkeypatch.setattr(forecast_replay, 'clean_source', lambda project: 'a' * 40)
    calls = []
    async def score_case(root, case, **kwargs):
        calls.append(case['run_id'])
        return {'run_id': case['run_id']}
    monkeypatch.setattr(forecast_replay, '_score_verified_case', score_case)
    def reject(*args, **kwargs):
        raise ValueError('holdout seal missing')
    monkeypatch.setattr(forecast_replay, 'load_sealed_inputs', reject)
    options = {'candidates_sha256': 'b' * 64, 'registration_sha256': 'c' * 64,
               'seal_sha256': 'd' * 64, 'capture_project': tmp_path}
    with pytest.raises(ValueError, match='seal missing'):
        forecast_replay.score_sealed(tmp_path, **options)
    assert calls == []
    protocol = {'scoring': 'fixture'}
    (tmp_path / 'freeze').mkdir()
    (tmp_path / 'freeze/protocol.json').write_text(json.dumps({'protocol': protocol}))
    monkeypatch.setattr(forecast_replay, 'verify_candidate_freeze', lambda *args: {'protocol_sha256': stable_id(protocol)})
    monkeypatch.setattr(forecast_replay, 'load_sealed_inputs', lambda *args, **kwargs: {
        'capture_commit': 'e' * 40, 'models': {}, 'thresholds': {},
        'cases': [{'run_id': 'one'}, {'run_id': 'two'}]})
    result = forecast_replay.score_sealed(tmp_path, **options)
    assert calls == ['one', 'two'] and [case['run_id'] for case in result['cases']] == calls
    assert result['activation_allowed'] is False and result['scorer_commit'] == 'a' * 40


def test_verified_case_rejects_changed_run_before_replay(tmp_path, monkeypatch):
    import json
    from sentinelops.telemetry.core import stable_id
    from sentinelops.telemetry.store import ArtifactStore

    run_id, group_id, slot_id, commit = 'run-' + '1' * 32, 'bench-final-01', 'bench-final-01-healthy', 'a' * 40
    run_path = tmp_path / 'capture/groups' / group_id / 'slots' / slot_id / 'runs' / run_id / 'run.json'
    serving = ArtifactStore(run_path.parent / 'serving')
    raw_ref = serving.write('raw', {'sample': 'fixture'})
    manifest_ref = serving.write('manifests', {'raw_refs': [raw_ref]})
    run = {'run_id': run_id, 'session_id': group_id, 'source_commit': commit, 'source_manifest_ref': manifest_ref,
           'environment': 'local'}
    run_path.write_text(json.dumps(run))
    artifacts = ArtifactStore(tmp_path)
    result_ref = artifacts.write('manifests', {'status': 'completed', 'run_sha256': stable_id(run)})
    oracle_ref = artifacts.write('manifests', {'certificate_version': 'local-http-oracle/v1',
                                                 'source_result_ref': result_ref, 'oracle': {}})
    case = {'run_id': run_id, 'group_id': group_id, 'slot_id': slot_id, 'kind': 'healthy',
            'run_path': str(run_path), 'source_result_ref': result_ref, 'oracle_ref': oracle_ref,
            'source_manifest_sha256': manifest_ref.split('/')[-1].removesuffix('.json'), 'raw_refs': [raw_ref]}
    calls = []
    monkeypatch.setattr(forecast_replay, 'source_health_policy', lambda **kwargs: {'policy': {}, 'sha256': 'policy'})
    monkeypatch.setattr(forecast_replay, 'source_topology', lambda *args, **kwargs: {
        'graph_sha256': 'graph', 'excluded_edges': []})
    monkeypatch.setattr(forecast_replay, 'index_recorded_captures', lambda *args: ['index'])
    async def replay(index, **kwargs):
        calls.append(index)
        return serving, ['batches']
    monkeypatch.setattr(forecast_replay, 'replay_batches', replay)
    monkeypatch.setattr(forecast_replay, 'score_batches', lambda *args, **kwargs: {'score_schedule': {}, 'rows': []})
    monkeypatch.setattr(forecast_replay, 'project_latency_truth',
                        lambda store, case, service, schedule: ({'service': service}, []))
    result = asyncio.run(forecast_replay._score_verified_case(tmp_path, case, capture_commit=commit,
                         project=tmp_path, protocol={}, models={}, output_root=tmp_path / 'replay'))
    assert calls == [['index']] and len(result['truth']) == 3 and result['kind'] == 'healthy'
    run['environment'] = 'changed'
    run_path.write_text(json.dumps(run))
    with pytest.raises(ValueError, match='changed after sealed source verification'):
        asyncio.run(forecast_replay._score_verified_case(tmp_path, case, capture_commit=commit,
                    project=tmp_path, protocol={}, models={}, output_root=tmp_path / 'replay'))
    assert calls == [['index']]
