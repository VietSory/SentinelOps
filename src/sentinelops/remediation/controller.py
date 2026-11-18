"""Single-writer, on-call-approved dispatcher over verified incident evidence.

Decisions are immutable artifacts, including manual/failed decisions. Claimed
approvals are never replayed after restart; existing intents are reconciled.
Run one controller for a target/store (the action journal also enforces its
durable incident attempt budget and target lock).
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.incidents.rca_replay import group_severity
from sentinelops.remediation.approvals import APPROVAL_VERSION, PROPOSAL_VERSION, create_slot, read_slot
from sentinelops.remediation.domain import Blocked, Request, RUNBOOK, image_digest
from sentinelops.telemetry.core import stable_id, timestamp

logger = logging.getLogger('sentinelops-remediation-controller')
TERMINAL = {'blocked', 'recovered', 'escalated'}
SEVERITIES = ('low', 'medium', 'high', 'critical')
TARGET_BINDING = ('target', 'current_image', 'rollback_image', 'known_good_ledger_ref')


class AutoRunbookController:
    def __init__(self, *, incidents, evidence, executor, catalog, kill_switch: Path,
                 store, clock=lambda: datetime.now(timezone.utc), max_actions_per_tick=1):
        if type(max_actions_per_tick) is not int or not 1 <= max_actions_per_tick <= 100:
            raise ValueError('invalid controller action budget')
        self.incidents, self.evidence, self.executor = incidents, evidence, executor
        self.catalog, self.kill_switch, self.store = deepcopy(catalog), Path(kill_switch), store
        self.clock, self.max_actions_per_tick = clock, max_actions_per_tick
        self._lock = asyncio.Lock()
        self._decisions = {}
        self._pending = {}
        self._loaded = False
        self._active_approval = None
        self.executor.authorize = self.authorize

    @staticmethod
    def _request(decision):
        incident, revision, runbook = (decision[k] for k in ('incident_id', 'incident_revision', 'executor_runbook'))
        return Request(incident, revision, stable_id([incident, revision, runbook])[:32])

    async def _restore(self):
        if self._loaded:
            return
        # ArtifactStore.read checks the content hash; never trust a raw JSON file.
        for path in sorted((self.store.root / 'manifests').glob('*.json')):
            value = self.store.read('artifact://manifests/' + path.name)
            if value.get('controller_version') != 'auto-runbook-controller/v1':
                continue
            incident = value['incident_id']
            if incident in self._decisions and self._decisions[incident] != value:
                raise ValueError('conflicting immutable controller decisions')
            self._decisions[incident] = value
        for value in self._decisions.values():
            # Legacy auto intents may already have sent a mutation. Observe or
            # cancel them on restart; never create/replay an auto execution.
            if value['decision'] in ('auto', 'oncall'):
                state = await self.executor.journal.find(self._request(value), self.executor.policy.target)
                if state is not None and state['phase'] not in TERMINAL:
                    self._pending[state['action_id']] = state
        self._loaded = True

    async def _decide(self, snapshot):
        # Lazy import permits independent controller tests while lane match is
        # implementing the exact extract_symptoms/decide interface.
        from sentinelops.remediation import matching

        if self.kill_switch.exists():
            raise Blocked('runbook-disabled-or-kill-switch')
        identifier = snapshot['incident_id']
        async with self.evidence.pool.acquire() as conn:
            job_id = await conn.fetchval('''SELECT job_id FROM sentinel_control.incident_jobs
                WHERE incident_id=$1 AND consumer_id=$2''', identifier, self.evidence.consumer_id)
        if job_id is None:
            raise Blocked('incident-has-no-verified-native-job')
        job, accepted, derived = await self.evidence.verified_evidence(job_id)
        current = await self.incidents.get(identifier)
        _, checkpoint = await self.evidence.checkpoint()
        if current['revision'] != snapshot['revision']:
            raise Blocked('incident-revision-changed')
        if (current['context']['lifecycle_stage'] != 'RCA_READY'
            or current['context']['availability'] != 'ELIGIBLE'):
            raise Blocked('incident-lifecycle-or-availability-mismatch')
        rca = derived['rca']
        group = next(g for g in rca['correlation']['groups'] if g['group_id'] == rca['group_id'])
        if (identifier != 'INC-' + rca['group_id'] or job['incident_id'] != identifier
            or set(current['payload']['affected_services']) != set(group['affected_services'])
            or not {accepted['capture_ref'], accepted['result_ref']}.issubset(current['context']['source_refs'])):
            raise Blocked('current-incident-revision-does-not-bind-verified-rca')
        if any(checkpoint['service_states'][s]['availability'] != 'ELIGIBLE' for s in group['affected_services']):
            raise Blocked('current-incident-health-unavailable')
        if await self.evidence.source.next(checkpoint) is not None:
            raise Blocked('newer-accepted-health-awaits-consumer')
        intent = self.evidence.store.read(job['intent_ref'])
        observations = {}
        current_signals = {}
        for item in intent['opening_objects']:
            signal = item['signal']
            if signal['signal_id'] not in group['signal_ids']:
                continue
            service = signal['service']
            values = observations.setdefault(service, {})
            for ref in signal['evidence_refs']:
                values[ref] = self.evidence.store.read(ref)
            # Recompute severity with the latest verified consumer snapshot too.
            current_signals[signal['signal_id']] = (item['signal_ref'], checkpoint['service_states'][service])
        observations = {s: sorted(values.values(), key=lambda v: utc_time(v['bucket_end']))
                        for s, values in observations.items()}
        derived = deepcopy(derived)
        derived['severity'] = max((derived['severity'], current['payload']['severity'],
                                  group_severity(self.evidence.store, group, current_signals)),
                                 key=SEVERITIES.index)
        symptoms = matching.extract_symptoms(derived=derived, observations=observations)
        decision = matching.decide(symptoms, self.catalog)
        # Defense in depth: only the single executable runbook may dispatch.
        if decision['decision'] not in ('oncall', 'engineer'):
            raise Blocked('unsupported-runbook-decision')
        if decision['decision'] == 'oncall' and (rca['abstained']
                                               or decision['executor_runbook'] != RUNBOOK
                                               or decision['score'] < .80 or decision['margin'] < .15):
            raise Blocked('oncall-decision-not-qualified')
        if decision['decision'] == 'oncall':
            decision.update(await self._target_binding(group))
        return deepcopy(decision)

    async def _target_binding(self, group):
        """Read server-owned target/ledger evidence before publishing a proposal."""
        try:
            target = self.executor.policy.target
            good = await self.executor.registry.resolve(target, incident_at=utc_time(group['first_onset_at']))
            before = await self.executor.kube.observe()
            before.validate(self.executor.policy)
            if good.target != before.target or before.target != target:
                raise Blocked('target-identity-mismatch')
            return {'target': asdict(before.target), 'current_image': image_digest(before.image),
                    'rollback_image': image_digest(good.image), 'known_good_ledger_ref': good.ledger_ref}
        except Exception as exc:
            raise Blocked('proposal-target-unavailable') from exc

    @staticmethod
    def _check_target_binding(proposal, current):
        if any(field not in proposal or field not in current or proposal[field] != current[field]
               for field in TARGET_BINDING):
            raise Blocked('approval-target-changed')

    async def _prepared_target(self, request, proposal):
        # The executor may have read a different ledger between hook calls.
        # Bind the actual prepared PATCH inputs too, even if the live ledger
        # has since returned to the proposal's original value.
        state = await self.executor.journal.find(request, self.executor.policy.target)
        if state is None or state['phase'] not in ('prepared', 'dispatching'):
            return
        try:
            evidence = state['intent']['evidence']
            before, good = evidence['before'], evidence['known_good']
            if before['target'] != good['target']:
                raise Blocked('approval-target-changed')
            self._check_target_binding(proposal, {'target': before['target'], 'current_image': before['image'],
                'rollback_image': good['image'], 'known_good_ledger_ref': good['ledger_ref']})
        except (KeyError, TypeError) as exc:
            raise Blocked('approval-target-changed') from exc

    def _engineer(self, reason):
        return {'decision_version': 'runbook-decision/v1', 'decision': 'engineer',
                'runbook_id': None, 'executor_runbook': None, 'score': None, 'margin': None,
                'reasons': [reason], 'ranking': [], 'symptoms': None,
                'catalog_sha256': stable_id(self.catalog), 'fingerprint': stable_id([None, reason])}

    async def authorize(self, request):
        try:
            pinned = self._decisions.get(request.incident_id)
            if pinned is None or request != self._request(pinned):
                raise Blocked('current-runbook-decision-changed')
            await self._current(pinned)
            approval = self._approval(pinned)
            used = read_slot(self.store, 'used', pinned['proposal_id'])
            if (self._active_approval != (pinned['proposal_id'], stable_id(approval))
                or used is None or used['approval_sha256'] != stable_id(approval)):
                raise Blocked('approval-already-used-or-not-active')
            await self._prepared_target(request, pinned)
            if self.kill_switch.exists():
                raise Blocked('runbook-disabled-or-kill-switch')
        except Blocked:
            raise
        except Exception as exc:
            raise Blocked('controller-authorization-evidence-unavailable') from exc

    async def _current(self, proposal):
        try:
            snapshot = await self.incidents.get(proposal['incident_id'])
            if snapshot['revision'] != proposal['incident_revision']:
                raise Blocked('incident-revision-changed')
            decision = await self._decide(snapshot)
            if decision['decision'] != 'oncall':
                raise Blocked('current-runbook-decision-changed')
            self._check_target_binding(proposal, decision)
            if any(decision.get(k) != proposal.get(k) for k in decision):
                raise Blocked('current-runbook-decision-changed')
        except Blocked as exc:
            if str(exc) == 'proposal-target-unavailable':
                raise Blocked('approval-target-changed') from exc
            raise
        except Exception as exc:
            raise Blocked('controller-authorization-evidence-unavailable') from exc

    def _approval(self, proposal):
        try:
            return self._validated_approval(proposal)
        except Blocked:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            raise Blocked('approval-invalid') from exc

    def _validated_approval(self, proposal):
        now = self.clock()
        if now >= utc_time(proposal['expires_at']):
            raise Blocked('approval-expired')
        value = read_slot(self.store, 'approval', proposal['proposal_id'])
        if value is None:
            raise Blocked('approval-missing')
        if (value.get('approval_version') != APPROVAL_VERSION
            or value.get('proposal_id') != proposal['proposal_id']
            or value.get('proposal_sha256') != stable_id(proposal)
            or not isinstance(value.get('approved_by'), str) or not value['approved_by'].strip()
            or not utc_time(proposal['created_at']) <= utc_time(value['approved_at']) <= now
            or utc_time(value['approved_at']) >= utc_time(proposal['expires_at'])):
            raise Blocked('approval-invalid')
        if value.get('decision') != 'approve':
            raise Blocked('approval-rejected' if value.get('decision') == 'reject' else 'approval-invalid')
        return value

    def _escalate(self, proposal, reason, **extra):
        value = {'outcome_version': 'runbook-approval-outcome/v1',
                 'proposal_id': proposal['proposal_id'], 'incident_id': proposal['incident_id'],
                 'incident_revision': proposal['incident_revision'], 'phase': 'escalated',
                 'reason': reason, 'recorded_at': timestamp(self.clock()), **extra}
        create_slot(self.store, 'outcome', proposal['proposal_id'], value)
        logger.info(json.dumps({'event': 'runbook_approval_escalated', **value}, sort_keys=True))
        return value

    async def _approved_actions(self, results):
        actions = 0
        for proposal in self._decisions.values():
            if proposal.get('proposal_version') != PROPOSAL_VERSION:
                continue
            pid = proposal['proposal_id']
            if read_slot(self.store, 'outcome', pid) or read_slot(self.store, 'used', pid):
                continue
            try:
                approval = self._approval(proposal)
            except Blocked as exc:
                if str(exc) != 'approval-missing':
                    results.append(self._escalate(proposal, str(exc)))
                continue
            if actions >= self.max_actions_per_tick:
                continue
            try:
                await self._current(proposal)
            except Blocked as exc:
                results.append(self._escalate(proposal, str(exc)))
                continue
            # Claim before execute: a crash can lose an attempt, never reuse an
            # approval. Restarts only reconcile durable executor intents.
            create_slot(self.store, 'used', pid, {'usage_version': 'runbook-approval-used/v1',
                'proposal_id': pid, 'approval_sha256': stable_id(approval),
                'used_at': timestamp(self.clock())})
            self._active_approval = (pid, stable_id(approval))
            actions += 1
            try:
                state = await self.executor.execute(self._request(proposal))
            except Exception:
                self._loaded = False
                raise
            finally:
                self._active_approval = None
            if state['phase'] in ('blocked', 'escalated'):
                reason = state['facts'].get('reason', 'executor-blocked')
                if self.clock() >= utc_time(proposal['expires_at']):
                    reason = 'approval-expired'
                elif reason == 'evidence-stale-or-future':
                    reason = 'approval-after-evidence-freshness'
                results.append(self._escalate(proposal, reason, action_id=state['action_id']))
            results.append(state)
            if state['phase'] not in TERMINAL:
                self._pending[state['action_id']] = state

    async def tick(self) -> list[dict]:
        async with self._lock:
            await self._restore()
            results = []
            for identifier, state in list(self._pending.items()):
                if state['phase'] == 'prepared':
                    state = await self.executor.journal.transition(identifier, state['revision'], 'blocked',
                        {'reason': 'prepared-intent-requires-explicit-revalidation'}, release=True)
                else:
                    state = await self.executor.reconcile(identifier)
                results.append(state)
                if state['phase'] in TERMINAL:
                    del self._pending[identifier]
                else:
                    self._pending[identifier] = state
            after = None
            await self._approved_actions(results)
            while True:
                page = await self.incidents.list_current(limit=100, after=after)
                for snapshot in page[:100]:
                    identifier = snapshot['incident_id']
                    if identifier in self._decisions or snapshot['context']['lifecycle_stage'] != 'RCA_READY':
                        continue
                    try:
                        decision = await self._decide(snapshot)
                    except Exception as exc:
                        reason = str(exc) if isinstance(exc, Blocked) else 'symptom-extraction-or-evidence-failed'
                        decision = self._engineer(reason)
                    decision.update(controller_version='auto-runbook-controller/v1', incident_id=identifier,
                                    incident_revision=snapshot['revision'], decided_at=timestamp(self.clock()))
                    if decision['decision'] == 'oncall':
                        created = self.clock()
                        decision.update(proposal_version=PROPOSAL_VERSION,
                            proposal_id=stable_id([identifier, snapshot['revision'], decision['runbook_id']]),
                            created_at=timestamp(created), expires_at=timestamp(created + timedelta(minutes=15)))
                    reference = self.store.write('manifests', decision)
                    self._decisions[identifier] = deepcopy(decision)
                    logger.info(json.dumps({'event': 'runbook_proposal' if decision['decision'] == 'oncall'
                                            else 'runbook_decision', 'decision_ref': reference, **decision}, sort_keys=True))
                    results.append(deepcopy(decision))
                if len(page) <= 100:
                    break
                last = page[99]
                after = (utc_time(last['created_at']), last['incident_id'])
            return results
