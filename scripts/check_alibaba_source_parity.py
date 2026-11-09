"""Target-environment source parity for the ADR-016 Alibaba bundle (profile v2).

Runs the real services on an owned local Kubernetes sandbox with healthy traffic
and checks criteria fixed here before any measurement. The record is PASS only
when every criterion passes. It never writes a release, approval or activation
and removes all owned resources on exit.

Criteria `alibaba-parity-criteria/v1`:
  P1 the bundle verifies against its manifest SHA-256 (sentinelops-model/v3,
     origin admitted-alibaba-v2022), its frozen config equals the repository
     config and its report hash matches the provenance;
  P2 every raw capture uses `prometheus-increase-60s-5s/v2` and carries the
     pinned histogram `_sum`/`_count` queries;
  P3 every bucket is eligible with full scrape coverage and a receipt lag within
     the frozen SLO health policy `maximum_lag_seconds`;
  P4 Prometheus request counts match the independent server-completion ledger
     within 5% per bucket, with no 5xx;
  P5 per-minute mean latency (`increase(_sum)/increase(_count)`, the statistic the
     model is trained on) is within 10% of the exact server-log mean per bucket.
     The model reads ratios to the service's own trailing median, so a constant
     scale bias cancels; 10% bounds per-minute noise from scrape alignment;
  P6 after the minimum baseline history, the shared training transform
     (`service_windows_fast`) yields a final relative window for every service
     from the Prometheus minutes, with the bundle's feature order and finite IF,
     MAD and XGBoost scores.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import signal
import subprocess

import httpx

from scripts.check_kubernetes_testbed import BusinessSandbox, pod_samples
from scripts.check_model_source_parity import FIRST_AGE, COUNT_TOLERANCE, drive, exact_logs, utcnow
from scripts.kubernetes_testbed import SERVICES
from scripts.run_s2_h4 import clean_commit, deploy, emit, wait_ready
from sentinelops.ml.release import PARITY_VERIFICATION_VERSION, PARITY_VERSION, parity_verification_ref
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import REQUESTS, SOURCE_PROFILE_V2, PrometheusSource, mean_latency_ms, queries
from sentinelops.telemetry.store import ArtifactStore

CRITERIA_VERSION = 'alibaba-parity-criteria/v1'
MEAN_TOLERANCE = .10
PROJECT = Path(__file__).resolve().parents[1]


def verify_bundle(bundle, manifest_sha256):
    """P1: integrity and frozen-config identity before any traffic."""
    from sentinelops.ml.alibaba_training import load_config
    from sentinelops.ml.models import load_bundle
    manifest, detector, risk = load_bundle(bundle, expected_manifest_sha256=manifest_sha256)
    provenance = manifest['provenance']
    report = (bundle / 'report.json').read_bytes()
    ok = (manifest.get('bundle_version') == 'sentinelops-model/v3' and manifest.get('origin') == 'admitted-alibaba-v2022'
          and provenance['config'] == load_config() and hashlib.sha256(report).hexdigest() == provenance['report_sha256']
          and risk is not None)
    return manifest, detector, risk, ok


def minute_count(config):
    """Window plus the minimum baseline, plus one spare minute against scrape loss."""
    return config['window_minutes'] + config['baseline']['min_minutes'] + 1


async def capture_buckets(client, source, ends, inventory):
    captures = []
    for end in ends:
        await asyncio.sleep(max(0, (end + timedelta(seconds=FIRST_AGE) - utcnow()).total_seconds()))
        current = await asyncio.gather(*(asyncio.to_thread(source.capture, s, end, profile=SOURCE_PROFILE_V2)
                                         for s in SERVICES))
        for capture in current:
            service = capture['service']
            method, route = BUSINESS_ROUTES[service]
            selector = ','.join(k + '=' + json.dumps(v) for k, v in [('service', service), ('method', method), ('route', route)])
            expression = 'sum by (k8s_pod_uid,k8s_namespace_name) (increase(' + REQUESTS + '{' + selector + '}[60s]))'
            body = (await client.get(source.endpoint + '/api/v1/query',
                                     params={'query': expression, 'time': end.timestamp()})).raise_for_status().json()
            pod_count = pod_samples(body, inventory[service], as_of=end)
            aggregate = float(capture['queries']['requests']['response']['data']['result'][0]['value'][1])
            if not math.isclose(pod_count, aggregate, rel_tol=1e-8, abs_tol=1e-8):
                raise ValueError('metric aggregate does not resolve to the observed Kubernetes pod')
        captures.extend(current)
        emit('minute-captured', bucket_end=timestamp(end))
    return captures


def evaluate(root, bundle_parts, captures, logs, ends, policy):
    import pandas as pd
    from sentinelops.ml.alibaba_training import service_windows_fast
    manifest, detector, risk, bundle_ok = bundle_parts
    config = manifest['provenance']['config']
    checks, failures = {}, []

    def check(name, ok, detail):
        checks.setdefault(name, []).append({'ok': bool(ok), **detail})
        if not ok:
            failures.append({'criterion': name, **detail})

    check('P1', bundle_ok, {'model_version': manifest['model_version']})
    for raw in captures:
        expected = queries(raw['service'], SOURCE_PROFILE_V2)
        check('P2', raw['source_profile'] == SOURCE_PROFILE_V2
              and {k: v['expression'] for k, v in raw['queries'].items()} == expected,
              {'service': raw['service'], 'bucket_end': raw['bucket_end']})
    store = ArtifactStore(root / 'source')
    normalized = process_captures(captures, store)
    observations = [store.read(ref) for ref in normalized['observation_refs']]
    check('P3', normalized['observation_count'] == len(ends) * len(SERVICES) and not normalized['quarantine_refs'],
          {'what': 'all buckets normalized', 'observations': normalized['observation_count']})
    for service in SERVICES:
        minutes = {}
        for end in ends:
            obs = next(o for o in observations if o['service'] == service and o['bucket_end'] == timestamp(end))
            raw = next(c for c in captures if c['service'] == service and c['bucket_end'] == timestamp(end))
            lag = (datetime.fromisoformat(obs['available_at'].replace('Z', '+00:00')) - end).total_seconds()
            check('P3', obs['quality']['eligible_for_inference'] and obs['quality']['coverage_ratio'] == 1
                  and lag <= policy['maximum_lag_seconds'],
                  {'service': service, 'bucket_end': timestamp(end), 'lag_seconds': lag,
                   'coverage': obs['quality']['coverage_ratio']})
            records = [r for r in logs if r['service'] == service
                       and (end.timestamp() - 60) * 1000 < r['timestamp_unix_ms'] <= end.timestamp() * 1000]
            count = obs['measurements']['request_count'] or 0
            relative = abs(count / len(records) - 1) if records else None
            check('P4', relative is not None and relative <= COUNT_TOLERANCE and obs['measurements']['error_count'] == 0,
                  {'service': service, 'bucket_end': timestamp(end), 'prometheus_count': count,
                   'server_completions': len(records), 'relative_difference': relative})
            mean = mean_latency_ms(raw)
            exact = sum(r['duration_ms'] for r in records) / len(records) if records else None
            difference = abs(mean / exact - 1) if mean is not None and exact else None
            check('P5', difference is not None and difference <= MEAN_TOLERANCE,
                  {'service': service, 'bucket_end': timestamp(end), 'prometheus_mean_ms': mean,
                   'exact_mean_ms': exact, 'relative_difference': difference})
            if mean is not None and obs['quality']['eligible_for_inference']:
                minutes[int(end.timestamp()) // 60] = (mean, float(count))
        series = pd.DataFrame.from_dict(minutes, orient='index', columns=['rt', 'rate']).sort_index()
        rows = service_windows_fast(series, config) if len(series) else []
        last = int(ends[-1].timestamp()) // 60
        values = rows[-1]['features'] if rows and rows[-1]['minute'] == last else None
        scores = None
        if values is not None:
            detection = detector.predict([values])
            scores = {name: float(item['raw_score'][0]) for name, item in detection.items()}
            scores['xgboost'] = float(risk.predict_proba([values])[0, 1])
        check('P6', values is not None and manifest['ordered_features'] == config['features']
              and all(math.isfinite(v) for v in values) and all(math.isfinite(v) for v in scores.values()),
              {'service': service, 'window_end': timestamp(ends[-1]),
               'features': dict(zip(config['features'], values)) if values else None, 'scores': scores})
    return checks, failures, normalized['manifest_ref']


async def measure(sandbox, root, bundle_parts, policy):
    targets = {s: sandbox.forward(s, 8000) for s in SERVICES}
    source = PrometheusSource(sandbox.forward('prometheus', 9090))
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            for url in (*[t + '/ready' for t in targets.values()], source.endpoint + '/-/ready'):
                await wait_ready(client, url)
            config = await asyncio.to_thread(source.verify_configuration)
            inventory = await asyncio.to_thread(sandbox.inventory)
            first_end = (int(utcnow().timestamp()) // 60 + 2) * 60
            count = minute_count(bundle_parts[0]['provenance']['config'])
            ends = [datetime.fromtimestamp(first_end + m * 60, timezone.utc) for m in range(count)]
            captures, rows = await asyncio.gather(capture_buckets(client, source, ends, inventory),
                                                  drive(client, targets, ends[-1] + timedelta(seconds=39)))
            if inventory != await asyncio.to_thread(sandbox.inventory):
                raise ValueError('workload pod identity changed while sampling')
            logs = await asyncio.to_thread(exact_logs, sandbox, inventory, rows, ends)
            return evaluate(root, bundle_parts, captures, logs, ends, policy) + (config,)
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True, help='From a separate trusted channel')
    parser.add_argument('--environment', required=True)
    parser.add_argument('--verified-by', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    commit = clean_commit()
    bundle = args.bundle.resolve(strict=True)
    bundle_parts = verify_bundle(bundle, args.manifest_sha256)
    if not bundle_parts[3]:
        raise SystemExit('P1: bundle does not verify; no sandbox started')
    policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_text())
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    sandbox = BusinessSandbox(root, args.kind)

    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        images = deploy(sandbox)
        checks, failures, normalization_ref, config = asyncio.run(measure(sandbox, root, bundle_parts, policy))
        manifest = bundle_parts[0]
        scope = {'candidate_id': manifest['model_version'], 'environment': args.environment,
                 'source_profile': SOURCE_PROFILE_V2,
                 'models': [{'service': s, 'model_version': manifest['model_version']} for s in sorted(BUSINESS_ROUTES)],
                 'verified_by': args.verified_by, 'verified_at': timestamp(utcnow())}
        verification = {'version': PARITY_VERIFICATION_VERSION, 'criteria_version': CRITERIA_VERSION, **scope,
                        'source_commit': commit, 'bundle_manifest_sha256': args.manifest_sha256,
                        'config_sha256': stable_id(manifest['provenance']['config']),
                        'criteria': checks, 'failures': failures, 'images': images,
                        'configuration_hash': config['configuration_hash'], 'normalization_ref': normalization_ref,
                        'activation_allowed': False, 'G3_accepted': False}
        (root / 'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n')
        record = {'parity_version': PARITY_VERSION, **scope, 'status': 'FAIL' if failures else 'PASS',
                  'evidence': [parity_verification_ref(verification)]}
        (root / 'parity.json').write_text(json.dumps(record, indent=2) + '\n')
        emit('parity-complete', status=record['status'], failures=len(failures), record=str(root / 'parity.json'))
    except subprocess.CalledProcessError as exc:
        if sandbox.kubeconfig.exists():
            sandbox.diagnostics()
        emit('command-failed', operation=Path(exc.cmd[0]).name, code=exc.returncode)
        raise
    finally:
        sandbox.cleanup()
        emit('owned-sandbox-cleaned', cluster=sandbox.name)


if __name__ == '__main__':
    main()
