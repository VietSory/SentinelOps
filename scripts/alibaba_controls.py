"""ADR-016 layer-1 controls: the Q-05/Q-07 comparisons already fixed by the v1 protocol.

Reads a finished fit and recomputes, on the same Alibaba evaluation services and
without refitting, retuning or changing any threshold:

- the fixed slope control (relative latency slope, threshold at the calibration
  healthy 99th percentile, as in the local protocol) against XGBoost average precision;
- precursor recall with lead >= 60 s for XGBoost (protocol risk_threshold 0.8)
  and for the slope control, both through the protocol persistence;
- the actual alert budget on risk negatives;
- paired service-level percentile bootstrap for IF-MAD event F1 and XGB-slope AP.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SLOPE = 'rt_slope_rel_per_min'


def iso(minute):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(minute * 60, timezone.utc).isoformat().replace('+00:00', 'Z')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fit', type=Path, required=True)
    parser.add_argument('--minutes', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('output must be new')
    import subprocess
    project = Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=project).strip():
        raise SystemExit('controls require a clean committed checkout')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
    import joblib
    import numpy as np
    from sklearn.metrics import average_precision_score
    import xgboost
    from sentinelops.ml.alibaba_training import build_rows, load_config, load_minutes
    from sentinelops.ml.evaluation import event_metrics
    from sentinelops.ml.local_evaluation import load_protocol, persistent_states, risk_curve
    from sentinelops.telemetry.core import stable_id

    fit = json.loads((args.fit / 'report.json').read_text())
    config = load_config()
    if stable_id(config) != fit['config_sha256']:
        raise SystemExit('frozen config differs from the fit')
    protocol = load_protocol()
    data, provenance = load_minutes(args.minutes)
    if provenance != fit['source_archives']:
        raise SystemExit('aggregates differ from the fit provenance')
    rows = build_rows(data, config)
    names = config['features']
    slope_index = names.index(SLOPE)
    calibration = [r['features'] for r in rows if r['split'] == 'calibration' and r['healthy']]
    evaluation = [r for r in rows if r['split'] == 'evaluation']
    counts = fit['counts']['evaluation']
    if len(evaluation) != counts['windows'] or len(calibration) != fit['counts']['calibration']['healthy']:
        raise SystemExit('rebuilt rows differ from the fit counts')

    detector = joblib.load(args.fit / 'detector.joblib')
    if detector.thresholds != fit['thresholds']:
        raise SystemExit('detector thresholds differ from the fit report')
    risk = xgboost.XGBClassifier()
    risk.load_model(args.fit / 'risk.json')
    values = [r['features'] for r in evaluation]
    detected = detector.predict(values)
    xgb = risk.predict_proba(values)[:, 1]
    slope = np.maximum(0., np.asarray(values)[:, slope_index])
    slope_threshold = float(np.quantile(np.maximum(0., np.asarray(calibration)[:, slope_index]),
                                        1 - protocol['false_alert_budget'], method='higher'))
    flags = {'isolation_forest': detected['isolation_forest']['alert'], 'median_mad': detected['median_mad']['alert'],
             'xgboost': xgb >= protocol['risk_threshold'], 'slope': slope > slope_threshold}

    by_service = {}
    for i, r in enumerate(evaluation):
        by_service.setdefault(r['service'], []).append(i)
    services = sorted(by_service)
    detector_runs = {k: [] for k in ('isolation_forest', 'median_mad')}
    precursor = {k: {} for k in ('xgboost', 'slope')}
    for service in services:
        idx = sorted(by_service[service], key=lambda i: evaluation[i]['minute'])
        events = sorted({evaluation[i]['onset_minute'] for i in idx if evaluation[i]['onset_minute'] is not None})
        healthy = 60 * sum(evaluation[i]['healthy'] for i in idx)
        states = {}
        for name, flag in flags.items():
            records = [{'window_end': iso(evaluation[i]['minute']), 'decision_at': iso(evaluation[i]['minute']),
                        'flag': bool(flag[i])} for i in idx]
            states[name] = persistent_states(records, 'flag', protocol)
        for name in detector_runs:
            detector_runs[name].append({'run_id': service, 'events': [
                {'service': service, 'onset_at': iso(m), 'deadline_at': iso(m + 5)} for m in events],
                'eligible_healthy_service_seconds': healthy,
                'alerts': [{'service': service, 'emitted_at': s['decision_at']} for s in states[name] if s['entered']]})
        for name in precursor:
            eligible = {}
            for i, s in zip(idx, states[name]):
                r = evaluation[i]
                if r['risk_label'] == 1:
                    lead = 60 * (r['onset_minute'] - r['minute'])
                    best = eligible.setdefault(r['onset_minute'], None)
                    if s['active'] and s['available'] and (best is None or lead > best):
                        eligible[r['onset_minute']] = lead
            precursor[name][service] = eligible

    labelled = np.array([i for i, r in enumerate(evaluation) if r['risk_label'] in (0, 1)])
    labels = np.array([evaluation[i]['risk_label'] for i in labelled])
    service_of = np.array([services.index(evaluation[i]['service']) for i in labelled])
    scores = {'xgboost': xgb[labelled], 'slope': slope[labelled]}
    risk_report = {}
    for name in precursor:
        events = [lead for svc in services for lead in precursor[name][svc].values()]
        qualified = sum(lead is not None and lead >= 60 for lead in events)
        risk_report[name] = {**risk_curve(labels.tolist(), scores[name].astype(float).tolist()),
                             'eligible_precursor_events': len(events), 'detected_precursor_events': sum(l is not None for l in events),
                             'precursor_recall_lead_ge_60s': qualified / len(events) if events else None,
                             'actual_alert_rate_on_negatives': float(flags[name][labelled][labels == 0].mean())}
    risk_report['slope']['threshold'] = slope_threshold
    risk_report['xgboost']['threshold'] = protocol['risk_threshold']

    detector_metrics = {name: event_metrics(runs) for name, runs in detector_runs.items()}
    per_run = {name: np.array([[d['tp'], d['fp'], d['fn']] for d in m['per_run']]) for name, m in detector_metrics.items()}
    rng = np.random.default_rng(protocol['bootstrap_seed'])
    positions = [np.nonzero(service_of == k)[0] for k in range(len(services))]
    f1_diff, ap_diff = [], []
    f1 = lambda c: 2 * c[0] / (2 * c[0] + c[1] + c[2]) if (2 * c[0] + c[1] + c[2]) else np.nan
    for _ in range(protocol['bootstrap_replicates']):
        pick = rng.integers(0, len(services), len(services))
        f1_diff.append(f1(per_run['isolation_forest'][pick].sum(0)) - f1(per_run['median_mad'][pick].sum(0)))
        rows_pick = np.concatenate([positions[k] for k in pick])
        y = labels[rows_pick]
        if 0 < y.sum() < len(y):
            ap_diff.append(average_precision_score(y, scores['xgboost'][rows_pick])
                           - average_precision_score(y, scores['slope'][rows_pick]))
    alpha = (1 - protocol['confidence_level']) / 2
    interval = lambda v: {'valid_replicates': int(np.isfinite(v).sum()), 'lower': float(np.nanquantile(v, alpha)),
                          'upper': float(np.nanquantile(v, 1 - alpha))}
    for m in detector_metrics.values():
        m.pop('per_run', None)
        m.pop('detection_delays_seconds', None)
        m.pop('measured_leads_seconds', None)
    report = {'report_version': 'alibaba-relative-controls/v1', 'authorizing_adr': 'ADR-016',
              'fit_report_sha256': stable_id(fit), 'config_sha256': fit['config_sha256'],
              'fitting_source_commit': fit['fitting_source_commit'], 'controls_source_commit': commit,
              'protocol_reused': {k: protocol[k] for k in ('false_alert_budget', 'entry_windows', 'exit_windows',
                                                          'risk_threshold', 'bootstrap_replicates', 'bootstrap_seed',
                                                          'confidence_level')},
              'slope_control_feature': SLOPE, 'detector_events': detector_metrics, 'risk': risk_report,
              'paired_service_bootstrap': {'method': 'paired-whole-service-percentile-bootstrap', 'services': len(services),
                                           'if_minus_mad_event_f1': interval(np.array(f1_diff)),
                                           'xgb_minus_slope_average_precision': interval(np.array(ap_diff))},
              'no_refit_or_retune': True, 'activation_allowed': False, 'G3_accepted': False,
              'limitations': fit['limitations'] + [
                  'Services share one cluster and time span; the service bootstrap is conditional, not IID proof.',
                  'Precursor lead is window end to first breach minute at one-minute resolution.']}
    args.output.mkdir(parents=True)
    (args.output / 'controls.json').write_text(json.dumps(report, indent=2, default=float) + '\n')
    print(json.dumps({k: report[k] for k in ('risk', 'paired_service_bootstrap')}, indent=2, default=float))


if __name__ == '__main__':
    main()
