"""Operator-run three-round Payment runbook demo; never approves a known-good image.

Requires the controller's durable decision store, original health/review stores,
an explicit read-only action database DSN in SENTINELOPS_ACTION_READ_DATABASE_URL,
and an explicit limited Payment Kubernetes credential. No ambient AWS/kubeconfig.
--kill-switch-run requires the controller's shared kill-switch file to exist and
a fresh kill-switch decision (arrange a pending incident before starting). It
never injects a regression or sends a PATCH, including dry-run PATCH.
--reject-run and --failed-verify-run each run one negative round in a fresh
output directory. Failed verification uses an explicit fault credential to POST
/internal/faults through the Kubernetes proxy of the new known-good Payment pod.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import time
from urllib.parse import parse_qs, urlsplit

import asyncpg

from sentinelops.remediation.domain import Policy, Target, image_digest
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.kubernetes import LocalKubernetes
from sentinelops.remediation.reader import ActionReader
from sentinelops.remediation.registry import KnownGoodRegistry
from sentinelops.remediation.approvals import PROPOSAL_VERSION, read_slot, record_approval
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.deployment_changes import DeploymentChangeReview
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream

COOLDOWN = 600
RUNBOOK_ID = 'payment-bad-deploy-rollback'


async def wait_for(read, accept, *, timeout, poll, clock, sleep):
    deadline = clock() + timeout
    while True:
        value = await read()
        if accept(value):
            return value
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('runbook demo evidence deadline exceeded')
        await sleep(min(poll, remaining))


def proposal_decision(card):
    return (card.get('decision_version') == 'runbook-decision/v1'
        and card.get('proposal_version') == PROPOSAL_VERSION
        and isinstance(card.get('proposal_id'), str) and re.fullmatch(r'[0-9a-f]{64}', card['proposal_id'])
        and card.get('decision') == 'oncall' and card.get('runbook_id') == RUNBOOK_ID
        and card.get('executor_runbook') == 'payment-rollback-known-good/v1'
        and type(card.get('score')) in (int, float) and math.isfinite(card['score'])
        and .80 <= card['score'] <= 1 and type(card.get('margin')) in (int, float)
        and math.isfinite(card['margin']) and .15 <= card['margin'] <= 1
        and (card.get('symptoms') or {}).get('severity') in ('low', 'medium', 'high', 'critical'))


def approval_command(proposal, store, approved_by='<oncall-name>', *, decision='approve'):
    return shlex.join(['kubectl', '-n', 'sentinelops-b-actions', 'exec', 'deploy/remediation-controller',
        '--', 'python', 'scripts/approve_runbook.py', '--store', str(store),
        '--proposal-id', proposal['proposal_id'], '--approved-by', approved_by,
        '--decision', decision, '--reason', 'on-call reviewed proposal', '--confirm-oncall-approval'])


async def run_demo(backend, *, output=Path('artifacts/aws-demo/runs'), kill_switch_run=False, approve_as=None,
                   failed_verify_run=False, reject_run=False,
                   cooldown=COOLDOWN, timeout=1200., poll=10., clock=time.monotonic,
                   sleep=asyncio.sleep, now=lambda: datetime.now(timezone.utc)):
    """All external operations are injectable; receipts require observed evidence."""
    if (not math.isfinite(cooldown) or cooldown < COOLDOWN
        or not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(poll) or not 0 < poll <= 10):
        raise ValueError('invalid demo cooldown/deadline/poll bounds')
    output = Path(output)
    if approve_as is not None and (not isinstance(approve_as, str) or not approve_as.strip()):
        raise ValueError('approve-as must name the on-call operator')
    if sum((kill_switch_run, failed_verify_run, reject_run)) > 1:
        raise ValueError('negative demo modes are mutually exclusive')
    count = 1 if failed_verify_run or reject_run else 3
    output.mkdir(parents=True, exist_ok=True)
    # Refuse reuse before doing anything that could mutate Payment.
    if any((output / f'{n}.json').exists() for n in range(1, count + 1)):
        raise FileExistsError('demo receipts already exist; choose a new output directory')
    receipts, consumed = [], set()
    for number in range(1, count + 1):
        if number > 1:
            # Waiting after recovery is stricter than spacing deployment starts.
            await sleep(cooldown)
        good = await backend.known_good(now())
        before = await backend.deployment()
        if before.image != good.image:
            raise ValueError('Payment must be on the approved known-good image before every round')
        # The executor only rolls back to a distinct digest in the same repository
        # (domain.rollback_patch), so a regression elsewhere could never be recovered.
        if not kill_switch_run and (backend.regression_image == good.image
                                    or backend.regression_image.split('@')[0] != good.image.split('@')[0]):
            raise ValueError('regression must be a distinct digest in the known-good image repository')
        baseline = {ref for ref, _ in await backend.decisions()}
        dispatches_before = await backend.dispatches()
        started = timestamp(now())
        if kill_switch_run:
            if not backend.kill_active():
                raise ValueError('shared controller kill switch must already be active')
        else:
            if backend.kill_active():
                raise ValueError('controller kill switch is active')
            change = await backend.deploy(before)
            if change.get('status') != 'CHANGE_OBSERVED' or not change.get('change_ref'):
                raise ValueError('regression deployment must have observed change evidence')

        async def decision_read():
            if kill_switch_run:
                if not backend.kill_active() or await backend.dispatches() != dispatches_before:
                    raise ValueError('kill switch changed or action mutation was observed')
                if (await backend.deployment()).resource_version != before.resource_version:
                    raise ValueError('Payment changed during kill-switch observation')
            for ref, card in await backend.decisions():
                if ref in baseline or card.get('incident_id') in consumed:
                    continue
                if (not card.get('incident_id') or type(card.get('incident_revision')) is not int
                    or card['incident_revision'] < 1):
                    continue
                if not kill_switch_run and not await backend.incident_after(card, started):
                    continue
                if kill_switch_run:
                    if card.get('decision') == 'auto':
                        raise ValueError('controller selected auto while kill switch was active')
                    if (card.get('decision') == 'engineer'
                        and 'runbook-disabled-or-kill-switch' in card.get('reasons', [])):
                        return ref, card
                elif proposal_decision(card):
                    return ref, card
            return None

        decision_ref, decision = await wait_for(decision_read, lambda v: v is not None,
            timeout=timeout, poll=poll, clock=clock, sleep=sleep)
        consumed.add(decision['incident_id'])
        receipt = {'receipt_version': 'runbook-demo/v1', 'run': number,
            'demo_mode': True, 'G6_accepted': False, 'q09_eligible': False,
            'started_at': started, 'known_good_ledger_ref': good.ledger_ref,
            'known_good_image': good.image, 'target': asdict(before.target),
            'decision_ref': decision_ref, 'decision': decision,
            'kill_switch_run': kill_switch_run, 'failed_verify_run': failed_verify_run, 'reject_run': reject_run}
        if kill_switch_run:
            # Observe for a full deadline, not merely one controller tick.
            end = clock() + timeout
            while clock() < end:
                await sleep(min(poll, end - clock()))
                await decision_read()
            receipt.update(mutation_count=0, dispatches_before=dispatches_before,
                dispatches_after=await backend.dispatches(),
                resource_version_before=before.resource_version,
                resource_version_after=(await backend.deployment()).resource_version)
        else:
            if await backend.dispatches() != dispatches_before:
                raise ValueError('rollback dispatched before operator decision')
            operator_decision = 'reject' if reject_run else 'approve'
            print(('On-call rejection required: ' if reject_run else 'On-call approval required: ')
                + approval_command(decision, getattr(backend, 'controller_store', '/artifacts/remediation'),
                                   decision=operator_decision), flush=True)
            if approve_as is not None:
                # Explicit rehearsal opt-in only; the controller still checks
                # this same immutable approval and all executor guards.
                record_approval(backend.store, proposal_id=decision['proposal_id'], approved_by=approve_as,
                    decision=operator_decision, reason='explicit demo rehearsal --approve-as',
                    confirm_oncall_approval=True, now=now())
            if reject_run:
                rejected_deployment = await backend.deployment()
                async def rejection_read():
                    if (await backend.dispatches() != dispatches_before
                        or await backend.action(decision) is not None):
                        raise ValueError('action observed during operator rejection')
                    current = await backend.deployment()
                    if (current.image, current.generation) != (rejected_deployment.image, rejected_deployment.generation):
                        raise ValueError('Payment changed during operator rejection')
                    return await backend.rejection(decision)
                rejection = await wait_for(rejection_read, lambda v: v is not None,
                    timeout=timeout, poll=poll, clock=clock, sleep=sleep)
                end = clock() + timeout
                while clock() < end:
                    await sleep(min(poll, end - clock()))
                    await rejection_read()
                receipt.update(phase='engineer', reason='approval-rejected', rejection=rejection,
                    mutation_count=0, dispatches_before=dispatches_before, dispatches_after=await backend.dispatches(),
                    regression_image=backend.regression_image, change_ref=change['change_ref'])
            else:
                await complete_action(backend, decision, before, good, receipt, change,
                    failed_verify_run=failed_verify_run, timeout=timeout, poll=poll,
                    clock=clock, sleep=sleep, now=now, dispatches_before=dispatches_before)
        receipt['completed_at'] = timestamp(now())
        with (output / f'{number}.json').open('x') as stream:
            json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
        receipts.append(receipt)
    return receipts


async def complete_action(backend, decision, before, good, receipt, change, *, failed_verify_run,
                          timeout, poll, clock, sleep, now, dispatches_before):
    fault = None
    cleanup = None
    try:
        async def action_read():
            nonlocal fault
            state = await backend.action(decision)
            if state is None:
                return None
            intent, facts = state['intent'], state['facts']
            evidence = intent.get('evidence') or {}
            if (intent['request']['incident_id'] != decision['incident_id']
                or intent['request']['incident_revision'] != decision['incident_revision']
                or intent['policy']['target'] != asdict(before.target)
                or evidence.get('before', {}).get('image') != backend.regression_image
                or evidence.get('known_good', {}).get('image') != good.image):
                raise ValueError('action is missing matching regression evidence')
            if failed_verify_run:
                if fault is None and state['phase'] == 'verifying':
                    fault = await backend.start_verify_fault(state)
                    if fault is not None:
                        fault['injected_at'] = timestamp(now())
                if fault is not None:
                    await backend.hold_verify_fault(fault)
                if state['phase'] in ('recovered', 'blocked', 'escalated'):
                    if (state['phase'] != 'escalated' or fault is None
                        or facts.get('reason') not in ('verification-timeout-target-quarantined',
                            'telemetry-inconclusive-target-quarantined', 'verification-expired-during-source-read')
                        or facts.get('telemetry', {}).get('status') == 'telemetry_verified'):
                        raise ValueError('failed verification requires a fault and telemetry escalation (C-R08)')
                    return state
                return None
            if state['phase'] in ('blocked', 'escalated'):
                raise ValueError('approved action did not recover: ' + state['phase'])
            if state['phase'] != 'recovered':
                return None
            if facts.get('telemetry', {}).get('status') != 'telemetry_verified':
                raise ValueError('recovered action is missing matching regression/telemetry evidence')
            return state
        action = await wait_for(action_read, lambda v: v is not None,
            timeout=timeout, poll=poll, clock=clock, sleep=sleep)
        after = await backend.deployment()
        if not after.ready_for(good.image, prior_generation=before.generation, old_pods=()):
            raise ValueError('Payment has not returned to the ready known-good image')
        dispatches_after = await backend.dispatches()
        if dispatches_after - dispatches_before != 1:
            raise ValueError('demo requires exactly one rollback dispatch per round')
        receipt.update(regression_image=backend.regression_image, change_ref=change['change_ref'],
            action_id=action['action_id'],
            action_revision=action['revision'], last_event_hash=action['last_event_hash'],
            phase=action['phase'], reason=action['facts'].get('reason'), telemetry=action['facts'].get('telemetry'),
            dispatches_before=dispatches_before, dispatches_after=dispatches_after)
        receipt['verdict_observed_at'] = timestamp(now())
    finally:
        if failed_verify_run:
            # Also clean up uncertain POSTs, timeouts, cancellation and bad verdicts.
            cleanup = await backend.clear_verify_fault()
    if failed_verify_run:
        receipt.update(constraint='C-R08', fault=fault, fault_cleanup=cleanup,
                       fault_cleared_at=timestamp(now()))


class Backend:
    def __init__(self, args, pool, kube, registry, *, observer=None, source=None, fault_kube=None):
        self.args, self.pool, self.kube, self.registry = args, pool, kube, registry
        self.regression_image = args.regression_image
        self.store = ArtifactStore(args.store)
        self.controller_store = getattr(args, 'controller_store', Path('/artifacts/remediation'))
        self.journal = ActionJournal(pool)
        self.observer, self.source = observer, source
        self.fault_kube, self.pending_fault = fault_kube, None

    async def known_good(self, instant):
        return await self.registry.resolve(self.kube.target, incident_at=instant)

    async def deployment(self, *, inflight=False):
        deployment = await self.kube.observe()
        deployment.validate(Policy(self.kube.target), before_dispatch=not inflight)
        return deployment

    async def deploy(self, before):
        review = DeploymentChangeReview(self.source, self.observer,
            ArtifactStore(self.args.deployment_change_store), before.target, environment=self.args.environment)
        baseline = await review.open()
        if (await self.kube.observe()).resource_version != before.resource_version:
            raise ValueError('Payment changed before regression injection')
        await self.kube.patch(before, self.regression_image, dry_run=True)
        await self.kube.patch(before, self.regression_image, dry_run=False)
        return await wait_for(lambda: review.complete(baseline['baseline_ref']),
            lambda change: change['status'] == 'CHANGE_OBSERVED', timeout=150, poll=2,
            clock=time.monotonic, sleep=asyncio.sleep)

    def kill_active(self):
        return self.args.kill_switch.exists()

    async def decisions(self):
        records = []
        for path in sorted((self.store.root / 'manifests').glob('*.json')):
            if not re.fullmatch(r'[0-9a-f]{64}', path.stem):
                continue
            ref = f'artifact://manifests/{path.name}'
            card = self.store.read(ref)
            if (card.get('proposal_version') == PROPOSAL_VERSION
                or card.get('decision_version') == 'runbook-decision/v1' and card.get('decision') == 'engineer'):
                records.append((ref, card))
        return records

    async def incident_after(self, decision, started):
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval('''SELECT EXISTS(SELECT FROM sentinel_control.incidents
                WHERE incident_id=$1 AND created_at >= $2::timestamptz)''',
                decision['incident_id'], datetime.fromisoformat(started.replace('Z', '+00:00'))))

    async def dispatches(self):
        async with self.pool.acquire() as conn:
            return await conn.fetchval('SELECT count(*) FROM sentinel_remediation.dispatches WHERE target_key=$1',
                self.kube.target.key)

    async def action(self, decision):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''SELECT action_id FROM sentinel_remediation.intents
                WHERE incident_id=$1 AND incident_revision=$2 AND target_key=$3''',
                decision['incident_id'], decision['incident_revision'], self.kube.target.key)
        if len(rows) > 1:
            raise ValueError('multiple actions for one incident revision')
        return await self.journal.get(rows[0]['action_id']) if rows else None

    async def rejection(self, decision):
        pid = decision['proposal_id']
        approval = read_slot(self.store, 'approval', pid)
        outcome = read_slot(self.store, 'outcome', pid)
        if approval is None or outcome is None:
            return None
        if (approval.get('approval_version') != 'runbook-approval/v1'
            or approval.get('proposal_sha256') != stable_id(decision) or approval.get('decision') != 'reject'
            or approval.get('proposal_id') != pid or not approval.get('approved_by')
            or outcome.get('outcome_version') != 'runbook-approval-outcome/v1'
            or outcome.get('proposal_id') != pid or outcome.get('incident_id') != decision['incident_id']
            or outcome.get('incident_revision') != decision['incident_revision']
            or outcome.get('phase') != 'escalated' or outcome.get('reason') != 'approval-rejected'
            or read_slot(self.store, 'used', pid) is not None):
            raise ValueError('missing matching unused rejection and controller escalation')
        return {'approval': approval, 'outcome': outcome,
            'approval_ref': f'artifact://manifests/{stable_id(approval)}.json',
            'outcome_ref': f'artifact://manifests/{stable_id(outcome)}.json'}

    async def start_verify_fault(self, action):
        """Select the ready replacement by UID, never a service or an old port-forward."""
        if self.fault_kube is None:
            raise ValueError('failed verification requires an explicit fault credential')
        evidence, facts = action['intent']['evidence'], action['facts']
        before, good = evidence['before'], evidence['known_good']['image']
        patch = facts.get('patch_receipt') or {}
        if (action['phase'] != 'verifying' or patch.get('image') != good
            or patch.get('generation') != before['generation'] + 1):
            raise ValueError('fault requires a sent rollback PATCH receipt')
        current = await self.deployment(inflight=True)
        if not current.ready_for(good, prior_generation=before['generation'],
                                 old_pods=[p['uid'] for p in before['pods']]):
            return None
        if current.generation != before['generation'] + 1:
            raise ValueError('unexpected generation before fault injection')
        base = '/api/v1/namespaces/' + current.target.namespace + '/pods'
        inventory = await self.fault_kube._request('GET', base, params={'limit': '100'})
        if inventory.get('metadata', {}).get('continue'):
            raise ValueError('incomplete fault pod inventory')
        pods = [p for p in inventory['items'] if p['metadata']['uid'] == current.pods[0].uid]
        if len(pods) != 1:
            raise ValueError('replacement fault pod is missing')
        name = pods[0]['metadata']['name']
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', name):
            raise ValueError('invalid fault pod name')
        fault = {'pod_uid': current.pods[0].uid, 'pod_name': name, 'image': good,
                 'generation': current.generation, 'mode': 'error', 'delay_ms': 0}
        path = base + '/' + name + ':8000/proxy/internal/faults'
        original = await self.fault_kube._request('GET', path)
        if original.get('mode') != 'healthy' or original.get('delay_ms') != 0:
            raise ValueError('replacement pod already has a fault')
        # Track before POST: a lost response must still trigger cleanup.
        self.pending_fault = (path, fault)
        await self._set_fault(path, 'error')
        return fault

    async def _set_fault(self, path, mode):
        result = await self.fault_kube._request('POST', path, json={'mode': mode, 'delay_ms': 0})
        if result.get('mode') != mode or result.get('delay_ms') != 0:
            raise ValueError('fault endpoint did not acknowledge requested mode')
        return result

    async def hold_verify_fault(self, fault):
        current = await self.deployment()
        if (current.image != fault['image'] or current.generation != fault['generation']
            or [p.uid for p in current.pods] != [fault['pod_uid']]):
            raise ValueError('replacement pod changed during failed verification')
        state = await self.fault_kube._request('GET', self.pending_fault[0])
        if state.get('mode') != 'error' or state.get('delay_ms') != 0:
            raise ValueError('verification fault was cleared before verdict')

    async def clear_verify_fault(self):
        if self.pending_fault is None:
            return None
        path, fault = self.pending_fault
        await self._set_fault(path, 'healthy')
        state = await self.fault_kube._request('GET', path)
        if state.get('mode') != 'healthy' or state.get('delay_ms') != 0:
            raise ValueError('fault cleanup was not verified')
        self.pending_fault = None
        return {'pod_uid': fault['pod_uid'], 'mode': 'healthy', 'delay_ms': 0}


async def run(args):
    if args.failed_verify_run and args.fault_token_file is None:
        raise ValueError('--failed-verify-run requires --fault-token-file (pods/proxy access)')
    image_digest(args.regression_image)
    dsn = os.environ.get('SENTINELOPS_ACTION_READ_DATABASE_URL', '')
    selected = urlsplit(dsn)
    if (selected.scheme not in ('postgresql', 'postgres') or not selected.hostname
        or selected.path != '/sentinelops_control' or selected.fragment
        or parse_qs(selected.query) != {'sslmode': ['verify-full']}):
        raise ValueError('explicit read-only database with sslmode=verify-full required')
    target = Target(**json.loads(args.target.read_text()))
    policy = json.loads(args.health_policy.read_text())
    pool = kube = observer = fault_kube = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=5, command_timeout=5)
        await ActionReader(pool).check_ready()
        async with pool.acquire() as conn:
            stream = await conn.fetchrow('SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1',
                args.health_stream)
        identity = validate_stream(stream, policy)
        if (identity['environment'] != args.environment
            or identity['artifact_root_hash'] != stable_id({'root': str(args.source_store.resolve())})):
            raise ValueError('health environment/store binding differs')
        source = HealthLedgerReader(pool, ArtifactStore(args.source_store), args.health_stream, policy)
        registry = KnownGoodRegistry(ArtifactStore(args.review_store), args.ledger_ref,
            approved_by=args.approved_by, source=source, environment=args.environment)
        kube = LocalKubernetes(args.server, target, token_file=args.token_file, ca_file=args.ca_file)
        observer = LocalKubernetes(args.server, target, token_file=args.observer_token_file,
            ca_file=args.observer_ca_file)
        if args.failed_verify_run:
            fault_kube = LocalKubernetes(args.server, target, token_file=args.fault_token_file, ca_file=args.ca_file)
        return await run_demo(Backend(args, pool, kube, registry, observer=observer, source=source,
            fault_kube=fault_kube), output=args.output, kill_switch_run=args.kill_switch_run,
            failed_verify_run=args.failed_verify_run, reject_run=args.reject_run,
            approve_as=args.approve_as, timeout=args.timeout, poll=args.poll_seconds)
    finally:
        try:
            if kube is not None:
                await kube.close()
        finally:
            try:
                if observer is not None:
                    await observer.close()
            finally:
                try:
                    if fault_kube is not None:
                        await fault_kube.close()
                finally:
                    if pool is not None:
                        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('regression-image', 'server', 'health-stream', 'environment', 'ledger-ref', 'approved-by'):
        parser.add_argument('--' + name, required=True)
    for name in ('source-store', 'review-store', 'store', 'deployment-change-store', 'target',
                 'token-file', 'ca-file', 'observer-token-file', 'observer-ca-file', 'kill-switch'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--health-policy', type=Path, default=Path('configs/slo_health_policy.json'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/aws-demo/runs'))
    parser.add_argument('--timeout', type=float, default=1200.)
    parser.add_argument('--poll-seconds', type=float, default=10.)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--kill-switch-run', action='store_true')
    modes.add_argument('--failed-verify-run', action='store_true')
    modes.add_argument('--reject-run', action='store_true')
    parser.add_argument('--fault-token-file', type=Path,
                        help='explicit credential for GET pods and GET/POST pods/proxy; failed-verify only')
    parser.add_argument('--approve-as', help='explicit on-call identity for automated rehearsal approval')
    parser.add_argument('--controller-store', type=Path, default=Path('/artifacts/remediation'),
                        help='store path inside the controller pod for the printed approval command')
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, default=str))
    except Exception as exc:
        raise SystemExit('runbook demo failed: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
