"""ADR-016/ADR-019: fit IF/MAD/XGBoost on Alibaba train services, evaluate on unseen services.

Uses only the frozen config. Train rows fit; calibration rows set every threshold by the frozen
false-alert budgets; the synthetic fault-shape gate runs on the fitted models; evaluation services
are scored once. ``--development`` never reads an evaluation service and its output cannot be
packaged. Writes an exclusive output directory. Nothing is released or activated here.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess


def iso(minute):
    return datetime.fromtimestamp(int(minute) * 60, timezone.utc).isoformat().replace('+00:00', 'Z')


def with_breach(base, multiplier):
    """Append the breach-relative feature for a breach defined as ``multiplier`` times the baseline."""
    import numpy as np
    return np.column_stack([base, np.full(len(base), -math.log(multiplier))])


def risk_summary(risk, base, labels, multipliers, threshold):
    from sentinelops.ml.local_evaluation import risk_curve
    summary = {}
    for column, multiplier in enumerate(multipliers):
        known = labels[:, column] >= 0
        y = labels[known, column]
        scores = risk.predict_proba(with_breach(base[known], multiplier))[:, 1]
        curve = risk_curve([int(v) for v in y], [float(v) for v in scores])
        curve['recall_at_degrading'] = float((scores[y == 1] >= threshold).mean()) if (y == 1).any() else None
        curve['alert_rate_on_negatives'] = float((scores[y == 0] >= threshold).mean()) if (y == 0).any() else None
        summary[f'{multiplier:g}'] = curve
    return summary


def detector_events(detector, arrays, selected, base, multiplier, protocol):
    import numpy as np
    from sentinelops.ml.evaluation import event_metrics
    from sentinelops.ml.local_evaluation import persistent_states
    rows = np.nonzero(selected)[0]
    detected = detector.predict(with_breach(base[rows], multiplier))
    runs = {name: [] for name in ('isolation_forest', 'median_mad')}
    service = arrays['service'][rows]
    for start, stop in zip(*(lambda cut: (cut[:-1], cut[1:]))(np.concatenate(
            [[0], np.nonzero(np.diff(service))[0] + 1, [len(rows)]]))):
        index = rows[start:stop]
        name_of = arrays['services'][service[start]]
        onsets = arrays['onset_minute'][index]
        events = sorted({int(m) for m in onsets[np.isfinite(onsets)]})
        healthy_seconds = 60 * int(arrays['healthy'][index].sum())
        for name in runs:
            records = [{'window_end': iso(m), 'decision_at': iso(m), 'flag': bool(flag)}
                       for m, flag in zip(arrays['minute'][index], detected[name]['alert'][start:stop])]
            states = persistent_states(records, 'flag', protocol)
            runs[name].append({'run_id': name_of, 'events': [
                {'service': name_of, 'onset_at': iso(m), 'deadline_at': iso(m + 5)} for m in events],
                'eligible_healthy_service_seconds': healthy_seconds,
                'alerts': [{'service': name_of, 'emitted_at': s['decision_at']} for s in states if s['entered']]})
    metrics = {name: event_metrics(items) for name, items in runs.items()}
    for item in metrics.values():
        item.pop('per_run', None)
    return metrics


def progress(stage):
    import resource, sys
    print(json.dumps({'stage': stage, 'max_rss_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}),
          file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=Path, required=True, help='Verified Alibaba aggregate directory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--development', action='store_true',
                        help='Fit and gate only; evaluation services are not read and the fit cannot be packaged')
    args = parser.parse_args()
    import joblib
    import numpy as np
    from sentinelops.ml.alibaba_training import build_arrays, load_config, load_minutes
    from sentinelops.ml.fault_shapes import fault_shape_gate
    from sentinelops.ml.local_evaluation import load_protocol
    from sentinelops.ml.models import fit_detector, fit_exceedance_risk
    from sentinelops.telemetry.core import stable_id
    if args.output.exists():
        raise SystemExit('output must be new')
    project = Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=project).strip():
        raise SystemExit('fit requires a clean committed checkout')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
    config = load_config()
    data, provenance = load_minutes(args.minutes)
    splits = ('train', 'calibration') if args.development else ('train', 'calibration', 'evaluation')
    arrays = build_arrays(data, config, splits=splits)
    progress('windows')
    names, multipliers = config['features'], config['label']['breach_multipliers']
    base, labels = arrays['features'], arrays['labels']
    split = arrays['service_split'][arrays['service']]
    train, calibration = split == 'train', split == 'calibration'
    healthy = arrays['healthy']
    detector = fit_detector(with_breach(base[train & healthy], multipliers[0]),
                            with_breach(base[calibration & healthy], multipliers[0]), names, seed=config['seed'],
                            false_alert_budget=config['false_alert_budget'],
                            detector_features=config['detector']['features'])
    progress('detector')
    spec = config['risk']
    rng = np.random.default_rng(config['seed'])
    keep = spec['negative_keep_one_in']
    train_rows = np.nonzero(train)[0]
    parts, targets, weights = [], [], []
    for column, multiplier in enumerate(multipliers):
        positive = train_rows[labels[train_rows, column] == 1]
        negative = train_rows[labels[train_rows, column] == 0]
        negative = np.sort(rng.choice(negative, size=len(negative) // keep, replace=False))
        parts.append(with_breach(base[np.concatenate([positive, negative])], multiplier))
        targets.append(np.concatenate([np.ones(len(positive), int), np.zeros(len(negative), int)]))
        weights.append(np.concatenate([np.ones(len(positive)), np.full(len(negative), float(keep))]))
    risk = fit_exceedance_risk(np.concatenate(parts), np.concatenate(targets), np.concatenate(weights), names,
                               monotone_constraints=spec['monotone_constraints'], estimator=spec['estimator'],
                               seed=config['seed'])
    progress('risk-fit')
    risk_thresholds = {}
    for level, rule in spec['thresholds'].items():
        column = multipliers.index(rule['multiplier'])
        negatives = calibration & (labels[:, column] == 0)
        scores = risk.predict_proba(with_breach(base[negatives], rule['multiplier']))[:, 1]
        risk_thresholds[level] = float(np.quantile(scores, 1 - rule['false_alert_budget'], method='higher'))
    if not 0 < risk_thresholds['watch'] < risk_thresholds['degrading'] < 1:
        raise SystemExit('calibrated risk thresholds are not ordered watch < degrading')
    progress('risk-thresholds')
    protocol = load_protocol()
    gate = fault_shape_gate(detector, risk, config, protocol, risk_thresholds['degrading'])
    progress('fault-shape-gate')
    evaluation = None
    if not args.development:
        selected = split == 'evaluation'
        evaluation = {'detector_events': detector_events(detector, arrays, selected, base, multipliers[0], protocol),
                      'risk': risk_summary(risk, base[selected], labels[selected], multipliers, risk_thresholds['degrading'])}
    progress('evaluation')
    args.output.mkdir(parents=True)
    joblib.dump(detector, args.output / 'detector.joblib', compress=3)
    risk.save_model(args.output / 'risk.json')
    report = {'report_version': 'alibaba-relative-development/v2', 'authorizing_adr': 'ADR-019',
              'development_fit': bool(args.development),
              'fitting_source_commit': commit, 'config': config, 'config_sha256': stable_id(config),
              'source_archives': provenance,
              'counts': {name: {'windows': int((split == name).sum()), 'healthy': int((healthy & (split == name)).sum()),
                                'positives': {f'{m:g}': int((labels[split == name, i] == 1).sum()) for i, m in enumerate(multipliers)},
                                'negatives': {f'{m:g}': int((labels[split == name, i] == 0).sum()) for i, m in enumerate(multipliers)},
                                'services': int(len(set(arrays['service'][split == name].tolist())))} for name in splits},
              'thresholds': detector.thresholds, 'risk_thresholds': risk_thresholds,
              'calibration': {'risk': risk_summary(risk, base[calibration], labels[calibration], multipliers,
                                                   risk_thresholds['degrading'])},
              'fault_shape_gate': gate, 'evaluation': evaluation,
              'activation_allowed': False, 'G3_accepted': False,
              'limitations': ['Alibaba MSRTMCR has no error/status field; 5xx detection is not learned.',
                              'Breach labels are relative-latency rules on production minutes, not operator incidents.',
                              'Evaluation services are disjoint by identity but share the same cluster and time span.',
                              'Risk thresholds are calibrated on breaches defined as multiples of the baseline; '
                              'an SLO further from the baseline than the largest multiplier is extrapolated by the '
                              'monotone constraints.']}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2, default=float) + '\n')
    print(json.dumps({'output': str(args.output), 'development_fit': bool(args.development),
                      'thresholds': detector.thresholds, 'risk_thresholds': risk_thresholds,
                      'fault_shape_gate': {'passed': gate['passed'], 'checks': gate['checks']}}, indent=2))


if __name__ == '__main__':
    main()
