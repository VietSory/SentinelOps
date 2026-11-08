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

Nothing here approves an image or a model by itself. All owned resources are
removed on exit; the journal, evidence and verification are kept in `<output>`.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
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
from scripts.kubernetes_testbed import NAMESPACE, SERVICES
from scripts.run_s2_h4 import (PAYMENT, TOKEN_DURATION, bad_image, ceil_minute, clean_commit, clock, connect_pool,
                               deploy, drive, emit, forward_replacement, wait_ready)
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


def control_db(sandbox, postgres, owned):
    """Private control DB with incident schemas and the action journal (001–005)."""
    bootstrap = sandbox.root / 'db-init'
    bootstrap.mkdir(mode=0o755)
    for path in sorted((PROJECT / 'infra/control').glob('00[1-5]-*.sql')):
        shutil.copyfile(path, bootstrap / path.name)
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


async def resilient_traffic(client, sandbox, urls, stop_event, phase):
    """1 request/s per entry; re-forward Payment after its pod is replaced."""
    while not stop_event.is_set():
        try:
            await drive(client, urls, clock() + timedelta(seconds=30), phase['bad'])
        except (httpx.HTTPError, ValueError) as exc:
            emit('traffic-interrupted', reason=type(exc).__name__, bad=phase['bad'])
            urls[PAYMENT] = await asyncio.to_thread(forward_replacement, sandbox)
            await wait_ready(client, urls[PAYMENT] + '/ready')


async def trial(sandbox, dsn, port, bootstrap_password, bad, registry, args):
    root = sandbox.root
    urls = {s: sandbox.forward(s, 8000) for s in SERVICES}
    prometheus, jaeger = sandbox.forward('prometheus', 9090), sandbox.forward('jaeger', 16686)
    report = {'phases': []}
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
                                  configuration_hash=cfg['configuration_hash'])
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
                        if str(exc) != 'kubernetes-accepted-http-bucket-unavailable':
                            raise
                    await asyncio.sleep(1)

            consumer = asyncio.create_task(consume())
            traffic = asyncio.create_task(resilient_traffic(client, sandbox, urls, stop, phase))
            try:
                await asyncio.sleep(max(0, (ceil_minute(1) - clock()).total_seconds()))
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
                while clock() < deadline:
                    async with pool.acquire() as conn:
                        row = await conn.fetchrow('''SELECT j.incident_id FROM sentinel_control.incident_jobs j
                            JOIN sentinel_control.incident_job_evidence e USING(job_id)
                            WHERE j.consumer_id=$1 ORDER BY j.closing_bucket_end LIMIT 1''', journal.consumer_id)
                    if row is None:
                        await asyncio.sleep(2)
                        continue
                    snapshot = await journal.repository.get(row['incident_id'])
                    request = Request(row['incident_id'], snapshot['revision'], 'g6-' + uuid4().hex)
                    if state is None:
                        assessment = await authority.assess(request, target, now=clock())
                        if assessment.missing_reason == 'newer-accepted-health-awaits-consumer':
                            await asyncio.sleep(2)
                            continue
                        report['h4'] = {'dispatch_authority': assessment.dispatch_authority,
                                        'missing_reason': assessment.missing_reason, 'revision': assessment.revision}
                        state = await executor.execute(request)
                        phase['bad'] = state['phase'] not in ('blocked',)
                        emit('action-state', phase=state['phase'], action_id=state['action_id'])
                    else:
                        state = await executor.reconcile(state['action_id'])
                        emit('action-state', phase=state['phase'])
                    if state['phase'] in TERMINAL:
                        break
                    await asyncio.sleep(5)
                if state is None:
                    report['action_outcome'] = 'NO_ACTION_ATTEMPTED_BEFORE_DEADLINE'
                else:
                    # Keep the verified journal (intent, history, hash chain) before the DB is deleted.
                    report['action'] = await actions.get(state['action_id'])
                    report['action_outcome'] = state['phase'] if state['phase'] in TERMINAL else 'NONTERMINAL_TIMEOUT'
                    (root / 'action-journal.json').write_text(json.dumps(report['action'], indent=2, default=str) + '\n')
            finally:
                stop.set()
                for task in (traffic, consumer):
                    task.cancel()
                await asyncio.gather(traffic, consumer, return_exceptions=True)
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
    args = parser.parse_args()
    commit = clean_commit()
    registry = ModelRegistry.load(ArtifactStore(args.model_store), args.model_release_ref)
    if not registry.available:
        raise ValueError('G6 requires a complete validated model release (H1)')
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    sandbox = BusinessSandbox(root, args.kind)
    owned = []
    emit('sandbox-owned', cluster=sandbox.name, commit=commit, policy_enabled=args.enable_rollback_policy)

    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        images = deploy(sandbox)
        bad = bad_image(sandbox, images['application'])
        port, password = control_db(sandbox, images['postgres'], owned)
        dsn = WRITER_DSN_PREFIX + port + '/sentinelops_control'
        report = asyncio.run(trial(sandbox, dsn, port, password, bad, registry, args))
        result = {'version': 'g6-local-trial/v1', 'source_commit': commit, 'healthy_image': images['application'],
                  'bad_image': bad, 'model_release_ref': args.model_release_ref, **report,
                  'policy_enabled': args.enable_rollback_policy, 'G6_accepted': False}
        (root / 'verification.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
        emit('g6-trial-complete', action_outcome=report.get('action_outcome'), report=str(root / 'verification.json'))
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
