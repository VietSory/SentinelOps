"""Operator-run Payment runbook demo (three rounds by default); never approves a known-good image.

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
from uuid import uuid4
import json
import math
import os
from pathlib import Path
import re
import shlex
import time
from urllib.parse import parse_qs, urlsplit

import asyncpg

from sentinelops.contracts import utc_time
from sentinelops.notifications import read_notification_record
from sentinelops.remediation.domain import Blocked, KubernetesUnavailable, Policy, Target, image_digest
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.kubernetes import LocalKubernetes
from sentinelops.remediation.reader import ActionReader
from sentinelops.remediation.registry import KnownGoodRegistry
from sentinelops.remediation.approvals import is_proposal, read_slot, record_approval
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.deployment_changes import DeploymentChangeReview
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream

COOLDOWN = 600
RUNBOOK_ID = 'payment-bad-deploy-rollback'


def _seconds(later, earlier):
    try:
        return round((datetime.fromisoformat(later.replace('Z', '+00:00'))
                      - datetime.fromisoformat(earlier.replace('Z', '+00:00'))).total_seconds())
    except (AttributeError, TypeError, ValueError):
        return None


def _round_warning(episodes, deploy, breach):
    return min((at for at in episodes if deploy and breach and _seconds(at, deploy) is not None
                and _seconds(at, deploy) >= 0 and _seconds(breach, at) >= 0), default=None)


def round_receipt(*, invocation, round_number, scenario, clocks, episodes, rca_top1, proposal, action,
                  dispatch_count, notifications, image_params, service='payment-service'):
    """Demo round receipt v2 (DEMO_UPGRADE_SPEC R5.4), checked by scripts.check_demo_round.

    The warning is the first episode of the root service that started after the deploy and
    no later than the first breach; derived timings are recomputed by the checker.
    """
    deploy, breach = clocks.get('deploy_started_at'), clocks.get('first_breach_at')
    started = _round_warning(episodes.get(service, []), deploy, breach)
    redacted = {k: v for k, v in (proposal or {}).items() if k not in ('current_image', 'rollback_image', 'target')}
    return {'receipt_version': 'runbook-demo-round/v2', 'runner_invocation_id': invocation, 'round': round_number,
            'scenario': scenario, **{k: clocks.get(k) for k in (
                'deploy_started_at', 'change_observed_at', 'first_breach_at', 'incident_created_at',
                'proposal_created_at', 'approved_at', 'recovered_at')},
            'warning': {'service': service, 'episode_started_at': started},
            'lead_seconds': _seconds(breach, started) if started else None,
            'deploy_to_breach_seconds': _seconds(breach, deploy), 'rca_top1': rca_top1, 'proposal': redacted,
            'action': dict(action), 'dispatch_count': dispatch_count, 'notifications': dict(notifications),
            'image_params': dict(image_params), 'demo_mode': True, 'q09_eligible': False}


def round_notifications(root, *, proposal_id, service, warning, started):
    keys = {'early_warning': f'warning:{service}:{warning}' if warning else None,
            'proposal_pending': f'proposal:{proposal_id}',
            'outcome': f'outcome:{proposal_id}:recovered'}
    counts = dict.fromkeys(keys, 0)
    for kind, key in keys.items():
        if key is None:
            continue
        record = read_notification_record(root, key)
        if (record and record.get('kind') == kind and record.get('key') == key
                and record.get('message_id') not in (None, '', 'seeded-existing-event', 'disabled')
                and utc_time(record['sent_at']) >= utc_time(started)):
            counts[kind] = 1
    return counts


async def wait_for(read, accept, *, timeout, poll, clock, sleep):
    deadline = clock() + timeout
    while True:
        try:
            value = await read()
        except KubernetesUnavailable:
            # A rolling Deployment can change between the ReplicaSet and Pod reads, which the
            # client reports as unavailable. Retry until the deadline; re-raise only then.
            if deadline - clock() <= 0:
                raise
        else:
            if accept(value):
                return value
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('runbook demo evidence deadline exceeded')
        await sleep(min(poll, remaining))


def proposal_decision(card):
    return (card.get('decision_version') == 'runbook-decision/v1'
        and is_proposal(card)
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
                   failed_verify_run=False, reject_run=False, rounds=3,
                   cooldown=COOLDOWN, timeout=1200., poll=10., clock=time.monotonic,
                   sleep=asyncio.sleep, now=lambda: datetime.now(timezone.utc)):
    """All external operations are injectable; receipts require observed evidence."""
    if (not math.isfinite(cooldown) or cooldown < COOLDOWN
        or not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(poll) or not 0 < poll <= 10):
        raise ValueError('invalid demo cooldown/deadline/poll bounds')
    if type(rounds) is not int or not 1 <= rounds <= 3:
        raise ValueError('demo rounds must be an integer from 1 to 3')
    output = Path(output)
    if approve_as is not None and (not isinstance(approve_as, str) or not approve_as.strip()):
        raise ValueError('approve-as must name the on-call operator')
    if sum((kill_switch_run, failed_verify_run, reject_run)) > 1:
        raise ValueError('negative demo modes are mutually exclusive')
    count = 1 if failed_verify_run or reject_run else rounds
    output.mkdir(parents=True, exist_ok=True)
    # Refuse reuse before doing anything that could mutate Payment.
    if any((output / f'{n}.json').exists() for n in range(1, count + 1)):
        raise FileExistsError('demo receipts already exist; choose a new output directory')
    receipts, consumed = [], set()
    invocation = uuid4().hex
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
                if not failed_verify_run and getattr(backend, 'round_facts_enabled', False):
                    facts = await backend.round_facts(decision, started, receipt)
                    receipt['round_v2'] = round_receipt(invocation=invocation, round_number=number,
                        scenario=backend.scenario, clocks={'deploy_started_at': started,
                            'change_observed_at': change.get('observed_at'), **facts['clocks']},
                        episodes=facts['episodes'], rca_top1=facts['rca_top1'], proposal=decision,
                        action={'phase': receipt.get('phase'),
                                'telemetry_verified': (receipt.get('telemetry') or {}).get('status') == 'telemetry_verified'},
                        dispatch_count=receipt['dispatches_after'] - receipt['dispatches_before'],
                        notifications=facts['notifications'], image_params=backend.image_params)
        receipt['completed_at'] = timestamp(now())
        with (output / f'{number}.json').open('x') as stream:
            json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
        if 'round_v2' in receipt:
            with (output / f'round-{number}.json').open('x') as stream:
                json.dump(receipt['round_v2'], stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
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
    round_facts_enabled = False
    scenario = 'abrupt-regression'
    image_params = {}

    def __init__(self, args, pool, kube, registry, *, observer=None, source=None, fault_kube=None):
        self.args, self.pool, self.kube, self.registry = args, pool, kube, registry
        self.regression_image = args.regression_image
        self.store = ArtifactStore(args.store)
        self.controller_store = getattr(args, 'controller_store', Path('/artifacts/remediation'))
        self.journal = ActionJournal(pool)
        self.observer, self.source = observer, source
        self.fault_kube, self.pending_fault = fault_kube, None
        self.prometheus = getattr(args, 'prometheus', None)
        if self.prometheus:
            self.round_facts_enabled = True
            self.scenario = getattr(args, 'scenario', None) or self.scenario
            self.image_params = json.loads(getattr(args, 'image_params', None) or '{}')

    def _prometheus(self, path, params):
        import httpx
        with httpx.Client(base_url=self.prometheus, timeout=10, trust_env=False) as client:
            body = client.get(path, params=params).json()
        if body.get('status') != 'success':
            raise ValueError('prometheus query failed')
        return body['data']['result']

    async def round_facts(self, decision, started, receipt):
        """Clocks, warning episodes and email counts for the v2 round receipt (WP5)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''SELECT i.created_at, v.payload FROM sentinel_control.incidents i
                JOIN sentinel_control.incident_versions v ON v.incident_id=i.incident_id AND v.revision=$2
                WHERE i.incident_id=$1''', decision['incident_id'], decision['incident_revision'])
        payload = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
        ranking = payload.get('root_cause_ranking') or []
        approval = read_slot(self.store, 'approval', decision['proposal_id'])
        start = datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp()
        end = datetime.now(timezone.utc).timestamp()
        service = ranking[0]['service'] if ranking else 'payment-service'
        series = await asyncio.to_thread(self._prometheus, '/api/v1/query_range', {
            'query': f'sentinelops_risk_episode_started_unixtime{{service="{service}"}}',
            'start': start - 1800, 'end': end, 'step': 60})
        episodes = sorted({datetime.fromtimestamp(float(v), timezone.utc).isoformat().replace('+00:00', 'Z')
                           for item in series for _, v in item['values']})
        onset = next((item.get('first_anomaly_at') for item in payload.get('service_signals') or []
                      if item.get('service') == service), None)
        warning = _round_warning(episodes, started, onset)

        async def read_notifications():
            return await asyncio.to_thread(round_notifications, self.store.root / 'notifier',
                proposal_id=decision['proposal_id'], service=service, warning=warning, started=started)

        try:
            sent = await wait_for(read_notifications, lambda counts: counts['outcome'] == 1,
                timeout=30, poll=2, clock=time.monotonic, sleep=asyncio.sleep)
        except TimeoutError:
            sent = await read_notifications()
        return {'clocks': {'first_breach_at': onset,
                           'incident_created_at': timestamp(row['created_at']),
                           'proposal_created_at': decision.get('created_at'),
                           'approved_at': approval.get('approved_at') if approval else None,
                           'recovered_at': receipt.get('verdict_observed_at')},
                'episodes': {service: episodes}, 'rca_top1': ranking[0]['service'] if ranking else None,
                'notifications': sent}

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
            if (is_proposal(card)
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
            approve_as=args.approve_as, rounds=getattr(args, 'rounds', 3),
            timeout=args.timeout, poll=args.poll_seconds)
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
    parser.add_argument('--rounds', type=int, choices=(1, 2, 3), default=3,
                        help='number of rounds (default: 3); reject/failed-verify modes always run once')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--kill-switch-run', action='store_true')
    modes.add_argument('--failed-verify-run', action='store_true')
    modes.add_argument('--reject-run', action='store_true')
    parser.add_argument('--fault-token-file', type=Path,
                        help='explicit credential for GET pods and GET/POST pods/proxy; failed-verify only')
    parser.add_argument('--prometheus', help='Prometheus URL; enables the v2 round receipt (warning lead, emails)')
    parser.add_argument('--scenario', choices=('gradual-regression', 'abrupt-regression'), default='abrupt-regression')
    parser.add_argument('--image-params', help='JSON of the regression image parameters, recorded in the receipt')
    parser.add_argument('--approve-as', help='explicit on-call identity for automated rehearsal approval')
    parser.add_argument('--controller-store', type=Path, default=Path('/artifacts/remediation'),
                        help='store path inside the controller pod for the printed approval command')
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, default=str))
    except Exception as exc:
        raise SystemExit('runbook demo failed: ' + type(exc).__name__ + safe_reason(exc)) from None


# Fixed guard strings. Library ValueErrors (asyncpg configuration, schema validation) can
# echo DSNs or rejected values, so only literals that appear verbatim in a raise of this
# driver or of the sentinelops package are printed; anything else prints its class only.
def _raise_literals(paths, names):
    pattern = re.compile(r"raise (?:" + "|".join(names) + r")\('([^'\\]+)'\)")
    found = set()
    for path in paths:
        try:
            found.update(pattern.findall(path.read_text(encoding='utf-8')))
        except OSError:
            continue
    return frozenset(found)


import sentinelops as _package
GUARD_MESSAGES = _raise_literals([Path(__file__)], ('ValueError', 'TimeoutError'))
DOMAIN_CODES = _raise_literals(Path(_package.__file__).parent.rglob('*.py'), ('Blocked', 'KubernetesUnavailable'))
TERMINAL_PHASES = ('blocked', 'escalated', 'recovered', 'verifying', 'dispatching', 'prepared')


def safe_reason(exc):
    message = str(exc)
    driver = type(exc) in (ValueError, TimeoutError)
    if ((driver and message in GUARD_MESSAGES)
            or (isinstance(exc, (Blocked, KubernetesUnavailable)) and message in DOMAIN_CODES)
            or (type(exc) is ValueError
                and message in {'approved action did not recover: ' + phase for phase in TERMINAL_PHASES})):
        return ': ' + message
    return ''


if __name__ == '__main__':
    main()
