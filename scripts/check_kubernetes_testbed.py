"""Real business services on exclusive kind; observational source handoff only.

No training corpus, known-good approval, H4 grant, rollback or G6 acceptance.
The one full RED bucket is not a five-minute feature or three-bucket certificate.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
from uuid import uuid4

import httpx
import yaml

from scripts.check_rollback_kubernetes import PROJECT, Sandbox, command
from scripts.kubernetes_testbed import NAMESPACE, SERVICES, render
from sentinelops.contracts import utc_time
from sentinelops.remediation.domain import Policy
from sentinelops.remediation.kubernetes import LocalKubernetes, _owner
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.kubernetes_evidence import KubernetesIncidentEvidence, verify_kubernetes_binding
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import PrometheusSource, REQUESTS
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.trace_evidence import validate_trace
from sentinelops.trace_query import decode_v3_trace

BODY = {'sku': 'sku-001', 'quantity': 1, 'amount': 10.0, 'currency': 'USD'}


def trace_identities(body, inventory):
    found = {service: set() for service in SERVICES}
    for line in body.splitlines():
        if not line.strip(): continue
        frame = json.loads(line)
        if 'error' in frame: raise ValueError('Jaeger returned an error')
        for resource in frame['result'].get('resourceSpans', []):
            attrs = {a['key']: a['value'].get('stringValue')
                     for a in resource.get('resource', {}).get('attributes', [])}
            service = attrs.get('service.name')
            if service not in found: raise ValueError('unexpected trace service')
            expected = inventory[service]
            if (attrs.get('k8s.pod.uid') != expected['pod_uid']
                or attrs.get('service.instance.id') != expected['pod_uid']
                or attrs.get('k8s.pod.name') != expected['pod_name']
                or attrs.get('k8s.namespace.name') != NAMESPACE):
                raise ValueError('trace resource does not match observed Kubernetes pod')
            found[service].add(attrs['k8s.pod.uid'])
    if any(len(values) != 1 for values in found.values()):
        raise ValueError('incomplete trace pod identities')
    return {service: next(iter(values)) for service, values in found.items()}


def pod_samples(body, expected, *, as_of):
    if body.get('status') != 'success' or body.get('warnings') or body.get('data', {}).get('resultType') != 'vector':
        raise ValueError('pod metric query unavailable')
    rows = body['data'].get('result', [])
    if len(rows) != 1: raise ValueError('ambiguous or missing business metric pod identity')
    row = rows[0];labels = row['metric'];clock, count = row['value']
    if (labels.get('k8s_pod_uid') != expected['pod_uid'] or labels.get('k8s_namespace_name') != NAMESPACE
        or float(clock) > as_of.timestamp() or not math.isfinite(float(count)) or float(count) <= 0):
        raise ValueError('business metric does not match observed Kubernetes pod/clock/traffic')
    return float(count)


class BusinessSandbox(Sandbox):
    def __init__(self, root, kind):
        super().__init__(root, kind)
        self.forwards = []

    def create(self):
        super().create()
        self.docker('update', '--cpus', str(min(4, os.cpu_count() or 1)), '--memory', '4g',
                    '--memory-swap', '4g', self.nodes[0])

    def application(self):
        _, base = self.lock_image('python:3.11-slim')
        original = (PROJECT / 'docker/Dockerfile.service').read_text()
        if not original.startswith('FROM python:3.11-slim\n'):
            raise ValueError('application Dockerfile base requires explicit review')
        dockerfile = self.root / 'application.Dockerfile'
        dockerfile.write_text(original.replace('FROM python:3.11-slim\n', 'FROM ' + base + '\n', 1))
        tag = f'localhost:{self.port}/sentinelops-business:observed'
        print(json.dumps({'stage': 'building-real-business-services', 'base': base}), flush=True)
        self.docker('build', '-f', str(dockerfile), '-t', tag, str(PROJECT))
        self.docker('push', tag)
        digests = json.loads(self.docker('image', 'inspect', '--format', '{{json .RepoDigests}}', tag))
        return next(ref for ref in digests if ref.startswith(f'localhost:{self.port}/sentinelops-business@sha256:'))

    def forward(self, service, port):
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0));local = reservation.getsockname()[1]
        log = (self.root / (service + '-forward.log')).open('xb')
        try:
            child = subprocess.Popen(['kubectl', '--kubeconfig', str(self.kubeconfig), '--context', 'kind-' + self.name,
                '-n', NAMESPACE, 'port-forward', '--address', '127.0.0.1', 'service/' + service, f'{local}:{port}'],
                stdout=log, stderr=log)
        finally: log.close()
        self.forwards.append(child)
        return f'http://127.0.0.1:{local}'

    def inventory(self):
        result = {}
        for service in SERVICES:
            deployment = json.loads(self.kubectl('-n', NAMESPACE, 'get', 'deployment', service, '-o', 'json'))
            replicas = json.loads(self.kubectl('-n', NAMESPACE, 'get', 'replicasets', '-l', 'app=' + service, '-o', 'json'))['items']
            pods = json.loads(self.kubectl('-n', NAMESPACE, 'get', 'pods', '-l', 'app=' + service, '-o', 'json'))['items']
            if len(pods) != 1: raise ValueError('ambiguous business pod inventory')
            pod = pods[0]
            owners = [r['metadata']['uid'] for r in replicas if _owner(r, deployment['metadata']['uid'], 'Deployment')]
            statuses = pod.get('status', {}).get('containerStatuses', [])
            if (not any(_owner(pod, uid, 'ReplicaSet') for uid in owners) or len(statuses) != 1
                or statuses[0].get('ready') is not True or not statuses[0].get('imageID')
                or pod['metadata'].get('deletionTimestamp')):
                raise ValueError('business pod is not a ready child of its Deployment')
            result[service] = {'deployment_uid': deployment['metadata']['uid'],
                'generation': deployment['metadata']['generation'], 'pod_uid': pod['metadata']['uid'],
                'pod_name': pod['metadata']['name'], 'namespace': pod['metadata']['namespace'],
                'image': deployment['spec']['template']['spec']['containers'][0]['image'],
                'image_id': statuses[0]['imageID']}
        return result

    def cleanup(self):
        for child in self.forwards:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(timeout=10)
        super().cleanup()


async def verify(sandbox, endpoint, target, images):
    store = ArtifactStore(sandbox.root / 'source')
    kube = LocalKubernetes(endpoint, target, token_file=sandbox.root / 'observer-token', ca_file=sandbox.root / 'ca.crt')
    checkout, prometheus, jaeger = [sandbox.forward(s, p) for s, p in
        [('checkout-service', 8000), ('prometheus', 9090), ('jaeger', 16686)]]
    source = PrometheusSource(prometheus)
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            for url in (checkout + '/ready', prometheus + '/-/ready', jaeger + '/api/v3/services'):
                for _ in range(60):
                    try:
                        response = await client.get(url);response.raise_for_status();break
                    except httpx.HTTPError: await asyncio.sleep(.5)
                else: raise RuntimeError('local Kubernetes port-forward/readiness unavailable')
            configuration = await asyncio.to_thread(source.verify_configuration)
            await kube.require_observer()
            before = await kube.observe();before.validate(Policy(target))
            if not before.ready_for(images['application'], prior_generation=0, old_pods=()):
                raise ValueError('real Payment deployment is not ready')
            inventory = await asyncio.to_thread(sandbox.inventory)
            start = datetime.now(timezone.utc)
            # First wholly observed minute; do not shorten the frozen source clock.
            end = datetime.fromtimestamp((int(start.timestamp()) // 60 + 2) * 60, timezone.utc)
            cutoff = end + timedelta(seconds=38)
            requests = []
            print(json.dumps({'stage': 'observing-real-business-workload', 'bucket_end': timestamp(end),
                              'capture_after': timestamp(cutoff)}), flush=True)
            while datetime.now(timezone.utc) < cutoff:
                trace, correlation = uuid4().hex, 'b-kube-' + uuid4().hex
                response = await client.post(checkout + '/v1/checkout', json=BODY,
                    headers={'traceparent': '00-' + trace + '-' + uuid4().hex[:16] + '-01',
                             'x-correlation-id': correlation})
                if response.status_code != 200 or response.headers.get('x-trace-id') != trace:
                    raise ValueError('real Kubernetes checkout failed')
                requests.append({'trace_id': trace, 'correlation_id': correlation, 'status': response.status_code,
                                 'completed_at': timestamp(datetime.now(timezone.utc))})
                await asyncio.sleep(.7)
            request_ref = store.write('raw', {'capture_version': 'kubernetes-business-requests/v1', 'requests': requests})
            captures = [await asyncio.to_thread(source.capture, service, end) for service in SERVICES]
            manifest = process_captures(captures, store, environment='local')
            if manifest['observation_count'] != 3 or manifest['quarantine_refs']:
                raise ValueError('real Kubernetes RED captures could not be decoded')
            pod_refs = {}
            for service in SERVICES:
                method, route = BUSINESS_ROUTES[service]
                selector = ','.join(k + '=' + json.dumps(v) for k, v in
                                    [('service', service), ('method', method), ('route', route)])
                expression = 'sum by (k8s_pod_uid,k8s_namespace_name) (increase(' + REQUESTS + '{' + selector + '}[60s]))'
                response = await client.get(prometheus + '/api/v1/query', params={'query': expression, 'time': end.timestamp()})
                response.raise_for_status();body = response.json()
                count = pod_samples(body, inventory[service], as_of=end)
                capture = next(c for c in captures if c['service'] == service)
                aggregate = float(capture['queries']['requests']['response']['data']['result'][0]['value'][1])
                if not math.isclose(count, aggregate, rel_tol=1e-8, abs_tol=1e-8):
                    raise ValueError('pod request count differs from RED source aggregate')
                pod_refs[service] = store.write('raw', {'capture_version': 'kubernetes-pod-series/v1', 'service': service,
                    'expression': expression, 'query_at': timestamp(end), 'response': body,
                    'observed_at': timestamp(datetime.now(timezone.utc))})
            # Reuse native timing/parentage/status validation, then independently
            # preserve resource attributes the RCA decoder intentionally omits.
            selected = next(r for r in reversed(requests) if utc_time(r['completed_at']) <= end)
            for _ in range(60):
                response = await client.get(jaeger + '/api/v3/traces/' + selected['trace_id'])
                try:
                    response.raise_for_status()
                    validate_trace(decode_v3_trace(response.text, selected['trace_id']), selected['trace_id'],
                        selected['correlation_id'], {s: 200 for s in SERVICES}, require_timing=True)
                    trace_identities(response.text, inventory);break
                except (ValueError, KeyError, httpx.HTTPError): await asyncio.sleep(.5)
            else: raise ValueError('real distributed trace lacks matching pod identities/timing/parentage')
            trace_ref = store.write('raw', {'capture_version': 'kubernetes-trace-resource/v1',
                'trace_id': selected['trace_id'], 'body': response.text,
                'observed_at': timestamp(datetime.now(timezone.utc))})
            log_refs = {}
            for service in SERVICES:
                raw = await asyncio.to_thread(sandbox.kubectl, '-n', NAMESPACE, 'logs', 'deployment/' + service, '--tail=500')
                matched = []
                for line in raw.decode().splitlines():
                    try: record = json.loads(line)
                    except ValueError: continue
                    if record.get('event') == 'request_completed' and record.get('correlation_id') == selected['correlation_id']:
                        matched.append(record)
                if (len(matched) != 1 or matched[0].get('k8s.pod.uid') != inventory[service]['pod_uid']
                    or matched[0].get('k8s.namespace.name') != NAMESPACE or matched[0].get('trace_id') != selected['trace_id']):
                    raise ValueError('business log identity does not resolve the observed pod/trace')
                log_refs[service] = store.write('raw', {'capture_version': 'kubernetes-business-log/v1', 'records': matched})
            after = await kube.observe();after.validate(Policy(target))
            if before.generation != after.generation or before.image != after.image or before.pods != after.pods:
                raise ValueError('Payment rollout changed during source observations')
            if inventory != await asyncio.to_thread(sandbox.inventory):
                raise ValueError('business pod inventory changed during observations')
            # Exercise the production read-only collector against these actual
            # services/API/metric counters. This source-only fixture has no PG
            # acceptance step and cannot grant model/action authority.
            policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_text())
            health = HealthWorker(None, store, policy, endpoint=prometheus, environment='local',
                                  configuration_hash=configuration['configuration_hash'])
            batch_ref = store.write('manifests', {'identity':health.identity, 'stream_id':health.stream_id,
                'bucket_end':timestamp(end), 'normalization_manifest_ref':manifest['manifest_ref']})
            state_ref = store.write('manifests', {'last_batch_ref':batch_ref})
            incident_kube = LocalKubernetes(endpoint, target, token_file=sandbox.root / 'observer-token',
                                           ca_file=sandbox.root / 'ca.crt')
            collector = KubernetesIncidentEvidence(incident_kube, {s:inventory[s]['deployment_uid'] for s in SERVICES},
                prometheus_endpoint=prometheus, jaeger_endpoint=jaeger)
            try:
                captured_ref = await collector.capture(store, source_state_ref=state_ref,
                    job_id=stable_id({'source-only':target.key}), intent_ref=state_ref,
                    group={'first_onset_at':timestamp(end-timedelta(seconds=60))},
                    policy=json.loads((PROJECT / 'configs/rca_policy.json').read_text()),
                    health_identity=health.identity, latency_ms=policy['latency_ms'])
                verified = verify_kubernetes_binding(store, store.read(captured_ref), health.identity)
                if any(verified[s]['pod_uid'] != inventory[s]['pod_uid'] for s in SERVICES):
                    raise ValueError('incident collector selected different physical business Pods')
            finally:
                await collector.close()
            return {'status': 'PASS', 'scope': 'actual-local-kubernetes-business-observations', 'images': images,
                'incident_capture_ref':captured_ref, 'accepted_incident_journal':False,
                'source_profile': captures[0]['source_profile'], 'prometheus_configuration': configuration,
                'request_count': len(requests), 'request_ref': request_ref, 'bucket_end': timestamp(end),
                'normalization_manifest_ref': manifest['manifest_ref'], 'pod_metric_refs': pod_refs,
                'trace_ref': trace_ref, 'log_refs': log_refs, 'pod_inventory': inventory,
                'payment_before': before.projection(), 'payment_after': after.projection(),
                'identity_observations_consistent': True, 'physical_binding_accepted': False,
                'source_parity_accepted': False, 'known_good_approved': False, 'H4_accepted': False,
                'G6_accepted': False, 'training_evidence': False, 'model_inference_available': False,
                'diagnostic_accuracy_claim': False}
    finally:
        source.close();await kube.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', default=shutil.which('kind'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.kind: parser.error('an explicit installed kind binary is required')
    running = subprocess.run(['pgrep', '-f', '[p]ython.*capture_local_(campaign|corpus)'], stdout=subprocess.DEVNULL)
    if running.returncode != 1: raise RuntimeError('refuse shared-host workload during an active/unknown A capture')
    root = args.output.resolve()
    if not root.is_relative_to((PROJECT / 'artifacts/lane-b').resolve()):
        raise ValueError('business Kubernetes output must stay in the owned ignored B root')
    root.mkdir(parents=True, exist_ok=False)
    sandbox = BusinessSandbox(root, args.kind)
    files = ['scripts/check_kubernetes_testbed.py', 'scripts/kubernetes_testbed.py', 'scripts/check_rollback_kubernetes.py',
             'services/common/observability.py', 'services/common/runtime.py', 'docker/Dockerfile.service',
             'infra/local/init.sql', 'observability/otel-collector-config.yaml', 'observability/prometheus.yml',
             'pyproject.toml', 'infra/kubernetes/payment-rollback-rbac.yaml',
             'infra/kubernetes/payment-quarantine-observer-rbac.yaml']
    files += [f'services/{name}/app.py' for name in ('checkout', 'payment', 'inventory')]
    hashes = {p: hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() for p in files}
    try:
        sandbox.create()
        images = {key: sandbox.lock_image(image)[1] for key, image in {
            'postgres': 'postgres:16-alpine', 'otel-collector': 'otel/opentelemetry-collector:0.162.0',
            'prometheus': 'prom/prometheus:v3.5.0', 'jaeger': 'cr.jaegertracing.io/jaegertracing/jaeger:2.21.0'}.items()}
        images['application'] = sandbox.application()
        for image in images.values(): sandbox.docker('exec', sandbox.nodes[0], 'crictl', 'pull', image)
        password = secrets.token_urlsafe(32)
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'commerce-credentials', 'namespace': NAMESPACE},
                  'type': 'Opaque', 'stringData': {'password': password,
                    'database-url': f'postgresql://sentinel:{password}@postgres:5432/sentinelops'}}
        # Secret goes only to the explicitly owned private API, never to artifacts/logs.
        sandbox.kubectl('apply', '-f', '-', data=json.dumps(secret).encode())
        documents = render(images)
        (root / 'images.json').write_text(json.dumps(images, indent=2))
        (root / 'manifests.yaml').write_text(yaml.safe_dump_all(documents, sort_keys=False))
        sandbox.kubectl('apply', '-f', str(root / 'manifests.yaml'))
        for service in ('postgres', 'jaeger', 'otel-collector', 'prometheus', *SERVICES):
            sandbox.kubectl('-n', NAMESPACE, 'rollout', 'status', 'deployment/' + service, '--timeout=180s')
        endpoint, target = sandbox.identity()
        report = asyncio.run(verify(sandbox, endpoint, target, images))
        report['observer_commit'] = command(['git', 'rev-parse', 'HEAD']).decode().strip()
        report['observer_worktree_dirty'] = bool(command(['git', 'status', '--porcelain']).strip())
        report['loaded_source_sha256'] = hashes
        report['observer_changed_during_run'] = any(hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() != h for p, h in hashes.items())
        (root / 'verification.json').write_text(json.dumps(report, indent=2))
        print('LOCAL KUBERNETES BUSINESS OBSERVATIONS: PASS ' + str(root / 'verification.json'))
    except Exception:
        if sandbox.kubeconfig.exists(): sandbox.diagnostics()
        raise
    finally: sandbox.cleanup()


if __name__ == '__main__': main()
