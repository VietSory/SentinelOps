"""Target-environment source parity for an H1 candidate → `model-source-parity/v1`.

Runs the real services on an owned local Kubernetes sandbox with healthy traffic
for five complete minutes, then checks predeclared criteria against the frozen
fitting corpus and the candidate. The record is PASS only when every criterion
passes; otherwise it is FAIL with the failed checks. It never writes a release,
approval or activation and removes all owned resources on exit.

Criteria (fixed before any measurement):
  C1 candidate inventory verifies and development predictions replay on this host;
  C2 every raw capture has the same source profile, queries and latency bucket
     bounds as the frozen training captures of the same service;
  C3 all five buckets per service are eligible with full scrape coverage and a
     receipt lag within the frozen SLO health policy `maximum_lag_seconds`;
  C4 Prometheus request counts match the independent server-completion ledger
     within 5% per bucket (increase() extrapolation), with no 5xx;
  C5 the histogram p95 and the exact-log p95 fall in the same histogram bucket;
  C6 a complete five-minute FeatureWindow is eligible at its live cutoff (and
     masked before it), the feature schema matches the bundle, and candidate
     scores are finite.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
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
from scripts.verify_model_candidate import verify_candidate
from sentinelops.contracts import utc_time
from sentinelops.experiments.traffic import PATHS
from sentinelops.ml.local_data import _inside
from sentinelops.ml.release import PARITY_VERSION, _audit
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_KEYS, FEATURE_SET, build_feature_window, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import REQUESTS, SOURCE_PROFILE, PrometheusSource
from sentinelops.telemetry.store import ArtifactStore

FIRST_AGE = 35
COUNT_TOLERANCE = .05
PROJECT = Path(__file__).resolve().parents[1]


def utcnow():
    return datetime.now(timezone.utc)


def capture_shape(raw):
    return {'profile': raw['source_profile'],
            'queries': {key: value['expression'] for key, value in raw['queries'].items()},
            'latency_bounds_ms': sorted((x['metric']['le'] for x in raw['queries']['buckets']['response']['data']['result']),
                                        key=float)}


def training_shapes(corpus):
    """Shapes of the frozen corpus's own training healthy raw captures, per service."""
    corpus = Path(corpus)
    freeze = json.loads((corpus / 'dataset-freeze.json').read_text())
    shapes = {s: set() for s in BUSINESS_ROUTES}
    for run_path in sorted(corpus.glob('runs/train-*-healthy/captures/run-*/run.json')):
        run = json.loads(run_path.read_text())
        store = ArtifactStore(run_path.parent / 'serving')
        manifest = store.read(run['source_manifest_ref'])
        for ref in store.read(manifest['normalization_manifest_ref'])['raw_refs'][:6]:
            raw = store.read(ref)
            shapes[raw['service']].add(json.dumps(capture_shape(raw), sort_keys=True))
    if any(len(v) != 1 for v in shapes.values()):
        raise ValueError('frozen training captures do not share one source shape per service')
    return freeze, {s: json.loads(next(iter(v))) for s, v in shapes.items()}


def bucket_of(value, bounds):
    edges = [float(b) for b in bounds if b != '+Inf']
    for lower, upper in zip([0.] + edges, edges):
        if lower <= value <= upper:
            return upper
    return math.inf


async def drive(client, targets, stop):
    rows = []

    async def request(service, url):
        trace, correlation = uuid4().hex, 'parity-' + uuid4().hex
        method, _ = BUSINESS_ROUTES[service]
        body = ({'sku': 'sku-001', 'quantity': 1, 'amount': 9.9} if service == 'checkout-service'
                else {'order_id': 'direct-' + uuid4().hex, 'amount': 9.9, 'currency': 'USD'} if service == 'payment-service' else None)
        started = utcnow()
        response = await client.request(method, url + PATHS[service], json=body,
            headers={'x-correlation-id': correlation, 'traceparent': f'00-{trace}-{uuid4().hex[:16]}-01'})
        if (response.status_code != 200 or response.headers.get('x-trace-id') != trace
                or response.headers.get('x-correlation-id') != correlation):
            raise ValueError(f'unexpected healthy response on {service}: {response.status_code}')
        rows.append({'request_entry_service': service, 'trace_id': trace, 'correlation_id': correlation,
                     'started_at': timestamp(started), 'completed_at': timestamp(utcnow())})

    loop = asyncio.get_running_loop()
    tick = loop.time()
    while utcnow() < stop:
        await asyncio.gather(*(request(s, u) for s, u in targets.items()))
        tick += 1
        await asyncio.sleep(max(0, tick - loop.time()))
    return rows


async def capture_buckets(client, source, ends, inventory):
    captures = []
    for end in ends:
        await asyncio.sleep(max(0, (end + timedelta(seconds=FIRST_AGE) - utcnow()).total_seconds()))
        current = await asyncio.gather(*(asyncio.to_thread(source.capture, s, end) for s in SERVICES))
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


def exact_logs(sandbox, inventory, rows, ends):
    start_ms, stop_ms = int((ends[0] - timedelta(minutes=1)).timestamp() * 1000), int(ends[-1].timestamp() * 1000)
    clients = {row['correlation_id']: row for row in rows}
    selected = []
    for service in SERVICES:
        method, route = BUSINESS_ROUTES[service]
        for line in sandbox.kubectl('-n', NAMESPACE, 'logs', 'deployment/' + service).decode().splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if (item.get('event') != 'request_completed' or (item.get('method'), item.get('route')) != (method, route)
                    or not start_ms < item['timestamp_unix_ms'] <= stop_ms):
                continue
            client = clients.get(item['correlation_id'])
            if (client is None or item['trace_id'] != client['trace_id'] or item['k8s.pod.uid'] != inventory[service]['pod_uid']
                    or item['status_code'] != 200 or not math.isfinite(item['duration_ms']) or item['duration_ms'] < 0):
                raise ValueError('server completion does not resolve to its client, pod, route and status')
            selected.append(item)
    return selected


def evaluate(root, candidate, corpus, captures, logs, ends, config, policy):
    checks, failures = {}, []

    def check(name, ok, detail):
        checks.setdefault(name, []).append({'ok': bool(ok), **detail})
        if not ok:
            failures.append({'criterion': name, **detail})

    freeze, shapes = training_shapes(corpus)
    index = json.loads(_inside(candidate, 'handoff.json').read_bytes())
    check('C2', freeze['source_profile'] == SOURCE_PROFILE == index_profile(candidate, index),
          {'what': 'source profile', 'frozen': freeze['source_profile']})
    check('C2', freeze['feature_set_version'] == FEATURE_SET and freeze['ordered_features'] == list(FEATURE_KEYS),
          {'what': 'feature set and order'})
    for raw in captures:
        check('C2', capture_shape(raw) == shapes[raw['service']], {'service': raw['service'], 'bucket_end': raw['bucket_end']})
    store = ArtifactStore(root / 'source')
    normalized = process_captures(captures, store)
    observations = [store.read(ref) for ref in normalized['observation_refs']]
    check('C3', normalized['observation_count'] == len(ends) * len(SERVICES) and not normalized['quarantine_refs'],
          {'what': 'all buckets normalized', 'observations': normalized['observation_count']})
    cutoff = max(utc_time(c['collected_at']) for c in captures if utc_time(c['bucket_end']) == ends[-1])
    from sentinelops.ml.models import load_bundle
    for model in index['models']:
        service = model['service']
        for end in ends:
            obs = next(o for o in observations if o['service'] == service and utc_time(o['bucket_end']) == end)
            lag = (utc_time(obs['available_at']) - end).total_seconds()
            check('C3', obs['quality']['eligible_for_inference'] and obs['quality']['coverage_ratio'] == 1
                  and lag <= policy['maximum_lag_seconds'],
                  {'service': service, 'bucket_end': timestamp(end), 'lag_seconds': lag, 'coverage': obs['quality']['coverage_ratio']})
            records = [r for r in logs if r['service'] == service
                       and (end.timestamp() - 60) * 1000 < r['timestamp_unix_ms'] <= end.timestamp() * 1000]
            count = obs['measurements']['request_count'] or 0
            relative = abs(count / len(records) - 1) if records else math.inf
            check('C4', records and relative <= COUNT_TOLERANCE and obs['measurements']['error_count'] == 0,
                  {'service': service, 'bucket_end': timestamp(end), 'prometheus_count': count,
                   'server_completions': len(records), 'relative_difference': relative})
            if records and obs['payload']:
                exact = sorted(r['duration_ms'] for r in records)[math.ceil(.95 * len(records)) - 1]
                bounds = shapes[service]['latency_bounds_ms']
                observed = obs['payload']['p95_latency_ms']
                check('C5', bucket_of(observed, bounds) == bucket_of(exact, bounds),
                      {'service': service, 'bucket_end': timestamp(end), 'histogram_p95_ms': observed, 'exact_p95_ms': exact})
        manifest, detector, risk = load_bundle(_inside(candidate, 'runtime/' + model['bundle_directory']),
                                               expected_manifest_sha256=model['manifest_sha256'])
        feature = build_feature_window(observations, service=service, window_end=ends[-1], as_of=cutoff, source_profile=SOURCE_PROFILE)
        masked = build_feature_window(observations, service=service, window_end=ends[-1], as_of=ends[-1], source_profile=SOURCE_PROFILE)
        values = [feature['payload']['features'][k] for k in manifest['ordered_features']] if feature['payload'] else None
        scores = None
        if values is not None:
            detection = detector.predict([values])
            scores = {name: float(item['raw_score'][0]) for name, item in detection.items()}
            scores['xgboost'] = float(risk.predict_proba([values])[0, 1])
        check('C6', feature['quality']['eligible_for_inference'] and feature['quality']['coverage_ratio'] == 1
              and masked['quality']['coverage_ratio'] < 1 and tuple(manifest['ordered_features']) == FEATURE_KEYS
              and scores is not None and all(math.isfinite(v) for v in scores.values()),
              {'service': service, 'model_version': model['model_version'], 'scores': scores,
               'live_cutoff': timestamp(cutoff)})
    return checks, failures, normalized['manifest_ref']


def index_profile(candidate, index):
    return _audit(candidate, index['evaluation_ref'])['source_profile']


async def measure(sandbox, root, candidate, corpus, policy):
    targets = {s: sandbox.forward(s, 8000) for s in SERVICES}
    source = PrometheusSource(sandbox.forward('prometheus', 9090))
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            for url in (*[t + '/ready' for t in targets.values()], source.endpoint + '/-/ready'):
                await wait_ready(client, url)
            config = await asyncio.to_thread(source.verify_configuration)
            inventory = await asyncio.to_thread(sandbox.inventory)
            first_end = (int(utcnow().timestamp()) // 60 + 2) * 60
            ends = [datetime.fromtimestamp(first_end + m * 60, timezone.utc) for m in range(5)]
            captures, rows = await asyncio.gather(capture_buckets(client, source, ends, inventory),
                                                  drive(client, targets, ends[-1] + timedelta(seconds=39)))
            if inventory != await asyncio.to_thread(sandbox.inventory):
                raise ValueError('workload pod identity changed while sampling')
            logs = await asyncio.to_thread(exact_logs, sandbox, inventory, rows, ends)
            return evaluate(root, candidate, corpus, captures, logs, ends, config, policy) + (config,)
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--expected-id', required=True)
    parser.add_argument('--corpus', type=Path, required=True, help='Frozen fitting corpus root of the candidate')
    parser.add_argument('--environment', required=True, help='Name of the target environment being certified')
    parser.add_argument('--verified-by', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    commit = clean_commit()
    candidate = args.candidate.resolve(strict=True)
    replay = verify_candidate(candidate, args.expected_id, replay=True)  # C1
    policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_text())
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    sandbox = BusinessSandbox(root, args.kind)

    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        images = deploy(sandbox)
        checks, failures, normalization_ref, config = asyncio.run(measure(sandbox, root, candidate, args.corpus, policy))
        index = json.loads(_inside(candidate, 'handoff.json').read_bytes())
        verification = {'version': 'model-source-parity-check/v1', 'source_commit': commit, 'candidate_id': args.expected_id,
            'replay': replay, 'criteria': checks, 'failures': failures, 'images': images,
            'configuration_hash': config['configuration_hash'], 'normalization_ref': normalization_ref,
            'activation_allowed': False, 'G3_accepted': False}
        (root / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n')
        record = {'parity_version': PARITY_VERSION, 'candidate_id': args.expected_id, 'environment': args.environment,
            'source_profile': SOURCE_PROFILE,
            'models': [{'service': m['service'], 'model_version': m['model_version']} for m in index['models']],
            'status': 'FAIL' if failures else 'PASS', 'verified_by': args.verified_by,
            'verified_at': timestamp(utcnow()), 'evidence': [str(root / 'verification.json'), 'source_commit:' + commit]}
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
