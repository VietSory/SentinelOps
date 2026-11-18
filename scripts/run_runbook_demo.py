"""Operator-run three-round Payment runbook demo; never approves a known-good image.

Requires the controller's durable decision store, original health/review stores,
an explicit read-only action database DSN in SENTINELOPS_ACTION_READ_DATABASE_URL,
and an explicit limited Payment Kubernetes credential. No ambient AWS/kubeconfig.
--kill-switch-run requires the controller's shared kill-switch file to exist and
a fresh kill-switch decision (arrange a pending incident before starting). It
never injects a regression or sends a PATCH, including dry-run PATCH.
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
import time
from urllib.parse import parse_qs, urlsplit

import asyncpg

from sentinelops.remediation.domain import Policy, Target, image_digest
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.kubernetes import LocalKubernetes
from sentinelops.remediation.reader import ActionReader
from sentinelops.remediation.registry import KnownGoodRegistry
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


def auto_decision(card):
    return (card.get('decision_version') == 'runbook-decision/v1'
        and card.get('decision') == 'auto' and card.get('runbook_id') == RUNBOOK_ID
        and card.get('executor_runbook') == 'payment-rollback-known-good/v1'
        and type(card.get('score')) in (int, float) and math.isfinite(card['score'])
        and .80 <= card['score'] <= 1 and type(card.get('margin')) in (int, float)
        and math.isfinite(card['margin']) and .15 <= card['margin'] <= 1
        and card.get('symptoms', {}).get('severity') == 'low')


async def run_demo(backend, *, output=Path('artifacts/aws-demo/runs'), kill_switch_run=False,
                   cooldown=COOLDOWN, timeout=1200., poll=10., clock=time.monotonic,
                   sleep=asyncio.sleep, now=lambda: datetime.now(timezone.utc)):
    """All external operations are injectable; receipts require observed evidence."""
    if (not math.isfinite(cooldown) or cooldown < COOLDOWN
        or not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(poll) or not 0 < poll <= 10):
        raise ValueError('invalid demo cooldown/deadline/poll bounds')
    output = Path(output)
    count = 3
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
                elif auto_decision(card):
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
            'kill_switch_run': kill_switch_run}
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
            async def action_read():
                state = await backend.action(decision)
                if state is None:
                    return None
                if state['phase'] in ('blocked', 'escalated'):
                    raise ValueError('auto action did not recover: ' + state['phase'])
                if state['phase'] != 'recovered':
                    return None
                intent, facts = state['intent'], state['facts']
                evidence = intent.get('evidence') or {}
                if (intent['request']['incident_id'] != decision['incident_id']
                    or intent['request']['incident_revision'] != decision['incident_revision']
                    or intent['policy']['target'] != asdict(before.target)
                    or evidence.get('before', {}).get('image') != backend.regression_image
                    or evidence.get('known_good', {}).get('image') != good.image
                    or facts.get('telemetry', {}).get('status') != 'telemetry_verified'):
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
                phase='recovered', telemetry=action['facts']['telemetry'],
                dispatches_before=dispatches_before, dispatches_after=dispatches_after)
        receipt['completed_at'] = timestamp(now())
        with (output / f'{number}.json').open('x') as stream:
            json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
        receipts.append(receipt)
    return receipts


class Backend:
    def __init__(self, args, pool, kube, registry, *, observer=None, source=None):
        self.args, self.pool, self.kube, self.registry = args, pool, kube, registry
        self.regression_image = args.regression_image
        self.store = ArtifactStore(args.store)
        self.journal = ActionJournal(pool)
        self.observer, self.source = observer, source

    async def known_good(self, instant):
        return await self.registry.resolve(self.kube.target, incident_at=instant)

    async def deployment(self):
        deployment = await self.kube.observe()
        deployment.validate(Policy(self.kube.target))
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
            if card.get('decision_version') == 'runbook-decision/v1':
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


async def run(args):
    image_digest(args.regression_image)
    if '/payment-regression@sha256:' not in args.regression_image:
        raise ValueError('select a digest-pinned payment-regression image')
    dsn = os.environ.get('SENTINELOPS_ACTION_READ_DATABASE_URL', '')
    selected = urlsplit(dsn)
    if (selected.scheme not in ('postgresql', 'postgres') or not selected.hostname
        or selected.path != '/sentinelops_control' or selected.fragment
        or parse_qs(selected.query) != {'sslmode': ['verify-full']}):
        raise ValueError('explicit read-only database with sslmode=verify-full required')
    target = Target(**json.loads(args.target.read_text()))
    policy = json.loads(args.health_policy.read_text())
    pool = kube = observer = None
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
        return await run_demo(Backend(args, pool, kube, registry, observer=observer, source=source), output=args.output,
            kill_switch_run=args.kill_switch_run, timeout=args.timeout, poll=args.poll_seconds)
    finally:
        try:
            if kube is not None:
                await kube.close()
        finally:
            try:
                if observer is not None:
                    await observer.close()
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
    parser.add_argument('--kill-switch-run', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, default=str))
    except Exception as exc:
        raise SystemExit('runbook demo failed: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
