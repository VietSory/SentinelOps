"""Replay captured source availability through the existing health worker."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path

import sentinelops

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import clean_source
from sentinelops.ml.alibaba_local_evaluation import latency_forecast_labels
from sentinelops.ml.model_benchmark_forecast import MODELS, project_latency_truth, schedule_cutoffs
from sentinelops.ml.model_benchmark_forecast_inputs import (
    index_recorded_captures, load_sealed_inputs, source_health_policy, source_topology,
)
from sentinelops.ml.model_benchmark_holdout import verify_candidate_freeze
from sentinelops.ml.model_benchmark_runner import score_batch
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


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
    # The independent oracle needs one complete minute beyond the exclusive
    # score schedule to certify a no-breach timeline.
    stop = utc_time(run['ended_at']).replace(second=0, microsecond=0) - timedelta(minutes=1)
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
                    models=models['models'], xgboost_refit=models['xgboost_refit'], runtime=models.get('runtime'))
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


async def _score_verified_case(root, case, *, capture_commit, project, protocol, models, output_root):
    """Replay one previously verified sealed run without changing its sources."""
    root, project = Path(root).resolve(), Path(project).resolve()
    run_path = Path(case['run_path']).resolve()
    expected = root / 'capture/groups' / case['group_id'] / 'slots' / case['slot_id'] / 'runs' / case['run_id'] / 'run.json'
    if run_path != expected.resolve() or not run_path.is_relative_to(root):
        raise ValueError('verified forecast case has a foreign run path')
    run = json.loads(run_path.read_bytes())
    artifacts = ArtifactStore(root)
    source_result = artifacts.read(case['source_result_ref'])
    if (source_result['status'] != 'completed' or source_result['run_sha256'] != stable_id(run)
            or run['source_commit'] != capture_commit
            or run['source_manifest_ref'] != 'artifact://manifests/' + case['source_manifest_sha256'] + '.json'):
        raise ValueError('forecast run changed after sealed source verification')
    serving = ArtifactStore(run_path.parent / 'serving')
    source = serving.read(run['source_manifest_ref'])
    if sorted(source['raw_refs']) != sorted(case['raw_refs']):
        raise ValueError('forecast raw inventory changed after sealed source verification')
    certificate = artifacts.read(case['oracle_ref'])
    if (certificate.get('certificate_version') != 'local-http-oracle/v1'
            or certificate.get('source_result_ref') != case['source_result_ref']):
        raise ValueError('forecast oracle differs from the sealed source result')
    policy = source_health_policy(project=project, capture_commit=capture_commit, protocol=protocol)
    graph = source_topology(run, project=project, capture_commit=capture_commit)
    index = index_recorded_captures(serving, run)
    store, batches = await replay_batches(index, output_root=Path(output_root) / case['run_id'],
                                          policy=policy['policy'], environment=run['environment'])
    scores = score_batches(store, batches, case=case, run=run, graph=graph, models=models,
                           oracle=certificate['oracle'], policy=policy['policy'])
    truth, events = [], []
    for service in sorted(BUSINESS_ROUTES):
        record, found = project_latency_truth(artifacts, case, service, scores['score_schedule'])
        truth.append(record)
        events.extend(found)
    return {'group_id': case['group_id'], 'run_id': case['run_id'], 'slot_id': case['slot_id'],
            'kind': case['kind'], 'clock': 'recorded-source-availability-replay',
            'policy_sha256': policy['sha256'], 'graph_sha256': graph['graph_sha256'],
            'excluded_graph_edges': graph['excluded_edges'], 'score_schedule': scores['score_schedule'],
            'rows': scores['rows'], 'truth': truth, 'events': events}


def score_sealed(root, *, candidates_sha256, registration_sha256, seal_sha256, capture_project):
    """Score only the complete sealed cohort using an identifiable clean checkout."""
    root = Path(root).resolve()
    scorer = Path(__file__).resolve().parents[3]
    if not Path(sentinelops.__file__).samefile(scorer / 'src/sentinelops/__init__.py'):
        raise ValueError('forecast scoring must import its clean checkout source')
    scorer_commit = clean_source(scorer)
    inputs = load_sealed_inputs(root, candidates_sha256=candidates_sha256,
                                registration_sha256=registration_sha256, seal_sha256=seal_sha256)
    frozen = verify_candidate_freeze(root, candidates_sha256)
    protocol = json.loads((root / 'freeze/protocol.json').read_bytes())['protocol']
    if stable_id(protocol) != frozen['protocol_sha256']:
        raise ValueError('forecast protocol changed after input verification')
    replay_root = (root / 'score/a/replay').resolve()
    if not replay_root.is_relative_to(root) or replay_root.is_relative_to(root / 'capture'):
        raise ValueError('forecast replay output escapes its score root')

    async def run_cases():
        return [await _score_verified_case(root, case, capture_commit=inputs['capture_commit'],
                    project=capture_project, protocol=protocol, models=inputs['models'], output_root=replay_root)
                for case in inputs['cases']]

    cases = asyncio.run(run_cases())
    if clean_source(scorer) != scorer_commit:
        raise ValueError('forecast scorer source changed during replay')
    return {'scope': 'sealed-local-latency-holdout', 'clock': 'recorded-source-availability-replay',
            'scorer_commit': scorer_commit, 'capture_commit': inputs['capture_commit'],
            'candidates_sha256': candidates_sha256, 'registration_sha256': registration_sha256,
            'seal_sha256': seal_sha256, 'thresholds': inputs['thresholds'], 'cases': cases,
            'activation_allowed': False}
