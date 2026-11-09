"""ADR-016 development evidence: the Alibaba bundle on the frozen ADR-015 detector epoch.

The epoch was captured on profile v1, which has no histogram `_sum`, so the
latency input is the per-minute p95 instead of the mean the model was trained
on. ADR-016 allows this only as a disclosed fallback; it is development evidence
and never layer 2. Relative windows use every accepted observation of the run
from its start (warm-up included, as a live service would), and a window is
scored only when its last minute was available at the recorded v1 cutoff.
Event matching, persistence, healthy exposure and the group bootstrap are the
frozen v2 protocol functions. Nothing is fitted, tuned or activated.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--detector-store', type=Path, required=True)
    parser.add_argument('--detector-report-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('output must be new')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=PROJECT).strip():
        raise SystemExit('evaluation requires a clean committed checkout')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PROJECT, text=True).strip()
    import pandas as pd
    from sentinelops.contracts import utc_time
    from sentinelops.experiments.local_corpus import load_plan
    from sentinelops.ml.alibaba_training import service_windows_fast
    from sentinelops.ml.detector_evaluation import detector_summary, load_protocol_v2, paired_detector_interval
    from sentinelops.ml.local_data import detector_evaluation_inventory
    from sentinelops.ml.models import load_bundle
    from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
    from sentinelops.telemetry.store import ArtifactStore

    manifest, detector, _ = load_bundle(args.bundle, expected_manifest_sha256=args.manifest_sha256)
    config = manifest['provenance']['config']
    protocol_v2, protocol = load_protocol_v2(PROJECT)
    plan = load_plan(PROJECT / 'configs/local_http_detector_events_plan.json')
    det = ArtifactStore(args.detector_store)
    report = det.read(args.detector_report_ref)
    rows, cases = detector_evaluation_inventory(det, plan, report, project=PROJECT)

    by_run = defaultdict(list)
    for row in rows:
        by_run[(row['run_id'], row['service'])].append(row)
    records, coverage = defaultdict(list), defaultdict(lambda: {'v1_windows': 0, 'scored': 0})
    for case in cases:
        result = det.read(case['source_result_ref'])
        run_dir = args.detector_store / result['run_directory']
        run = json.loads((run_dir / 'run.json').read_text())
        serving = ArtifactStore(run_dir / 'serving')
        normalization = serving.read(serving.read(run['source_manifest_ref'])['normalization_manifest_ref'])
        observations = [serving.read(ref) for ref in normalization['observation_refs']]
        for service in sorted(BUSINESS_ROUTES):
            minutes = {}
            for obs in observations:
                p95 = (obs.get('payload') or {}).get('p95_latency_ms')
                if obs['service'] == service and obs['quality']['eligible_for_inference'] and p95 and p95 > 0:
                    minute = int(utc_time(obs['bucket_end']).timestamp()) // 60
                    minutes[minute] = (float(p95), float(obs['measurements']['request_count']), utc_time(obs['available_at']))
            series = pd.DataFrame.from_dict({m: v[:2] for m, v in minutes.items()}, orient='index',
                                            columns=['rt', 'rate']).sort_index()
            windows = {w['minute']: w['features'] for w in service_windows_fast(series, config)} if len(series) else {}
            for row in sorted(by_run[(case['run_id'], service)], key=lambda r: r['window_end']):
                end, cutoff = utc_time(row['window_end']), utc_time(row['prediction_cutoff'])
                minute = int(end.timestamp()) // 60
                features = windows.get(minute) if minute in minutes and minutes[minute][2] <= cutoff else None
                coverage[service]['v1_windows'] += 1
                record = {'run_id': row['run_id'], 'group_id': row['group_id'], 'window_end': row['window_end'],
                          'decision_at': row['prediction_cutoff'], 'risk_label': row['risk_label'],
                          'isolation_forest_flag': None, 'median_mad_flag': None}
                if features is not None:
                    detected = detector.predict([features])
                    coverage[service]['scored'] += 1
                    for name in ('isolation_forest', 'median_mad'):
                        record[name + '_flag'] = bool(detected[name]['alert'][0])
                        record[name + '_score'] = float(detected[name]['score'][0])
                records[service].append(record)

    services = {}
    for service in sorted(BUSINESS_ROUTES):
        services[service] = {'detector_events': detector_summary(records[service], cases, service, protocol),
                             'uncertainty': paired_detector_interval(records[service], cases, service, protocol),
                             'coverage': coverage[service]}
    result = {'evaluation_version': 'alibaba-local-detector-epoch/v1', 'authorizing_adr': 'ADR-016',
              'role': 'development-evidence', 'latency_statistic': 'p95-fallback (profile v1 has no _sum)',
              'model_version': manifest['model_version'], 'bundle_manifest_sha256': args.manifest_sha256,
              'config_sha256': stable_id(config), 'detector_report_ref': args.detector_report_ref,
              'protocol_sha256': stable_id(protocol_v2), 'evaluation_source_commit': commit,
              'evaluated_at': timestamp(pd.Timestamp.now(tz='UTC').to_pydatetime()), 'services': services,
              'activation_allowed': False, 'G3_accepted': False, 'holdout_opened': False,
              'limitations': [
                  'p95 replaces the mean latency the model was trained on; not layer 2 and not a final claim.',
                  'The epoch was already inspected with candidate 38bcc766; it is development evidence only.',
                  'Windows need five baseline minutes before the window; earlier windows are unscored (flag unavailable).',
                  'Event metrics use recorded v1 feature cutoffs with instantaneous counterfactual inference.']}
    args.output.mkdir(parents=True)
    (args.output / 'report.json').write_text(json.dumps(result, indent=2, default=float) + '\n')
    print(json.dumps({s: {'coverage': v['coverage'], **{n: {k: v['detector_events'][n][k] for k in ('tp', 'fp', 'fn', 'f1')}
                                                         for n in ('isolation_forest', 'median_mad')},
                          'ci': v['uncertainty']['if_minus_mad_event_f1']} for s, v in services.items()}, indent=1))


if __name__ == '__main__':
    main()
