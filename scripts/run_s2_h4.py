"""Reproducible S2 incident on an owned local Kubernetes sandbox, ending in an H4 assessment.

A real bad Payment image is deployed after an accepted healthy baseline; the
existing health and incident workers persist the incident through a private
control database. With ``--model-store/--model-release-ref`` the incident worker
uses that validated release (capture v6); without them it measures the no-model
path (capture v5). The run ends by recording ``NativeH4Authority.assess``.

It never constructs the rollback executor, approves a known-good image, writes an
approval/release or dispatches. Only owned kind/registry/database resources are
created and all are removed on exit. It refuses to run beside an active corpus
capture, so it cannot perturb acquisition measurements.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
from uuid import uuid4

import asyncpg
import httpx
import yaml

from scripts.check_kubernetes_testbed import BusinessSandbox
from scripts.check_rollback_kubernetes import PROJECT, private_file
from scripts.kubernetes_testbed import NAMESPACE, SERVICES, render
from sentinelops.experiments.traffic import PATHS
from sentinelops.ml.registry import ModelRegistry
from sentinelops.remediation.authority import NativeH4Authority
from sentinelops.remediation.domain import KubernetesUnavailable, Request
from sentinelops.remediation.kubernetes import LocalKubernetes
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.deployment_changes import DeploymentChangeReview
from sentinelops.telemetry.prometheus import PrometheusSource
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.deployment_evidence import DeploymentIncidentEvidence
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.health_source import HealthLedgerReader
from sentinelops.workers.incident import IncidentWorker
from sentinelops.workers.incident_journal import IncidentJournal
from sentinelops.workers.kubernetes_evidence import KubernetesIncidentEvidence
from sentinelops.workers.model_signals import ModelIncidentEvidence

OWNER_LABEL = 'sentinelops.s2-h4'
WRITER_DSN_PREFIX = 'postgresql://control_writer:local-control-writer@127.0.0.1:'
PAYMENT = 'payment-service'
POST_CHANGE_MINUTES = 8
TOKEN_DURATION = '60m'  # sandbox identity() issues 10m tokens; this run can exceed that


def clock():
    return datetime.now(timezone.utc)


def emit(stage, **data):
    print(json.dumps({'stage': stage, 'at': timestamp(clock()), **data}), flush=True)


def ceil_minute(offset=0):
    return datetime.fromtimestamp((int(clock().timestamp()) // 60 + 1 + offset) * 60, timezone.utc)


def clean_commit():
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=PROJECT).strip():
        raise ValueError('S2/H4 evidence requires a clean committed checkout')
    if subprocess.run(['pgrep', '-f', '[p]ython.*capture_local_(campaign|corpus)'], stdout=subprocess.DEVNULL).returncode != 1:
        raise ValueError('refuse to run beside an active corpus capture on this host')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PROJECT).decode().strip()


def deploy(sandbox):
    sandbox.create()
    images = {key: sandbox.lock_image(image)[1] for key, image in {
        'postgres': 'postgres:16-alpine', 'otel-collector': 'otel/opentelemetry-collector:0.162.0',
        'prometheus': 'prom/prometheus:v3.5.0', 'jaeger': 'cr.jaegertracing.io/jaegertracing/jaeger:2.21.0',
    }.items()}
    images['application'] = sandbox.application()
    for image in images.values():
        sandbox.docker('exec', sandbox.nodes[0], 'crictl', 'pull', image)
    password = secrets.token_urlsafe(32)
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'commerce-credentials', 'namespace': NAMESPACE},
              'type': 'Opaque', 'stringData': {'password': password,
              'database-url': f'postgresql://sentinel:{password}@postgres:5432/sentinelops'}}
    sandbox.kubectl('apply', '-f', '-', data=json.dumps(secret).encode())
    sandbox.kubectl('apply', '-f', '-', data=yaml.safe_dump_all(render(images), sort_keys=False).encode())
    for service in ('postgres', 'jaeger', 'otel-collector', 'prometheus', *SERVICES):
        sandbox.kubectl('-n', NAMESPACE, 'rollout', 'status', 'deployment/' + service, '--timeout=120s')
    return images


def bad_image(sandbox, base):
    """Same middleware/metrics/spans; Payment adds 0.95 s after a 130 s delay."""
    context = sandbox.root / 'payment-image-context'
    context.mkdir()
    patch = r"""from pathlib import Path
p = Path('/app/services/payment/app.py')
s = p.read_text()
gate = 'fault = FaultState()'
marker = '    await fault.apply(SERVICE_NAME)'
assert s.count(marker) == s.count(gate) == 1
s = s.replace(gate, 'import asyncio\nimport time\n'+gate+'\n_s2_regression_after = time.monotonic() + 130')
s = s.replace(marker, '    if time.monotonic() >= _s2_regression_after:\n        await asyncio.sleep(0.95)\n'+marker, 1)
p.write_text(s)
"""
    (context / 'Dockerfile').write_text('FROM ' + base + '\nRUN ' + json.dumps(['python', '-c', patch]) + '\n')
    # A real bad deployment is a new version of the SAME repository; the
    # executor only rolls back to a distinct digest of that repository.
    tag = f'localhost:{sandbox.port}/sentinelops-business:s2-regression'
    sandbox.docker('build', '-f', str(context / 'Dockerfile'), '-t', tag, str(context))
    sandbox.docker('push', tag)
    digests = json.loads(sandbox.docker('image', 'inspect', '--format', '{{json .RepoDigests}}', tag))
    image = next(r for r in digests if r.startswith(f'localhost:{sandbox.port}/sentinelops-business@sha256:'))
    sandbox.docker('exec', sandbox.nodes[0], 'crictl', 'pull', image)
    return image


def control_db(sandbox, postgres, owned):
    bootstrap = sandbox.root / 'db-init'
    bootstrap.mkdir(mode=0o755)
    for path in sorted((PROJECT / 'infra/control').glob('00[1-4]-*.sql')):
        shutil.copyfile(path, bootstrap / path.name)
    env = sandbox.root / 'db-bootstrap.env'
    fd = os.open(env, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('POSTGRES_DB=sentinelops_control\nPOSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
    owned.append(sandbox.docker('create', '--name', sandbox.name + '-control', '--label', OWNER_LABEL + '=' + sandbox.name,
        '--memory', '256m', '--cpus', '0.5', '--env-file', str(env), '-p', '127.0.0.1::5432',
        '-v', str(bootstrap) + ':/docker-entrypoint-initdb.d:ro', postgres).decode().strip())
    env.unlink()
    sandbox.docker('start', owned[0])
    address, port = sandbox.docker('port', owned[0], '5432/tcp').decode().strip().split(':')
    if address != '127.0.0.1' or not port.isdecimal():
        raise ValueError('private control database is not loopback-bound')
    return WRITER_DSN_PREFIX + port + '/sentinelops_control'


async def connect_pool(dsn):
    for _ in range(60):
        try:
            return await asyncpg.create_pool(dsn, min_size=1, max_size=4, timeout=2., command_timeout=10.)
        except (OSError, TimeoutError, asyncpg.CannotConnectNowError):
            await asyncio.sleep(.5)
    raise TimeoutError('private control database not ready')


async def wait_ready(client, url):
    for _ in range(60):
        try:
            (await client.get(url)).raise_for_status()
            return
        except httpx.HTTPError:
            await asyncio.sleep(.5)
    raise RuntimeError('private Kubernetes port forward is not ready')


async def drive(client, urls, stop, bad):
    """One request per second per entry; direct Payment/Inventory must stay 200."""
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    grace = deadline + 25
    counts = {s: {} for s in urls}

    async def request(service, endpoint):
        method, _ = BUSINESS_ROUTES[service]
        body = ({'sku': 'sku-001', 'quantity': 1, 'amount': 9.9} if service == 'checkout-service'
                else {'order_id': 'direct-' + uuid4().hex, 'amount': 9.9, 'currency': 'USD'} if service == PAYMENT else None)
        trace, correlation = uuid4().hex, 's2-' + uuid4().hex
        response = await client.request(method, endpoint + PATHS[service], json=body,
            headers={'traceparent': f'00-{trace}-{uuid4().hex[:16]}-01', 'x-correlation-id': correlation})
        wanted = (200,)
        if bad and service == 'checkout-service':
            wanted = (200, 502, 504) if loop.time() < grace else (200, 504)
        if (response.status_code not in wanted or response.headers.get('x-trace-id') != trace
                or response.headers.get('x-correlation-id') != correlation):
            raise ValueError(f'{service} returned {response.status_code}, expected {wanted}')
        counts[service][str(response.status_code)] = counts[service].get(str(response.status_code), 0) + 1

    while clock() < stop:
        await asyncio.gather(*(request(s, u) for s, u in urls.items()))
        deadline += 1
        await asyncio.sleep(max(0, deadline - loop.time()))
    return counts


def forward_replacement(sandbox):
    first = sandbox.root / (PAYMENT + '-forward.log')
    first.rename(sandbox.root / (PAYMENT + '-old-pod-forward.log'))
    return sandbox.forward(PAYMENT, 8000)


async def assess_h4(journal, job, target):
    """Record the internal authority decision only; never build an executor."""
    request = Request(job['incident_id'], (await journal.repository.get(job['incident_id']))['revision'],
                      's2h4-' + uuid4().hex)
    assessment = await NativeH4Authority(journal).assess(request, target, now=clock())
    rca = assessment.rca
    payment = next((c for c in rca['candidates'] if c['service'] == PAYMENT), None)
    return {'incident_id': assessment.incident_id, 'revision': assessment.revision, 'lifecycle': assessment.lifecycle,
            'availability': assessment.availability, 'latest_health_at': assessment.latest_health_at,
            'result_ref': assessment.result_ref, 'dispatch_authority': assessment.dispatch_authority,
            'missing_reason': assessment.missing_reason, 'ranking': rca['ranking'], 'abstained': rca['abstained'],
            'abstention_reasons': rca['abstention_reasons'],
            'payment_score': payment and payment['score'], 'payment_coverage': payment and payment['coverage'],
            'payment_components': payment and {k: v['value'] for k, v in payment['components'].items()},
            'change_bound': assessment.change is not None}


async def run(sandbox, bad, dsn, registry, release):
    urls = {s: sandbox.forward(s, 8000) for s in SERVICES}
    prometheus, jaeger = sandbox.forward('prometheus', 9090), sandbox.forward('jaeger', 16686)
    report = {}
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        for url in (*[u + '/ready' for u in urls.values()], prometheus + '/-/ready', jaeger + '/api/v3/services'):
            await wait_ready(client, url)
        source = PrometheusSource(prometheus)
        try:
            cfg = await asyncio.to_thread(source.verify_configuration)
        finally:
            source.close()
        endpoint, target = await asyncio.to_thread(sandbox.identity)
        observer_token = sandbox.root / 'observer-token'
        token = (await asyncio.to_thread(sandbox.kubectl, '-n', NAMESPACE, 'create', 'token', 'payment-quarantine-observer',
                                         '--duration=' + TOKEN_DURATION)).strip()
        observer_token.unlink()
        private_file(observer_token, token)
        pool = await connect_pool(dsn)
        kube = LocalKubernetes(endpoint, target, token_file=sandbox.root / 'observer-token', ca_file=sandbox.root / 'ca.crt')
        collector = None
        try:
            health_policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_text())
            rca_policy = json.loads((PROJECT / 'configs/rca_policy.json').read_text())
            topology = yaml.safe_load((PROJECT / 'configs/topology.yaml').read_text())
            store, raw, changes = (ArtifactStore(sandbox.root / n) for n in ('journal', 'health', 'change'))
            health = HealthWorker(pool, raw, health_policy, endpoint=prometheus, environment='local',
                                  configuration_hash=cfg['configuration_hash'])
            await health.check_ready()
            await health.tick()
            reader = HealthLedgerReader(pool, raw, health.stream_id, health_policy)
            inventory = await asyncio.to_thread(sandbox.inventory)
            base = KubernetesIncidentEvidence(kube, {s: x['deployment_uid'] for s, x in inventory.items()},
                                              prometheus_endpoint=prometheus, jaeger_endpoint=jaeger)
            collector = DeploymentIncidentEvidence(base, reader, changes)
            if registry is not None:
                collector = ModelIncidentEvidence(collector, registry)
            await collector.verify_source(health.identity)
            journal = IncidentJournal(pool, reader, store, topology, rca_policy, collector.profile, model_registry=registry)
            await journal.initialize()
            worker = IncidentWorker(journal, collector, connect=lambda: asyncpg.connect(dsn, timeout=5., command_timeout=10.))
            ready, finished = asyncio.Event(), asyncio.Event()
            observed_change = {}

            async def consume():
                attempts = 0
                while not finished.is_set():
                    decision = await health.tick()
                    if decision['status'] == 'COMMITTED':
                        ready.set()
                    try:
                        await worker.tick()
                    except KubernetesUnavailable as exc:
                        if str(exc) != 'kubernetes-accepted-http-bucket-unavailable':
                            raise
                        await asyncio.sleep(1)
                        continue
                    async with pool.acquire() as conn:
                        rows = await conn.fetch('''SELECT j.job_id, j.incident_id FROM sentinel_control.incident_jobs j
                            JOIN sentinel_control.incident_job_evidence e USING(job_id)
                            WHERE j.consumer_id=$1 ORDER BY j.closing_bucket_end''', journal.consumer_id)
                    if rows:
                        job, accepted, derived = await journal.verified_evidence(rows[0]['job_id'])
                        assessment = await assess_h4(journal, job, target)
                        attempts += 1
                        report.update(capture_version=store.read(accepted['capture_ref'])['capture_version'],
                            capture_ref=accepted['capture_ref'], change_ref=observed_change.get('change_ref'),
                            deployment_change_bound=derived.get('deployment_change') is not None, h4=assessment)
                        emit('h4-assessed', attempt=attempts, dispatch_authority=assessment['dispatch_authority'],
                             missing_reason=assessment['missing_reason'], payment_score=assessment['payment_score'])
                        if assessment['missing_reason'] != 'newer-accepted-health-awaits-consumer' or attempts >= 30:
                            finished.set()
                            return
                    await asyncio.sleep(1)

            async def experiment():
                first_full = ceil_minute(1)
                await drive(client, urls, first_full + timedelta(seconds=33), False)
                await asyncio.wait_for(ready.wait(), timeout=10)
                review = DeploymentChangeReview(reader, kube, changes, target)
                baseline = await review.open()
                emit('deploying-bad-payment-image', image=bad)
                await asyncio.to_thread(sandbox.kubectl, '-n', NAMESPACE, 'set', 'image', 'deployment/payment-service', 'payment=' + bad)
                await asyncio.to_thread(sandbox.kubectl, '-n', NAMESPACE, 'rollout', 'status', 'deployment/payment-service', '--timeout=65s')
                observed_change.update(await review.complete(baseline['baseline_ref']))
                urls[PAYMENT] = forward_replacement(sandbox)
                await wait_ready(client, urls[PAYMENT] + '/ready')
                traffic = asyncio.create_task(drive(client, urls, clock() + timedelta(minutes=POST_CHANGE_MINUTES), True))
                done_wait = asyncio.create_task(finished.wait())
                try:
                    done, _ = await asyncio.wait({traffic, done_wait}, return_when=asyncio.FIRST_COMPLETED)
                    if traffic in done:
                        report['post_change_counts'] = await traffic
                        if not finished.is_set():
                            raise TimeoutError('no persisted S2 incident after the post-change traffic window')
                finally:
                    for task in (traffic, done_wait):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(traffic, done_wait, return_exceptions=True)

            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(consume())
                tasks.create_task(experiment())
            report.update(health_stream_id=health.stream_id, consumer_id=journal.consumer_id,
                          model_release_ref=release, model_available=bool(registry and registry.available))
            return report
        finally:
            try:
                await (collector.close() if collector else kube.close())
            finally:
                await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', required=True, help='Pinned kind v0.30.0 binary')
    parser.add_argument('--output', type=Path, required=True, help='New private run directory')
    parser.add_argument('--model-store', type=Path)
    parser.add_argument('--model-release-ref')
    args = parser.parse_args()
    if bool(args.model_store) != bool(args.model_release_ref):
        parser.error('a validated release needs both --model-store and --model-release-ref')
    commit = clean_commit()
    registry = ModelRegistry.load(ArtifactStore(args.model_store), args.model_release_ref) if args.model_store else None
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    probe = root / '.permission-probe'
    probe.write_text('x'); probe.chmod(0o600)
    private = not probe.stat().st_mode & 0o077
    probe.unlink()
    if not private:
        raise SystemExit('output must be on a permission-enforcing filesystem (not /mnt/<drive> on WSL): '
                         'private credential files cannot be created there')
    sandbox = BusinessSandbox(root, args.kind)
    owned = []
    emit('sandbox-owned', cluster=sandbox.name, commit=commit, model_release=args.model_release_ref)

    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        images = deploy(sandbox)
        bad = bad_image(sandbox, images['application'])
        dsn = control_db(sandbox, images['postgres'], owned)
        report = asyncio.run(run(sandbox, bad, dsn, registry, args.model_release_ref))
        result = {'version': 's2-h4-assessment/v1', 'source_commit': commit, 'healthy_image': images['application'],
                  'bad_image': bad, **report, 'rollback_dispatched': False, 'known_good_approved': False,
                  'G6_accepted': False, 'holdout_opened': False}
        (root / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
        emit('s2-h4-complete', dispatch_authority=report['h4']['dispatch_authority'],
             missing_reason=report['h4']['missing_reason'], report=str(root / 'verification.json'))
    except subprocess.CalledProcessError as exc:
        if sandbox.kubeconfig.exists():
            sandbox.diagnostics()
        emit('command-failed', operation=Path(exc.cmd[0]).name, code=exc.returncode)
        raise
    finally:
        try:
            if owned:
                label = sandbox.docker('inspect', '--format', '{{index .Config.Labels "' + OWNER_LABEL + '"}}', owned[0]).decode().strip()
                if label == sandbox.name:
                    sandbox.docker('rm', '-f', '-v', owned[0])
        finally:
            sandbox.cleanup()
            emit('owned-sandbox-cleaned', cluster=sandbox.name)


if __name__ == '__main__':
    main()
