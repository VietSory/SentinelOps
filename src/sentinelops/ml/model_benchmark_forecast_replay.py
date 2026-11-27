"""Replay captured source availability through the existing health worker."""
from datetime import timedelta

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_local_evaluation import latency_forecast_labels
from sentinelops.ml.model_benchmark_forecast import MODELS, schedule_cutoffs
from sentinelops.ml.model_benchmark_runner import score_batch
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp


async def replay_batches(index, *, output_root, policy, environment='local'):
    """Rebuild accepted batches, representing missing or late minutes explicitly.

    The caller supplies an index from a verified sealed source. This is an
    availability-clock replay, not a measurement of live inference latency.
    """
    from scripts.check_ltfp_graph_parity import WorkerReplay

    if not index or type(policy.get('maximum_lag_seconds')) is not int or policy['maximum_lag_seconds'] < 60:
        raise ValueError('replay requires a nonempty minute index and a valid source lag policy')
    captures = {row['bucket_end']: list(row['captures']) if row['cutoff'] is not None else [] for row in index}
    if len(captures) != len(index):
        raise ValueError('replay source has duplicate minutes')
    minutes = [utc_time(row['bucket_end']) for row in index]
    if any(b - a != timedelta(minutes=1) for a, b in zip(minutes, minutes[1:])):
        raise ValueError('replay source minutes are not contiguous')
    replay = WorkerReplay(output_root, captures, environment=environment, policy=policy)
    started, batches = False, []
    for row, end in zip(index, minutes):
        cutoff = utc_time(row['cutoff']) if row['cutoff'] is not None else None
        if cutoff is None and not started:
            batches.append({'bucket_end': row['bucket_end'], 'prediction_cutoff': None,
                            'source_available_at': row.get('observed_at'),
                            'batch_ref': None, 'missing_reason': row['missing_reason']})
            continue
        now = cutoff or end + timedelta(seconds=policy['maximum_lag_seconds'] + 1)
        if cutoff is not None and not end + timedelta(seconds=30) <= cutoff < end + timedelta(minutes=1):
            raise ValueError('replay cutoff is outside its available worker interval')
        result = await replay.tick(row['bucket_end'], now=now)
        if result.get('status') != 'COMMITTED':
            raise ValueError('health worker did not commit the scheduled replay minute')
        started = True
        batches.append({'bucket_end': row['bucket_end'], 'prediction_cutoff': row['cutoff'],
                        'source_available_at': row.get('observed_at', row['cutoff']),
                        'batch_ref': result['manifest_ref'], 'missing_reason': row['missing_reason']})
    return replay.store, batches


def score_batches(store, batches, *, case, run, graph, models, oracle, policy):
    """Score every scheduled run/service minute, retaining unavailable rows."""
    warmup = utc_time(run['warmup_end_at'])
    start = warmup.replace(second=0, microsecond=0)
    if start < warmup:
        start += timedelta(minutes=1)
    stop = utc_time(run['ended_at']).replace(second=0, microsecond=0)
    if stop <= start or run['run_id'] != case['run_id'] or run['session_id'] != case['group_id']:
        raise ValueError('forecast score schedule differs from the verified run')
    selected = [row for row in batches if start <= utc_time(row['bucket_end']) < stop]
    schedule = {'start': timestamp(start), 'stop': timestamp(stop), 'cadence_seconds': 60,
                'cutoffs': {row['bucket_end']: row['prediction_cutoff'] for row in selected}}
    expected = schedule_cutoffs(schedule)
    if len(selected) != len(expected):
        raise ValueError('replay batches duplicate or omit a scheduled minute')
    rows = []
    for batch in selected:
        end, cutoff = utc_time(batch['bucket_end']), batch['prediction_cutoff']
        frames, seen_services = {}, set()
        if cutoff is not None and batch['batch_ref'] is not None:
            manifest = store.read(batch['batch_ref'])
            if utc_time(manifest['bucket_end']) != end or utc_time(manifest['evaluated_at']) != utc_time(cutoff):
                raise ValueError('accepted worker batch differs from the source clock')
            normalization = store.read(manifest['normalization_manifest_ref'])
            for ref in normalization['feature_refs']:
                frame = store.read(ref)
                if utc_time(frame['window_end']) != end:
                    continue
                service = frame['service']
                if service not in BUSINESS_ROUTES or service in seen_services:
                    raise ValueError('accepted batch duplicates or changes a feature service')
                seen_services.add(service)
                if utc_time(frame['as_of']) == utc_time(cutoff):
                    frames[service] = ref
        for service in sorted(BUSINESS_ROUTES):
            feature_ref = frames.get(service)
            if cutoff is not None and feature_ref:
                scored = score_batch(store, batch_ref=batch['batch_ref'], anchor=batch['bucket_end'],
                    as_of=cutoff, service=service, source_feature_ref=feature_ref,
                    node_ids=graph['node_ids'], topology=graph['topology'], expected_graph_sha256=graph['graph_sha256'],
                    config=models['config'], bundle=models['bundle'], manifest_sha256=models['manifest_sha256'],
                    oracle=oracle, health_policy=policy, run_id=case['run_id'], group_id=case['group_id'],
                    models=models['models'], xgboost_refit=models['xgboost_refit'])
                rows.extend({**row, 'source_available_at': batch.get('source_available_at', cutoff)} for row in scored)
                continue
            frame = {'service': service, 'window_start': timestamp(end - timedelta(minutes=5)),
                     'window_end': timestamp(end), 'as_of': cutoff}
            labels = (latency_forecast_labels(frame, oracle, policy) if cutoff is not None else
                      {'risk_label': None, 'risk_missing_reason': 'source-clock-unavailable',
                       'prediction_cutoff': None, 'risk_forecast_anchor_at': timestamp(end),
                       'slo_onset_at': None, 'future_confirmation_tail': None})
            reason = batch['missing_reason'] or 'accepted-feature-unavailable'
            for name in MODELS:
                rows.append({'group_id': case['group_id'], 'run_id': case['run_id'], 'service': service,
                    'window_start': frame['window_start'], 'window_end': frame['window_end'], 'feature_ref': None,
                    'source_feature_ref': None, 'oracle_sha256': stable_id(oracle),
                    'source_available_at': batch.get('source_available_at'),
                    'health_policy_sha256': stable_id(policy), **labels, 'model': name, 'score': None,
                    'score_missing_reason': reason, 'model_provenance': {'scope': 'unavailable'},
                    'graph_provenance': None, 'fixture_scope': False, 'activation_allowed': False})
    return {'score_schedule': schedule, 'rows': rows}
