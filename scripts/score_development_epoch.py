"""ADR-019 development check: score a candidate fit on an epoch that was already evaluated.

The epoch keeps its own single official evaluation. This run is development evidence only: it reads the
store without writing to it, rebuilds the verified cases with the epoch's own capture checkout, scores the
candidate with its Alibaba-calibrated thresholds and writes the summary outside the store. No threshold,
feature or weight is derived here, and the result never counts as acceptance.

Run it from this repository, with ``--project`` set to a clean checkout of the epoch's capture commit.
"""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Evaluated epoch store (read only)')
    parser.add_argument('--project', type=Path, required=True, help='Clean checkout at the epoch capture commit')
    parser.add_argument('--fit', type=Path, required=True, help='train_alibaba output directory')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('output must be new')
    import joblib
    import xgboost
    from sentinelops.contracts import utc_time
    from sentinelops.experiments.local_corpus import read_alibaba_epoch, read_binding
    from sentinelops.ml.alibaba_local_evaluation import score_by_route, score_records
    from sentinelops.ml.alibaba_training import load_config
    from sentinelops.ml.local_data import audit_alibaba_local
    from sentinelops.ml.local_evaluation import load_protocol
    from sentinelops.ml.models import ExceedanceRisk
    from sentinelops.telemetry.core import stable_id
    from sentinelops.telemetry.store import ArtifactStore

    project = args.project.resolve(strict=True)
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=project).strip():
        raise SystemExit('epoch checkout must be clean')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
    store = ArtifactStore(args.root)
    if args.output.resolve().is_relative_to(store.root.resolve()):
        raise SystemExit('development output must stay outside the epoch store')
    plan = json.loads((project / 'configs/alibaba_local_evaluation.json').read_bytes())
    epoch_ref, epoch = read_alibaba_epoch(store, plan)
    if not (store.root / 'evaluation-attempt.json').exists():
        raise SystemExit('only an epoch that already had its official evaluation may serve as development data')
    _, frozen = read_binding(store, store.root / 'evaluation-freeze.json')
    audit, _, cases = audit_alibaba_local(store, plan, project=project, now=utc_time(frozen['created_at']))
    if audit != frozen:
        raise SystemExit('epoch source differs from its evaluation freeze')
    fit = json.loads((args.fit / 'report.json').read_text())
    config = load_config()
    if fit.get('report_version') != 'alibaba-relative-development/v2' or fit['config_sha256'] != stable_id(config):
        raise SystemExit('fit does not match the frozen ADR-019 config')
    if fit.get('fault_shape_gate', {}).get('passed') is not True:
        raise SystemExit('fit failed the synthetic fault-shape gate; nothing to check')
    detector = joblib.load(args.fit / 'detector.joblib')
    booster = xgboost.XGBClassifier()
    booster.load_model(args.fit / 'risk.json')
    risk = ExceedanceRisk(booster, config['features'])
    protocol = load_protocol()
    health_policy = json.loads((project / 'configs/slo_health_policy.json').read_bytes())
    threshold = fit['risk_thresholds']['degrading']
    records = score_records(store, cases, detector, risk, config, plan['scoring'], protocol, health_policy, threshold)
    summary = score_by_route(records, cases, protocol)
    result = {'report_version': 'alibaba-development-check/v1', 'authorizing_adr': 'ADR-019',
              'role': 'development-only; never acceptance', 'epoch_ref': epoch_ref, 'epoch_source_commit': epoch['source_commit'],
              'epoch_checkout': head, 'fit_report_sha256': stable_id(fit), 'fitting_source_commit': fit['fitting_source_commit'],
              'thresholds': fit['thresholds'], 'risk_threshold': threshold, 'services': summary}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=float) + '\n')
    print(json.dumps({'output': str(args.output)}))


if __name__ == '__main__':
    main()
