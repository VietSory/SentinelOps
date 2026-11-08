"""ADR-016: fit IF/MAD/XGBoost on Alibaba train services, evaluate on unseen services.

Uses only the frozen config. Train rows fit; calibration rows set thresholds with
the existing 1% budget; evaluation services are scored once. Writes an exclusive
output directory with the detector, risk model and a pinned report. Nothing is
released or activated here.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def iso(minute):
    return datetime.fromtimestamp(minute * 60, timezone.utc).isoformat().replace('+00:00', 'Z')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=Path, required=True, help='Verified Alibaba aggregate directory')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import joblib
    import numpy as np
    from sentinelops.ml.alibaba_training import CONFIG, build_rows, load_config, load_minutes
    from sentinelops.ml.evaluation import event_metrics
    from sentinelops.ml.local_evaluation import load_protocol, persistent_states, risk_curve
    from sentinelops.ml.models import fit_detector, fit_risk
    from sentinelops.telemetry.core import stable_id
    if args.output.exists():
        raise SystemExit('output must be new')
    project = Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=project).strip():
        raise SystemExit('fit requires a clean committed checkout')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
    config = load_config()
    data, provenance = load_minutes(args.minutes)
    rows = build_rows(data, config)
    names = config['features']
    split = {k: [r for r in rows if r['split'] == k] for k in ('train', 'calibration', 'evaluation')}
    train_healthy = [r['features'] for r in split['train'] if r['healthy']]
    calibration_healthy = [r['features'] for r in split['calibration'] if r['healthy']]
    detector = fit_detector(train_healthy, calibration_healthy, names, seed=config['seed'],
                            false_alert_budget=config['false_alert_budget'])
    labelled = [r for r in split['train'] if r['risk_label'] in (0, 1)]
    risk = fit_risk([r['features'] for r in labelled], [r['risk_label'] for r in labelled], names, seed=config['seed'])
    protocol = load_protocol()
    evaluation = split['evaluation']
    detected = detector.predict([r['features'] for r in evaluation])
    risks = risk.predict_proba([r['features'] for r in evaluation])[:, 1]
    by_service = {}
    for i, r in enumerate(evaluation):
        by_service.setdefault(r['service'], []).append((r, i))
    detector_runs = {name: [] for name in ('isolation_forest', 'median_mad')}
    for service, items in by_service.items():
        items.sort(key=lambda x: x[0]['minute'])
        events = sorted({x[0]['onset_minute'] for x in items if x[0]['onset_minute'] is not None})
        healthy_seconds = 60 * sum(1 for x in items if x[0]['healthy'])
        for name in detector_runs:
            records = [{'window_end': iso(x[0]['minute']), 'decision_at': iso(x[0]['minute']),
                        'flag': bool(detected[name]['alert'][x[1]])} for x in items]
            states = persistent_states(records, 'flag', protocol)
            detector_runs[name].append({'run_id': service, 'events': [
                {'service': service, 'onset_at': iso(m), 'deadline_at': iso(m + 5)} for m in events],
                'eligible_healthy_service_seconds': healthy_seconds,
                'alerts': [{'service': service, 'emitted_at': s['decision_at']} for s in states if s['entered']]})
    risk_rows = [(r['risk_label'], float(risks[i])) for i, r in enumerate(evaluation) if r['risk_label'] in (0, 1)]
    args.output.mkdir(parents=True)
    joblib.dump(detector, args.output / 'detector.joblib', compress=3)
    risk.save_model(args.output / 'risk.json')
    report = {'report_version': 'alibaba-relative-development/v1', 'authorizing_adr': 'ADR-016',
              'fitting_source_commit': commit, 'config': config, 'config_sha256': stable_id(config),
              'source_archives': provenance,
              'counts': {k: {'windows': len(v), 'healthy': sum(r['healthy'] for r in v),
                             'positives': sum(r['risk_label'] == 1 for r in v),
                             'negatives': sum(r['risk_label'] == 0 for r in v),
                             'services': len({r['service'] for r in v})} for k, v in split.items()},
              'thresholds': detector.thresholds,
              'evaluation': {'detector_events': {name: event_metrics(runs) for name, runs in detector_runs.items()},
                             'risk': risk_curve([a for a, _ in risk_rows], [b for _, b in risk_rows])},
              'activation_allowed': False, 'G3_accepted': False,
              'limitations': ['Alibaba MSRTMCR has no error/status field; 5xx detection is not learned.',
                              'Breach labels are relative-latency rules on production minutes, not operator incidents.',
                              'Evaluation services are disjoint by identity but share the same cluster and time span.']}
    for name in ('isolation_forest', 'median_mad'):
        report['evaluation']['detector_events'][name].pop('per_run', None)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2, default=float) + '\n')
    print(json.dumps({'output': str(args.output), 'counts': report['counts']}, indent=2))


if __name__ == '__main__':
    main()
