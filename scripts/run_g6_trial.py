"""Operator-run local G6 trial: one live Kubernetes target, approval before the bad change.

Order on a single owned sandbox that stays alive for the whole trial:

1. real services + healthy traffic; health/incident workers with a validated
   model release (H1) on a private control DB that includes the action journal;
2. `ImageEvidenceReview` collects known-good evidence on the healthy image and
   writes the evidence ref to `<output>/AWAITING_OPERATOR_APPROVAL`;
3. the operator inspects it and runs `scripts.approve_known_good`, then writes
   the printed ledger ref to `<output>/approved-ledger-ref` (the run waits);
4. only then the bad Payment image is deployed and recorded as an observed change;
5. when `NativeH4Authority` grants dispatch authority for that incident, the
   existing `RollbackExecutor` runs once (policy enabled only by
   `--enable-rollback-policy`; creating `<output>/KILL_SWITCH` blocks dispatch)
   and is reconciled until recovered, escalated or blocked.

Nothing here approves an image or a model by itself. The journal, evidence and
verification are kept in `<output>`. Cleanup keeps the private control DB when
a rollback attempt may have written an intent that has yet to be exported.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import sys
import subprocess
from uuid import uuid4

import asyncpg
import httpx
import yaml

from scripts.check_kubernetes_testbed import BusinessSandbox
from scripts.check_rollback_kubernetes import PROJECT, private_file
from scripts.kubernetes_testbed import NAMESPACE, SERVICES
from scripts.lane_b_host import validate_action_reader
from scripts.run_s2_h4 import (LOCAL_CONTROL_MIGRATIONS, PAYMENT, TOKEN_DURATION, bad_image, ceil_minute, clean_commit, clock, connect_pool,
                               health_profile, healthy_history_minutes,
                               deploy, drive, emit, wait_ready)
from sentinelops.ml.registry import ModelRegistry
from sentinelops.remediation.authority import NativeH4Authority
from sentinelops.remediation.domain import KubernetesUnavailable, Policy, Request
from sentinelops.remediation.executor import LedgerRecovery, RollbackExecutor
from sentinelops.remediation.image_review import ImageEvidenceReview
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.kubernetes import LocalKubernetes
from sentinelops.remediation.registry import KnownGoodRegistry
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

OWNER_LABEL = 'sentinelops.g6-trial'
WRITER_DSN_PREFIX = 'postgresql://control_writer:local-control-writer@127.0.0.1:'
APPROVAL_WAIT_SECONDS = 1800
ACTION_DEADLINE_SECONDS = 900
TERMINAL = ('recovered', 'escalated', 'blocked')
FAILED_VERIFY_SCENARIO = 'commerce-db-unavailable-after-rollback'
PENDING_AUDIT = 'action-audit-pending.json'


def control_db(sandbox, postgres, owned):
    """Private control DB with incident schemas and the action journal (001–005)."""
    bootstrap = sandbox.root / 'db-init'
    bootstrap.mkdir(mode=0o755)
    for filename in (*LOCAL_CONTROL_MIGRATIONS, '005-remediation.sql'):
        shutil.copyfile(PROJECT / 'infra/control' / filename, bootstrap / filename)
    password = secrets.token_urlsafe(32)
    env = sandbox.root / 'db-bootstrap.env'
    fd = os.open(env, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('POSTGRES_DB=sentinelops_control\nPOSTGRES_PASSWORD=' + password + '\n')
    owned.append(sandbox.docker('create', '--name', sandbox.name + '-control', '--label', OWNER_LABEL + '=' + sandbox.name,
        '--memory', '256m', '--cpus', '0.5', '--env-file', str(env), '-p', '127.0.0.1::5432',
        '-v', str(bootstrap) + ':/docker-entrypoint-initdb.d:ro', postgres).decode().strip())
    env.unlink()
    sandbox.docker('start', owned[0])
    address, port = sandbox.docker('port', owned[0], '5432/tcp').decode().strip().split(':')
    if address != '127.0.0.1' or not port.isdecimal():
        raise ValueError('private control database is not loopback-bound')
    return port, password


async def action_dsn(port, bootstrap_password):
    """Dedicated login in control_action_writer; the bootstrap secret never leaves memory."""
    for _ in range(60):
        try:
            conn = await asyncpg.connect(f'postgresql://postgres:{bootstrap_password}@127.0.0.1:{port}/sentinelops_control',
                                         timeout=2)
            break
        except (OSError, TimeoutError, asyncpg.CannotConnectNowError, asyncpg.InvalidPasswordError):
            await asyncio.sleep(.5)
    else:
        raise TimeoutError('private control database not ready')
    try:
        runtime = secrets.token_urlsafe(32)
        statement = await conn.fetchval("SELECT format('CREATE ROLE g6_action LOGIN PASSWORD %L IN ROLE control_action_writer "
                                        "NOSUPERUSER NOCREATEDB NOCREATEROLE', $1::text)", runtime)
        await conn.execute(statement)
    finally:
        await conn.close()
    return f'postgresql://g6_action:{runtime}@127.0.0.1:{port}/sentinelops_control'


DEMO_RECEIPT = {'demo_mode': True, 'run_purpose': 'demo', 'q09_eligible': False, 'G6_accepted': False}


async def demo_reader_dsn(port, bootstrap_password):
    """A distinct SELECT-only login; never grant API membership in a writer role."""
    for _ in range(60):
        try:
            conn = await asyncpg.connect(
                f'postgresql://postgres:{bootstrap_password}@127.0.0.1:{port}/sentinelops_control', timeout=2)
            break
        except (OSError, TimeoutError, asyncpg.CannotConnectNowError, asyncpg.InvalidPasswordError):
            await asyncio.sleep(.5)
    else:
        raise TimeoutError('private control database not ready')
    try:
        password = secrets.token_urlsafe(32)
        statement = await conn.fetchval("SELECT format('CREATE ROLE g6_api_reader LOGIN PASSWORD %L "
            "IN ROLE control_action_reader NOSUPERUSER NOCREATEDB NOCREATEROLE', $1::text)", password)
        await conn.execute(statement)
        await conn.execute('GRANT USAGE ON SCHEMA sentinel_control TO g6_api_reader')
        await conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA sentinel_control TO g6_api_reader')
    finally:
        await conn.close()
    return f'postgresql://g6_api_reader:{password}@127.0.0.1:{port}/sentinelops_control'


def demo_api_environment(reader, args, token):
    # Do not inherit producer/writer credentials, demo fixtures or OIDC settings.
    env = {k: v for k, v in os.environ.items() if not k.startswith('SENTINELOPS_')
           and k not in ('CONTROL_DATABASE_URL', 'DATABASE_URL', 'COMMERCE_EVIDENCE_DATABASE_URL')}
    env.update(CONTROL_DATABASE_URL=reader, SENTINELOPS_ACTION_READ_DATABASE_URL=reader,
               SENTINELOPS_ENV='local', SENTINELOPS_ENABLE_DEMO='false',
               SENTINELOPS_OPERATOR_TOKEN=token, SENTINELOPS_MODEL_STORE=str(args.model_store.resolve()),
               SENTINELOPS_MODEL_RELEASE_REF=args.model_release_ref)
    return env


async def start_demo_api(sandbox, port, password, container, args):
    validate_action_reader(f'postgresql://g6_api_reader:pending@127.0.0.1:{port}/sentinelops_control',
                           port, sandbox=sandbox, container=container)
    reader = await demo_reader_dsn(port, password)
    token = secrets.token_urlsafe(48)
    private_file(sandbox.root / 'operator-token', (token + '\n').encode())
    with socket.socket() as listener, (sandbox.root / 'demo-api.log').open('x') as log:
        listener.bind(('127.0.0.1', args.demo_api_port))
        listener.listen(128)
        api_port = listener.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'sentinelops.main:app',
                                    '--fd', str(listener.fileno())], pass_fds=(listener.fileno(),),
                                   env=demo_api_environment(reader, args, token), cwd=PROJECT,
                                   stdout=log, stderr=log)
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError('demo API stopped; inspect private demo-api.log')
                try:
                    response = await client.get(f'http://127.0.0.1:{api_port}/ready/actions',
                                                headers={'Authorization': 'Bearer ' + token})
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
            else:
                raise TimeoutError('demo action reader did not become ready')
    except BaseException:
        stop_demo_api(process)
        raise
    emit('demo-api-ready', ui=f'http://127.0.0.1:{api_port}/ui/',
         token_file=str(sandbox.root / 'operator-token'), q09_eligible=False)
    return process


def stop_demo_api(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def keep_demo_alive(root, report, require_alive):
    initial = json.loads((root / 'verification.json').read_text()) if (root / 'verification.json').exists() else {}
    report = {**initial, **report, **DEMO_RECEIPT}
    (root / 'verification.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    emit('demo-awaiting-operator-stop', report=str(root / 'verification.json'), q09_eligible=False)
    while True:
        try:
            require_alive()
        except Exception as exc:
            # After the trial, preserve read access even if traffic/consumer fail.
            # Safety checks before every trial side effect remain strict.
            if 'demo_background_failure' not in report:
                report['demo_background_failure'] = type(exc).__name__
                (root / 'verification.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
                emit('demo-background-stopped', error_type=type(exc).__name__, q09_eligible=False)
        await asyncio.sleep(1)


def reissue(sandbox, account, filename):
    token = sandbox.kubectl('-n', NAMESPACE, 'create', 'token', account, '--duration=' + TOKEN_DURATION).strip()
    path = sandbox.root / filename
    path.unlink()
    private_file(path, token)
    return path


async def wait_for_file(path, timeout):
    for _ in range(int(timeout)):
        if path.exists():
            return path.read_text().strip()
        await asyncio.sleep(1)
    raise TimeoutError(f'operator did not provide {path.name} in time')


def source_unavailable_is_transient(exc, outage):
    """Which KubernetesUnavailable errors the trial consumer retries instead of failing.

    A golden-path trial keeps the strict rule: only a not-yet-accepted HTTP bucket is
    retried. During the injected dependency outage the Payment pod crash-loops by
    design, so the business inventory is legitimately unusable for a while; the
    production incident worker (`scripts/run_incident_worker.py`) treats every
    KubernetesUnavailable as SOURCE_UNAVAILABLE and retries. The trial does the same
    then, so the executor, not a harness crash, decides the outcome.
    """
    return outage or str(exc) == 'kubernetes-accepted-http-bucket-unavailable'


def reforward(sandbox, attempt):
    """Fresh Payment port-forward after its pod changes; keep every earlier log."""
    current = sandbox.root / (PAYMENT + '-forward.log')
    if current.exists():
        current.rename(sandbox.root / f'{PAYMENT}-forward.{attempt}.log')
    return sandbox.forward(PAYMENT, 8000)


async def resilient_traffic(client, sandbox, urls, stop_event, phase):
    """1 request/s per entry; re-forward Payment while a pod replacement rolls out."""
    attempt = 0
    while not stop_event.is_set():
        try:
            await drive(client, urls, clock() + timedelta(seconds=30), phase['bad'],
                        dependency_outage=lambda: phase.get('outage', False))
        except (httpx.HTTPError, ValueError) as exc:
            emit('traffic-interrupted', reason=type(exc).__name__, bad=phase['bad'])
            for _ in range(24):  # up to ~2 min of rollout (maxSurge 0 leaves a gap)
                if stop_event.is_set():
                    return
                attempt += 1
                try:
                    urls[PAYMENT] = await asyncio.to_thread(reforward, sandbox, attempt)
                    await wait_ready(client, urls[PAYMENT] + '/ready')
                    break
                except RuntimeError:
                    await asyncio.sleep(5)
            else:
                raise RuntimeError('payment port-forward did not recover after the pod change')


async def trial(sandbox, dsn, port, bootstrap_password, bad, registry, args):
    root = sandbox.root
    pending_audit = root / PENDING_AUDIT
    urls = {s: sandbox.forward(s, 8000) for s in SERVICES}
    prometheus, jaeger = sandbox.forward('prometheus', 9090), sandbox.forward('jaeger', 16686)
    report = {'phases': [], **(DEMO_RECEIPT if getattr(args, 'demo_keep_alive', False) else {})}
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        for url in (*[u + '/ready' for u in urls.values()], prometheus + '/-/ready', jaeger + '/api/v3/services'):
            await wait_ready(client, url)
        source = PrometheusSource(prometheus)
        try:
            cfg = await asyncio.to_thread(source.verify_configuration)
        finally:
            source.close()
        endpoint, target = await asyncio.to_thread(sandbox.identity)
        observer_token = await asyncio.to_thread(reissue, sandbox, 'payment-quarantine-observer', 'observer-token')
        runtime_token = await asyncio.to_thread(reissue, sandbox, 'payment-rollback', 'runtime-token')
        observer = LocalKubernetes(endpoint, target, token_file=observer_token, ca_file=root / 'ca.crt')
        runtime = LocalKubernetes(endpoint, target, token_file=runtime_token, ca_file=root / 'ca.crt')
        pool = await connect_pool(dsn)
        action_pool = await asyncpg.create_pool(await action_dsn(port, bootstrap_password), min_size=1, max_size=2)
        collector = None
        stop = asyncio.Event()
        phase = {'bad': False}
        try:
            health_policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_text())
            rca_policy = json.loads((PROJECT / 'configs/rca_policy.json').read_text())
            topology = yaml.safe_load((PROJECT / 'configs/topology.yaml').read_text())
            store, raw, changes, review = (ArtifactStore(root / n) for n in ('journal', 'health', 'change', 'review'))
            health = HealthWorker(pool, raw, health_policy, endpoint=prometheus, environment='local',
                                  configuration_hash=cfg['configuration_hash'], source_profile=health_profile(registry))
            await health.check_ready()
            await health.tick()
            reader = HealthLedgerReader(pool, raw, health.stream_id, health_policy)
            inventory = await asyncio.to_thread(sandbox.inventory)
            base = KubernetesIncidentEvidence(observer, {s: x['deployment_uid'] for s, x in inventory.items()},
                                              prometheus_endpoint=prometheus, jaeger_endpoint=jaeger)
            collector = ModelIncidentEvidence(DeploymentIncidentEvidence(base, reader, changes), registry)
            await collector.verify_source(health.identity)
            journal = IncidentJournal(pool, reader, store, topology, rca_policy, collector.profile, model_registry=registry)
            await journal.initialize()
            worker = IncidentWorker(journal, collector, connect=lambda: asyncpg.connect(dsn, timeout=5., command_timeout=10.))
            actions = ActionJournal(action_pool)
            await actions.check_ready()

            async def consume():
                while not stop.is_set():
                    await health.tick()
                    try:
                        await worker.tick()
                    except KubernetesUnavailable as exc:
                        if not source_unavailable_is_transient(exc, phase.get('outage', False)):
                            raise
                        emit('incident-source-unavailable', reason=str(exc))
                    await asyncio.sleep(1)

            consumer = asyncio.create_task(consume())
            traffic = asyncio.create_task(resilient_traffic(client, sandbox, urls, stop, phase))
            pending_request = None

            def export_action(action):
                if action is None:
                    raise RuntimeError('action journal missing during export')
                (root / 'action-journal.json').write_text(json.dumps(action, indent=2, default=str) + '\n')
                pending_audit.unlink()
                report['action'] = action

            def require_alive():
                api = getattr(args, '_demo_api_process', None)
                if api is not None and api.poll() is not None:
                    raise RuntimeError('demo API stopped; inspect private demo-api.log')
                for name, task in (('consumer', consumer), ('traffic', traffic)):
                    if task.done():
                        exc = task.exception() if not task.cancelled() else None
                        raise RuntimeError(f'{name} task stopped before a side effect: {type(exc).__name__}')

            try:
                await asyncio.sleep(max(0, (ceil_minute(healthy_history_minutes(registry)) - clock()).total_seconds()))
                images = ImageEvidenceReview(reader, observer, review, target)
                opened = await images.open()
                for _ in range(40):
                    await asyncio.sleep(15)
                    result = await images.complete(opened['review_ref'])
                    if result['status'] == 'AWAITING_OPERATOR_REVIEW':
                        break
                else:
                    raise TimeoutError('known-good evidence did not verify on the healthy image')
                (root / 'AWAITING_OPERATOR_APPROVAL').write_text(result['evidence_ref'] + '\n')
                emit('awaiting-operator-approval', evidence_ref=result['evidence_ref'], review_store=str(root / 'review'))
                ledger_ref = await wait_for_file(root / 'approved-ledger-ref', APPROVAL_WAIT_SECONDS)
                known_good = KnownGoodRegistry(review, ledger_ref, approved_by=args.approver, source=reader)
                # Preflight before any bad change: the approval must resolve for this exact
                # target and match both the collected evidence and the deployed healthy image.
                resolved = await known_good.resolve(target, incident_at=clock())
                current = await observer.observe()
                if resolved.image != result['evidence']['image'] or resolved.image != current.image:
                    raise ValueError('approved known-good image differs from evidence or the deployed healthy image')
                report['phases'].append({'known_good_ledger_ref': ledger_ref, 'evidence_ref': result['evidence_ref'],
                                         'known_good_image': resolved.image, 'preflight_resolved_at': str(clock())})

                change_review = DeploymentChangeReview(reader, observer, changes, target)
                baseline = await change_review.open()
                require_alive()
                emit('deploying-bad-payment-image', image=bad)
                phase['bad'] = True
                await asyncio.to_thread(sandbox.kubectl, '-n', NAMESPACE, 'set', 'image', 'deployment/payment-service', 'payment=' + bad)
                await asyncio.to_thread(sandbox.kubectl, '-n', NAMESPACE, 'rollout', 'status', 'deployment/payment-service', '--timeout=65s')
                change = await change_review.complete(baseline['baseline_ref'])
                report['phases'].append({'change_ref': change.get('change_ref')})

                authority = NativeH4Authority(journal)
                policy = Policy(target, enabled=bool(args.enable_rollback_policy))
                executor = RollbackExecutor(actions, runtime, authority, known_good, LedgerRecovery(reader, target),
                                            policy, rca_policy, enabled=lambda: not (root / 'KILL_SWITCH').exists())
                deadline = clock() + timedelta(seconds=ACTION_DEADLINE_SECONDS)
                state = None
                dependency_broken = False
                async def trial_incident():
                    # Only the incident whose verified evidence binds THIS observed change.
                    async with pool.acquire() as conn:
                        rows = await conn.fetch('''SELECT j.job_id, j.incident_id FROM sentinel_control.incident_jobs j
                            JOIN sentinel_control.incident_job_evidence e USING(job_id)
                            WHERE j.consumer_id=$1 ORDER BY j.closing_bucket_end''', journal.consumer_id)
                    for item in rows:
                        _, _, derived = await journal.verified_evidence(item['job_id'])
                        bound = derived.get('deployment_change')
                        if bound is not None and bound['source_ref'] == change['change_ref']:
                            return item['incident_id']
                    return None

                while clock() < deadline:
                    require_alive()
                    incident_id = await trial_incident()
                    if incident_id is None:
                        await asyncio.sleep(2)
                        continue
                    snapshot = await journal.repository.get(incident_id)
                    request = Request(incident_id, snapshot['revision'], 'g6-' + uuid4().hex)
                    if state is None:
                        assessment = await authority.assess(request, target, now=clock())
                        if assessment.missing_reason == 'newer-accepted-health-awaits-consumer':
                            await asyncio.sleep(2)
                            continue
                        if assessment.revision != request.incident_revision:
                            # The consumer advanced the incident between the snapshot and the
                            # assessment; the executor would rightly block this stale request.
                            emit('incident-revision-advanced', requested=request.incident_revision,
                                 assessed=assessment.revision)
                            await asyncio.sleep(1)
                            continue
                        report['h4'] = {'dispatch_authority': assessment.dispatch_authority,
                                        'missing_reason': assessment.missing_reason, 'revision': assessment.revision}
                        require_alive()
                        pending_request = request
                        # Leave durable cleanup state before the executor can create an intent.
                        with pending_audit.open('x') as stream:
                            json.dump({'request': asdict(request), 'target': asdict(target)}, stream)
                        state = await executor.execute(request)
                        # Once dispatched, the executor decides within its own verification window;
                        # never cut a trial off before it can reach recovered or escalated.
                        deadline = max(deadline, clock() + timedelta(seconds=policy.verification_seconds + 90))
                        phase['bad'] = state['phase'] not in ('blocked',)
                        emit('action-state', phase=state['phase'], action_id=state['action_id'])
                    else:
                        state = await executor.reconcile(state['action_id'])
                        emit('action-state', phase=state['phase'])
                    if args.failed_verify_scenario and not dependency_broken and state['phase'] == 'verifying':
                        # Negative Q-09 path: the rollback is dispatched, but a second, independent
                        # cause (commerce DB outage) keeps Payment unhealthy, so verification must fail.
                        phase['outage'] = True
                        await asyncio.to_thread(sandbox.kubectl, '-n', NAMESPACE, 'scale', 'deployment/postgres',
                                                '--replicas=0')
                        dependency_broken = True
                        report['phases'].append({'dependency_broken_at': str(clock()),
                                                 'scenario': FAILED_VERIFY_SCENARIO})
                        emit('dependency-broken', scenario=FAILED_VERIFY_SCENARIO)
                    if state['phase'] in TERMINAL:
                        break
                    await asyncio.sleep(5)
                if state is None:
                    report['action_outcome'] = 'NO_ACTION_ATTEMPTED_BEFORE_DEADLINE'
                else:
                    # Keep the verified journal (intent, history, hash chain) before the DB is deleted.
                    export_action(await actions.get(state['action_id']))
                    report['action_outcome'] = state['phase'] if state['phase'] in TERMINAL else 'NONTERMINAL_TIMEOUT'
            except BaseException as exc:
                # Bounded, redacted receipt; recover any recorded intent before the DB is deleted.
                # BaseException: SIGTERM/SIGINT arrive as cancellation and must not skip the audit.
                report['failure'] = {'error_type': type(exc).__name__}
                report['action_outcome'] = 'TRIAL_FAILED'
                if pending_request is not None and pending_audit.exists():
                    try:
                        found = await actions.find(pending_request, target)
                        if found is not None:
                            export_action(found)
                    except Exception as recovery:
                        report['failure']['journal_recovery_error_type'] = type(recovery).__name__
                # An intent may exist that could not be exported: keep the private DB instead.
                report['retain_control_db'] = pending_audit.exists()
                (root / 'failure-receipt.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
                if not isinstance(exc, Exception):
                    raise
            finally:
                try:
                    if getattr(args, 'demo_keep_alive', False) and report.get('failure', {}).get('error_type') not in ('CancelledError', 'KeyboardInterrupt'):
                        report.update(health_stream_id=health.stream_id, consumer_id=journal.consumer_id,
                                      model_release_ref=args.model_release_ref,
                                      scenario=FAILED_VERIFY_SCENARIO if args.failed_verify_scenario else 'golden-path')
                        await keep_demo_alive(root, report, require_alive)
                finally:
                    stop.set()
                    failures = {}
                    for name, task in (('traffic', traffic), ('consumer', consumer)):
                        if task.done() and not task.cancelled() and task.exception() is not None:
                            failures[name] = type(task.exception()).__name__
                        task.cancel()
                    await asyncio.gather(traffic, consumer, return_exceptions=True)
                    report['background_task_failures'] = failures
            report.update(health_stream_id=health.stream_id, consumer_id=journal.consumer_id)
            return report
        finally:
            try:
                await (collector.close() if collector else observer.close())
            finally:
                await runtime.close()
                await action_pool.close()
                await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-store', type=Path, required=True)
    parser.add_argument('--model-release-ref', required=True)
    parser.add_argument('--approver', required=True, help='Operator identity that must sign the known-good ledger')
    parser.add_argument('--enable-rollback-policy', action='store_true',
                        help='Without it the executor records a blocked intent and never patches')
    parser.add_argument('--failed-verify-scenario', action='store_true',
                        help='Negative Q-09 trial: after dispatch, scale the in-cluster commerce PostgreSQL to zero; '
                             'expected outcome escalated with a complete audit')
    parser.add_argument('--demo-keep-alive', action='store_true',
                        help='Demo only, excluded from Q-09; keep API/workers alive until operator stop; retain sandbox')
    parser.add_argument('--demo-api-port', type=int, default=18000)
    args = parser.parse_args()
    if not 0 <= args.demo_api_port <= 65535:
        parser.error('demo API port must be between 0 and 65535')
    commit = clean_commit()
    registry = ModelRegistry.load(ArtifactStore(args.model_store), args.model_release_ref)
    if not registry.available:
        raise ValueError('G6 requires a complete validated model release (H1)')
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    probe = root / '.permission-probe'
    probe.write_text('x'); probe.chmod(0o600)
    private = not probe.stat().st_mode & 0o077
    probe.unlink()
    if not private:
        raise SystemExit('output must be on a permission-enforcing filesystem (not /mnt/<drive> on WSL): '
                         'private credential files cannot be created there')
    if args.demo_keep_alive:
        (root / 'DEMO_ONLY').write_text('Excluded from Q-09\n')
        (root / 'verification.json').write_text(json.dumps({**DEMO_RECEIPT, 'action_outcome': 'DEMO_STARTED',
            'model_release_ref': args.model_release_ref, 'source_commit': commit}) + '\n')
    sandbox = BusinessSandbox(root, args.kind)
    owned = []
    api_process = None
    emit('sandbox-owned', cluster=sandbox.name, commit=commit, policy_enabled=args.enable_rollback_policy)

    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        images = deploy(sandbox)
        bad = bad_image(sandbox, images['application'])
        port, password = control_db(sandbox, images['postgres'], owned)
        dsn = WRITER_DSN_PREFIX + port + '/sentinelops_control'
        async def run():
            nonlocal api_process
            if args.demo_keep_alive:
                api_process = await start_demo_api(sandbox, port, password, owned[0], args)
                args._demo_api_process = api_process
            return await trial(sandbox, dsn, port, password, bad, registry, args)
        report = asyncio.run(run())
        result = {'version': 'g6-local-trial/v1', 'source_commit': commit, 'healthy_image': images['application'],
                  'bad_image': bad, 'model_release_ref': args.model_release_ref, **report,
                  'policy_enabled': args.enable_rollback_policy,
                  'scenario': FAILED_VERIFY_SCENARIO if args.failed_verify_scenario else 'golden-path',
                  'G6_accepted': False, **(DEMO_RECEIPT if args.demo_keep_alive else {})}
        (root / 'verification.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
        emit('g6-trial-complete', action_outcome=report.get('action_outcome'), report=str(root / 'verification.json'))
        if report.get('failure'):
            raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        if sandbox.kubeconfig.exists():
            sandbox.diagnostics()
        emit('command-failed', operation=Path(exc.cmd[0]).name, code=exc.returncode)
        raise
    finally:
        stop_demo_api(api_process)
        try:
            if args.demo_keep_alive:
                emit('demo-sandbox-retained', cluster=sandbox.name, container=owned[0] if owned else None,
                     note='operator owns explicit cleanup; this demo is excluded from Q-09')
            elif owned and (root / PENDING_AUDIT).exists():
                emit('control-db-retained-for-audit', container=owned[0],
                     note='an action intent may exist that was not exported; inspect, then remove manually')
            elif owned:
                label = sandbox.docker('inspect', '--format', '{{index .Config.Labels "' + OWNER_LABEL + '"}}', owned[0]).decode().strip()
                if label == sandbox.name:
                    sandbox.docker('rm', '-f', '-v', owned[0])
        finally:
            if not args.demo_keep_alive:
                sandbox.cleanup()
                emit('owned-sandbox-cleaned', cluster=sandbox.name)


if __name__ == '__main__':
    main()
