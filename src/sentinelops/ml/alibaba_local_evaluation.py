"""Evaluate the exact Alibaba fit once on a frozen, independent v2 local epoch."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median

from sentinelops.contracts import utc_time
from sentinelops.experiments.labels import SLOConfig
from sentinelops.experiments.local_campaign import register_alibaba_epoch
from sentinelops.experiments.local_corpus import (ALIBABA_PLAN, PROJECT, acquisition_lock, bind, clean_source,
                                                load_plan, read_alibaba_epoch, read_binding)
from sentinelops.incidents.health import validate_policy
from sentinelops.ml.alibaba_live import relative_feature_values
from sentinelops.ml.detector_evaluation import detector_summary, paired_detector_interval
from sentinelops.ml.local_data import _inside, audit_alibaba_local
from sentinelops.ml.local_evaluation import load_protocol, summarize
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import mean_latency_ms
from sentinelops.telemetry.store import ArtifactStore


def score_records(store, cases, detector, risk, config, scoring, protocol, health_policy):
    """Score only actual receipt-bounded minutes. Local outcomes are used as labels."""
    records = []
    for case in cases:
        result = store.read(case['source_result_ref'])
        captured = _inside(store.root, result['run_directory'])
        run = json.loads((captured/'run.json').read_bytes())
        if stable_id(run) != result['run_sha256']:
            raise ValueError('ADR-016 run changed after source verification')
        case['fault_mode'] = run['parameters']['fault_mode']
        serving = ArtifactStore(captured/'serving')
        source = serving.read(run['source_manifest_ref'])
        normalized = serving.read(source['normalization_manifest_ref'])
        observations = defaultdict(dict)
        for ref in normalized['observation_refs']:
            observation = serving.read(ref)
            if not observation['quality']['eligible_for_inference']:
                continue
            raw_ref, = observation['provenance']['source_refs']
            raw = serving.read(raw_ref)
            minute = int(utc_time(observation['bucket_end']).timestamp()) // 60
            slots = observations[observation['service']]
            if minute in slots:
                raise ValueError('ADR-016 source has duplicated eligible minutes')
            count = observation['measurements']['request_count']
            error = None
            if (count >= health_policy['minimum_requests']
                and observation['quality']['coverage_ratio'] >= health_policy['minimum_coverage']):
                error = observation['payload']['error_rate']
            slots[minute] = (mean_latency_ms(raw), count, error, utc_time(observation['available_at']))
        vectors, pending = [], []
        for row in case['rows']:
            cutoff, end = utc_time(row['prediction_cutoff']), utc_time(row['window_end'])
            anchor = int(end.timestamp()) // 60
            past = {minute: (rt, rate, error) for minute, (rt, rate, error, received) in
                    observations[row['service']].items() if minute <= anchor and received <= cutoff}
            record = {**{key: value for key, value in row.items() if key != 'features'},
                      'relative_features': None, 'decision_at': row['prediction_cutoff'],
                      'isolation_forest_flag': None, 'median_mad_flag': None,
                      'objective_slo_flag': None, 'online_error_mad_flag': None,
                      'xgboost_flag': None, 'slope_flag': None, 'xgboost_score': None, 'slope_score': None}
            records.append(record)
            if not end <= cutoff <= end + timedelta(seconds=180):
                continue
            baseline = [error for minute, (_, _, error) in past.items()
                        if anchor - 60 <= minute < anchor and error is not None]
            if (anchor in past and past[anchor][2] is not None
                and cutoff - end <= timedelta(seconds=health_policy['maximum_lag_seconds'])):
                current_error = past[anchor][2]
                record['objective_slo_flag'] = current_error > health_policy['error_rate']
                if len(baseline) >= config['baseline']['min_minutes']:
                    center = median(baseline)
                    scale = max(1.4826 * median(abs(value - center) for value in baseline),
                                scoring['error_mad_min_scale'])
                    record['online_error_mad_flag'] = current_error > center + scoring['error_mad_multiplier'] * scale
            values = relative_feature_values({minute: (rt, rate) for minute, (rt, rate, _) in past.items()
                                              if rt is not None and rt > 0}, anchor, config)
            if values is not None:
                record['relative_features'] = values
                vectors.append(values)
                pending.append((record, cutoff < end + timedelta(seconds=60)))
        if not vectors:
            continue
        detections = detector.predict(vectors)
        risks = risk.predict_proba(vectors)[:, 1]
        for i, (record, risk_available) in enumerate(pending):
            for name in ('isolation_forest', 'median_mad'):
                record[name + '_flag'] = bool(detections[name]['alert'][i])
            if risk_available:
                value = float(risks[i])
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError('ADR-016 XGBoost returned an invalid probability')
                slope = max(0., vectors[i][config['features'].index('rt_slope_rel_per_min')])
                record.update(xgboost_score=value, xgboost_flag=value >= protocol['risk_threshold'],
                              slope_score=slope, slope_flag=slope > scoring['risk_relative_slope_threshold'])
    return records


def metric_event_cases(cases, service, measure, threshold, persistence):
    """Derive a dimension-specific event from the independently verified minute grid."""
    projected = []
    for case in cases:
        original = case['oracle']
        evidence = original['services'][service]
        buckets, onset, confirmation, streak, previous = {}, None, None, 0, None
        for key, item in sorted(evidence['buckets'].items(), key=lambda pair: int(pair[0])):
            minute = int(key)
            value = item[measure] if item['availability'] == 'VERIFIED' else None
            breach = value > threshold if value is not None else None
            streak = streak + 1 if breach and previous == minute - 60 else int(breach is True)
            if streak == persistence and onset is None:
                onset = timestamp(datetime.fromtimestamp(minute - 60 * (persistence - 1), timezone.utc))
                confirmation = timestamp(datetime.fromtimestamp(minute + 60, timezone.utc))
            previous = minute
            buckets[key] = {**item, 'breach': breach}
        selected = {**evidence, 'first_violation_at': onset, 'confirmed_at': confirmation, 'buckets': buckets}
        projected.append({**case, 'oracle': {**original, 'services': {**original['services'], service: selected}}})
    return projected


def score_by_route(records, cases, protocol):
    """Keep latency, HTTP errors and gradual-forecast denominators separate."""
    slo = SLOConfig()
    services = {}
    for service in BUSINESS_ROUTES:
        service_rows = [row for row in records if row['service'] == service]

        def select(mode):
            selected = [case for case in cases if case['kind'] == 'healthy' or
                        (case['kind'].endswith('-abrupt') and case['fault_mode'] == mode)]
            run_ids = {case['run_id'] for case in selected}
            return selected, [row for row in service_rows if row['run_id'] in run_ids]

        latency_cases, latency_rows = select('latency')
        error_cases, error_rows = select('error')
        latency_cases = metric_event_cases(latency_cases, service, 'p95_latency_ms', slo.latency_ms, slo.persistence)
        error_cases = metric_event_cases(error_cases, service, 'error_rate', slo.error_rate, slo.persistence)
        controls = detector_summary([
            {**row, 'isolation_forest_flag': row['objective_slo_flag'], 'median_mad_flag': row['online_error_mad_flag']}
            for row in error_rows], error_cases, service, protocol)
        gradual = [case for case in cases if case['kind'].endswith('-gradual')]
        gradual_ids = {case['run_id'] for case in gradual}
        forecast = summarize([row for row in service_rows if row['run_id'] in gradual_ids], gradual, service, protocol)
        services[service] = {
            'latency_detector_events': detector_summary(latency_rows, latency_cases, service, protocol),
            'latency_if_minus_mad_uncertainty': paired_detector_interval(latency_rows, latency_cases, service, protocol),
            'error_detector_events': {'objective_slo': controls['isolation_forest'], 'online_error_mad': controls['median_mad']},
            'gradual_risk': forecast['risk'],
            'risk_target_unavailable_windows': forecast['risk_target_unavailable_windows'],
            'risk_labelled_prediction_unavailable_windows': forecast['risk_labelled_prediction_unavailable_windows'],
        }
    return services


def evaluate_alibaba_local(store, bundle, manifest_sha256, output, *, project=PROJECT):
    """Bind a single attempt before reading any numerical output from the fitted model."""
    output, bundle = Path(output).resolve(), Path(bundle).resolve(strict=True)
    if output.exists() or output.is_relative_to(store.root):
        raise ValueError('ADR-016 evaluation output must be new and outside the source epoch')
    source = clean_source(project)
    plan = load_plan(project/'configs'/ALIBABA_PLAN.name)
    protocol = load_protocol(project)
    health_policy = json.loads((project/'configs'/'slo_health_policy.json').read_bytes())
    validate_policy(health_policy)
    if (plan['scoring']['event_deadline_seconds'] != protocol['event_deadline_seconds']
        or plan['scoring']['forecast_horizon_seconds'] != protocol['risk_horizon_seconds']):
        raise ValueError('ADR-016 scoring clocks differ from the frozen evaluation protocol')
    with acquisition_lock(store):
        epoch_ref, epoch = read_alibaba_epoch(store, plan)
        if register_alibaba_epoch(store, plan, source, bundle, manifest_sha256) != epoch_ref:
            raise ValueError('ADR-016 evaluation does not match the preregistered candidate')
        if (store.root/'evaluation-attempt.json').exists():
            raise ValueError('ADR-016 allows one evaluation attempt per locked epoch')
        freeze_path = store.root/'evaluation-freeze.json'
        if freeze_path.exists():
            admission_ref, frozen = read_binding(store, freeze_path)
            report, _, cases = audit_alibaba_local(store, plan, project=project, now=utc_time(frozen['created_at']))
            if report != frozen:
                raise ValueError('ADR-016 source differs from its evaluation freeze')
        else:
            report, _, cases = audit_alibaba_local(store, plan, project=project)
            if report['evaluation_ready'] is not True:
                raise ValueError('ADR-016 epoch is not source-verified and complete')
            admission_ref = bind(store, freeze_path, report)
        if report['evaluation_ready'] is not True:
            raise ValueError('ADR-016 epoch is not source-verified and complete')
        attempt_ref = bind(store, store.root/'evaluation-attempt.json', {
            'attempt_version': 'alibaba-local-evaluation-attempt/v1', 'epoch_ref': epoch_ref,
            'admission_ref': admission_ref, 'started_at': timestamp(datetime.now(timezone.utc))})

    from sentinelops.ml.models import load_bundle
    manifest, detector, risk = load_bundle(bundle, expected_manifest_sha256=manifest_sha256)
    scored = score_records(store, cases, detector, risk, manifest['provenance']['config'], plan['scoring'], protocol,
                           health_policy)
    summary = score_by_route(scored, cases, protocol)
    output.mkdir(parents=True, exist_ok=False)
    results = ArtifactStore(output)
    prediction_ref = results.write('manifests', {'table_version': 'alibaba-local-evaluation-predictions/v1', 'rows': scored})
    report_ref = results.write('manifests', {
        'report_version': plan['evaluation_protocol'], 'epoch_ref': epoch_ref, 'admission_ref': admission_ref,
        'attempt_ref': attempt_ref, 'candidate_id': epoch['model_version'], 'bundle_manifest_sha256': manifest_sha256,
        'source_commit': source, 'source_profile': plan['source_profile'], 'feature_set_version': plan['feature_set_version'],
        'scoring': plan['scoring'], 'base_protocol_sha256': stable_id(protocol), 'predictions_ref': prediction_ref,
        'slo_health_policy_sha256': stable_id(health_policy),
        'evaluated_at': timestamp(datetime.now(timezone.utc)), 'services': summary, 'fitting_rows': 0,
        'G3_accepted': False, 'activation_allowed': False, 'holdout_opened': False,
        'limitations': ['Local S0/S1/S3 evidence with recorded source receipt clocks and counterfactual inference.',
                        'Latency and HTTP 5xx detector events use separate verified oracle dimensions.',
                        'Gradual forecasting uses the combined SLO target.',
                        'The local epoch supplies no fitting, calibration or model-selection inputs.'],
    })
    return {'report_ref': report_ref, 'epoch_ref': epoch_ref, 'G3_accepted': False, 'activation_allowed': False}
