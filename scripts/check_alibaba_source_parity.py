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
from uuid import uuid4

import httpx

from scripts.check_kubernetes_testbed import BusinessSandbox, pod_samples
from scripts.kubernetes_testbed import NAMESPACE, SERVICES
from scripts.run_s2_h4 import clean_commit, deploy, emit, wait_ready
from sentinelops.experiments.traffic import PATHS
from sentinelops.ml.release import PARITY_VERIFICATION_VERSION, PARITY_VERSION, parity_verification_ref
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import REQUESTS, SOURCE_PROFILE_V2, PrometheusSource, mean_latency_ms, queries
from sentinelops.telemetry.store import ArtifactStore

CRITERIA_VERSION = 'alibaba-parity-criteria/v1'
FIRST_AGE = 35
COUNT_TOLERANCE = .05
MEAN_TOLERANCE = .10
PROJECT = Path(__file__).resolve().parents[1]


def utcnow():
    return datetime.now(timezone.utc)


async def drive(client, targets, stop):
    rows = []

    async def request(service, url):
        trace, correlation = uuid4().hex, 'parity-' + uuid4().hex
        method, _ = BUSINESS_ROUTES[service]
        body = ({'sku': 'sku-001', 'quantity': 1, 'amount': 9.9} if service == 'checkout-service'
                else {'order_id': 'direct-' + uuid4().hex, 'amount': 9.9, 'currency': 'USD'} if service == 'payment-service' else None)
        started = utcnow()
        try:
            response = await client.request(method, url + PATHS[service], json=body,
                headers={'x-correlation-id': correlation, 'traceparent': f'00-{trace}-{uuid4().hex[:16]}-01'})
            failed = (response.status_code != 200 or response.headers.get('x-trace-id') != trace
                      or response.headers.get('x-correlation-id') != correlation)
        except httpx.HTTPError:
            failed = True
        # A failed request is recorded and fails P4 for its minute; it never aborts the run
        # before evidence is written (Codex review).
        rows.append({'request_entry_service': service, 'trace_id': trace, 'correlation_id': correlation,
                     'started_at': timestamp(started), 'completed_at': timestamp(utcnow()), 'failed': failed})

    loop = asyncio.get_running_loop()
    tick = loop.time()
    while utcnow() < stop:
        await asyncio.gather(*(request(s, u) for s, u in targets.items()))
        tick += 1
        await asyncio.sleep(max(0, tick - loop.time()))
    return rows


def exact_logs(sandbox, inventory, rows, ends):
    """Server completions in the run, each marked failed when it is not a clean 200 resolved to
    its client, pod and route; client-side failures are added as failed records at their time."""
    start_ms, stop_ms = int((ends[0] - timedelta(minutes=1)).timestamp() * 1000), int(ends[-1].timestamp() * 1000)
    clients = {row['correlation_id']: row for row in rows}
    selected = []
    for row in rows:
        at = int(datetime.fromisoformat(row['completed_at'].replace('Z', '+00:00')).timestamp() * 1000)
        if row['failed'] and start_ms < at <= stop_ms:
            selected.append({'service': row['request_entry_service'], 'timestamp_unix_ms': at, 'failed': True,
                             'reason': 'client-request-failed'})
    for service in SERVICES:
        method, route = BUSINESS_ROUTES[service]
        for line in sandbox.kubectl('-n', NAMESPACE, 'logs', 'deployment/' + service).decode().splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if (item.get('event') != 'request_completed' or (item.get('method'), item.get('route')) != (method, route)
                    or not start_ms < item.get('timestamp_unix_ms', -1) <= stop_ms):
                continue
            client = clients.get(item.get('correlation_id'))
            duration = item.get('duration_ms')
            item['failed'] = (client is None or item.get('trace_id') != client['trace_id']
                              or item.get('k8s.pod.uid') != inventory[service]['pod_uid'] or item.get('status_code') != 200
                              or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0)
            item['service'] = service
            selected.append(item)
    return selected


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
            try:
                aggregate = float(capture['queries']['requests']['response']['data']['result'][0]['value'][1])
            except (KeyError, IndexError, TypeError, ValueError):
                # Prometheus failed this query; PrometheusSource kept the partial capture so the
                # pipeline quarantines it and evaluate() records FAIL (Codex review): no crash here.
                continue
            method, route = BUSINESS_ROUTES[service]
            selector = ','.join(k + '=' + json.dumps(v) for k, v in [('service', service), ('method', method), ('route', route)])
            expression = 'sum by (k8s_pod_uid,k8s_namespace_name) (increase(' + REQUESTS + '{' + selector + '}[60s]))'
            try:
                body = (await client.get(source.endpoint + '/api/v1/query',
                                         params={'query': expression, 'time': end.timestamp()})).raise_for_status().json()
                pod_count = pod_samples(body, inventory[service], as_of=end)
                verified = math.isclose(pod_count, aggregate, rel_tol=1e-8, abs_tol=1e-8)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
                verified = False
            # Recorded and checked in P2: a capture that does not resolve to the observed pod fails
            # the run with evidence instead of aborting it.
            capture['pod_identity_verified'] = verified
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
        recorded = {k: v['expression'] for k, v in raw['queries'].items()}
        check('P2', raw['source_profile'] == SOURCE_PROFILE_V2 and recorded == expected
              and raw.get('pod_identity_verified') is True,
              {'service': raw['service'], 'bucket_end': raw['bucket_end'], 'source_profile': raw['source_profile'],
               'queries_sha256': stable_id(recorded), 'pod_identity_verified': raw.get('pod_identity_verified')})
    store = ArtifactStore(root / 'source')
    normalized = process_captures(captures, store)
    observations = [store.read(ref) for ref in normalized['observation_refs']]
    check('P3', normalized['observation_count'] == len(ends) * len(SERVICES) and not normalized['quarantine_refs'],
          {'what': 'all buckets normalized', 'observations': normalized['observation_count']})
    for service in SERVICES:
        minutes = {}
        for end in ends:
            obs = next((o for o in observations if o['service'] == service and o['bucket_end'] == timestamp(end)), None)
            raw = next((c for c in captures if c['service'] == service and c['bucket_end'] == timestamp(end)), None)
            if obs is None or raw is None:
                # A quarantined or missing capture is a parity FAIL, recorded, never a crash (Codex review).
                missing = {'service': service, 'bucket_end': timestamp(end), 'observation_missing': obs is None,
                           'capture_missing': raw is None}
                for name in ('P3', 'P4', 'P5'):
                    check(name, False, missing)
                continue
            lag = (datetime.fromisoformat(obs['available_at'].replace('Z', '+00:00')) - end).total_seconds()
            check('P3', obs['quality']['eligible_for_inference'] and obs['quality']['coverage_ratio'] == 1
                  and lag <= policy['maximum_lag_seconds'],
                  {'service': service, 'bucket_end': timestamp(end), 'lag_seconds': lag,
                   'coverage': obs['quality']['coverage_ratio'],
                   'eligible_for_inference': obs['quality']['eligible_for_inference'],
                   'maximum_lag_seconds': policy['maximum_lag_seconds']})
            window = [r for r in logs if r['service'] == service
                      and (end.timestamp() - 60) * 1000 < r['timestamp_unix_ms'] <= end.timestamp() * 1000]
            records = [r for r in window if not r.get('failed')]
            failed_requests = len(window) - len(records)
            count = obs['measurements']['request_count'] or 0
            relative = abs(count / len(records) - 1) if records else None
            check('P4', relative is not None and relative <= COUNT_TOLERANCE and obs['measurements']['error_count'] == 0
                  and failed_requests == 0,
                  {'service': service, 'bucket_end': timestamp(end), 'prometheus_count': count,
                   'server_completions': len(records), 'relative_difference': relative,
                   'error_count': obs['measurements']['error_count'], 'failed_requests': failed_requests})
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


def preflight_failure(reason, config=None):
    """Readiness or source configuration not established: P2 cannot be shown, so nothing is measured."""
    return failure_checks('P2', reason, None), [{'criterion': 'P2', 'reason': reason}], None, config


async def measure(sandbox, root, bundle_parts, policy):
    targets = {s: sandbox.forward(s, 8000) for s in SERVICES}
    source = PrometheusSource(sandbox.forward('prometheus', 9090))
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            try:
                for url in (*[t + '/ready' for t in targets.values()], source.endpoint + '/-/ready'):
                    await wait_ready(client, url)
            except Exception as exc:
                return preflight_failure('readiness check failed: ' + str(exc))
            try:
                config = await asyncio.to_thread(source.verify_configuration)
            except Exception as exc:
                return preflight_failure('Prometheus configuration preflight failed: ' + str(exc))
            try:
                inventory = await asyncio.to_thread(sandbox.inventory)
            except Exception as exc:
                return preflight_failure('sandbox inventory preflight failed: ' + str(exc), config)
            first_end = (int(utcnow().timestamp()) // 60 + 2) * 60
            count = minute_count(bundle_parts[0]['provenance']['config'])
            ends = [datetime.fromtimestamp(first_end + m * 60, timezone.utc) for m in range(count)]
            captures, rows = await asyncio.gather(capture_buckets(client, source, ends, inventory),
                                                  drive(client, targets, ends[-1] + timedelta(seconds=39)))
            stable = inventory == await asyncio.to_thread(sandbox.inventory)
            logs = await asyncio.to_thread(exact_logs, sandbox, inventory, rows, ends)
            checks, failures, normalization_ref = evaluate(root, bundle_parts, captures, logs, ends, policy)
            if not stable:
                # A pod restart during sampling fails the run with evidence instead of aborting it.
                failures.append({'criterion': 'P2', 'reason': 'workload pod identity changed while sampling'})
            return checks, failures, normalization_ref, config
    finally:
        source.close()


def write_evidence(root, scope, commit, manifest, manifest_sha256, checks, failures, *, images=None, config=None,
                   normalization_ref=None):
    """Write the producer schema for both complete runs and early, non-certifying failures."""
    verification = {'version': PARITY_VERIFICATION_VERSION, 'criteria_version': CRITERIA_VERSION, **scope,
                    'source_commit': commit, 'bundle_manifest_sha256': manifest_sha256,
                    'config_sha256': stable_id(manifest['provenance']['config']) if manifest else None,
                    'criteria': checks, 'failures': failures, 'images': images or [],
                    'configuration_hash': config.get('configuration_hash') if config else None,
                    'scrape_interval': config.get('scrape_interval') if config else None,
                    'normalization_ref': normalization_ref,
                    'activation_allowed': False, 'G3_accepted': False}
    (root / 'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n')
    record = {'parity_version': PARITY_VERSION, **scope, 'status': 'FAIL' if failures else 'PASS',
              'evidence': [parity_verification_ref(verification)]}
    (root / 'parity.json').write_text(json.dumps(record, indent=2) + '\n')
    return verification, record


def failure_checks(criterion, reason, manifest):
    detail = {'ok': False, 'reason': reason}
    if criterion == 'P1':
        detail['model_version'] = manifest['model_version'] if manifest else None
    return {criterion: [detail]}


def write_failure(root, scope, commit, manifest, manifest_sha256, criterion, reason, **extra):
    """Record a run that stopped before all criteria could be measured; it never certifies."""
    _, record = write_evidence(root, scope, commit, manifest, manifest_sha256,
                               failure_checks(criterion, reason, manifest),
                               [{'criterion': criterion, 'reason': reason}], **extra)
    emit('parity-complete', status=record['status'], failures=1, record=str(root / 'parity.json'))


def abort_reason(exc):
    if isinstance(exc, subprocess.CalledProcessError):
        # Command arguments stay out of the evidence; the operation name and exit code identify the step.
        return f'run aborted: command {Path(exc.cmd[0]).name} exited {exc.returncode}'
    return f'run aborted: {type(exc).__name__}: {exc}'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True, help='From a separate trusted channel')
    parser.add_argument('--environment', required=True)
    parser.add_argument('--verified-by', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Evidence must name a clean commit; a dirty checkout is refused before any output exists.
    commit = clean_commit()
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    load_error = None
    try:
        bundle_parts = verify_bundle(args.bundle.resolve(strict=True), args.manifest_sha256)
    except Exception as exc:  # unreadable bundle or manifest hash mismatch is a P1 failure, not a crash
        bundle_parts, load_error = (None, None, None, False), f'{type(exc).__name__}: {exc}'
    manifest = bundle_parts[0]
    model_version = manifest['model_version'] if manifest else None
    scope = {'candidate_id': model_version, 'environment': args.environment,
             'source_profile': SOURCE_PROFILE_V2,
             'models': [{'service': s, 'model_version': model_version} for s in sorted(BUSINESS_ROUTES)],
             'verified_by': args.verified_by, 'verified_at': timestamp(utcnow())}
    if not bundle_parts[3]:
        reason = 'bundle verification failed' + (': ' + load_error if load_error else '') + '; no sandbox started'
        write_failure(root, scope, commit, manifest, args.manifest_sha256, 'P1', reason)
        raise SystemExit('P1: bundle does not verify; no sandbox started')
    try:
        policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_text())
        sandbox = BusinessSandbox(root, args.kind)
    except Exception as exc:
        write_failure(root, scope, commit, manifest, args.manifest_sha256, 'P2', abort_reason(exc))
        raise

    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    images, written = None, False
    try:
        images = deploy(sandbox)
        checks, failures, normalization_ref, config = asyncio.run(measure(sandbox, root, bundle_parts, policy))
        _, record = write_evidence(root, scope, commit, manifest, args.manifest_sha256, checks, failures,
                                   images=images, config=config, normalization_ref=normalization_ref)
        written = True
        emit('parity-complete', status=record['status'], failures=len(failures), record=str(root / 'parity.json'))
        if failures:
            raise SystemExit('source parity failed; evidence written to ' + str(root))
    except (Exception, KeyboardInterrupt) as exc:
        # Deployment, measurement or an interrupt that stops the run still leaves FAIL evidence.
        if not written:
            write_failure(root, scope, commit, manifest, args.manifest_sha256, 'P2', abort_reason(exc), images=images)
        if isinstance(exc, subprocess.CalledProcessError):
            if sandbox.kubeconfig.exists():
                sandbox.diagnostics()
            emit('command-failed', operation=Path(exc.cmd[0]).name, code=exc.returncode)
        raise
    finally:
        sandbox.cleanup()
        emit('owned-sandbox-cleaned', cluster=sandbox.name)


if __name__ == '__main__':
    main()
