"""Single-writer auto dispatcher over verified native incident evidence.

Decisions are immutable artifacts, including manual/failed decisions. Restarts
only reconcile an existing intent; a decision without an intent is never replayed.
Run one controller for a target/store (the action journal also enforces its
durable incident attempt budget and target lock).
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.incidents.rca_replay import group_severity
from sentinelops.remediation.domain import Blocked, Request, RUNBOOK
from sentinelops.telemetry.core import stable_id, timestamp

logger = logging.getLogger('sentinelops-remediation-controller')
TERMINAL = {'blocked', 'recovered', 'escalated'}
SEVERITIES = ('low', 'medium', 'high', 'critical')


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
            if value['decision'] == 'auto':
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
        if decision['decision'] == 'auto' and (derived['severity'] != 'low' or rca['abstained']
                                               or decision['executor_runbook'] != RUNBOOK):
            raise Blocked('auto-decision-not-qualified')
        return deepcopy(decision)

    def _engineer(self, reason):
        return {'decision_version': 'runbook-decision/v1', 'decision': 'engineer',
                'runbook_id': None, 'executor_runbook': None, 'score': None, 'margin': None,
                'reasons': [reason], 'ranking': [], 'symptoms': None,
                'catalog_sha256': stable_id(self.catalog), 'fingerprint': stable_id([None, reason])}

    async def authorize(self, request):
        try:
            snapshot = await self.incidents.get(request.incident_id)
            if snapshot['revision'] != request.incident_revision:
                raise Blocked('incident-revision-changed')
            decision = await self._decide(snapshot)
            pinned = self._decisions.get(request.incident_id)
            if (decision['decision'] != 'auto' or pinned is None
                or request != self._request(pinned)
                or decision['runbook_id'] != pinned['runbook_id']
                or decision['executor_runbook'] != pinned['executor_runbook']):
                raise Blocked('current-runbook-decision-not-auto')
            if self.kill_switch.exists():
                raise Blocked('runbook-disabled-or-kill-switch')
        except Blocked:
            raise
        except Exception as exc:
            raise Blocked('controller-authorization-evidence-unavailable') from exc

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
            actions = 0
            while True:
                page = await self.incidents.list_current(limit=100, after=after)
                for snapshot in page[:100]:
                    identifier = snapshot['incident_id']
                    if identifier in self._decisions or snapshot['context']['lifecycle_stage'] != 'RCA_READY':
                        continue
                    if actions >= self.max_actions_per_tick:
                        continue
                    try:
                        decision = await self._decide(snapshot)
                    except Exception as exc:
                        reason = str(exc) if isinstance(exc, Blocked) else 'symptom-extraction-or-evidence-failed'
                        decision = self._engineer(reason)
                    decision.update(controller_version='auto-runbook-controller/v1', incident_id=identifier,
                                    incident_revision=snapshot['revision'], decided_at=timestamp(self.clock()))
                    reference = self.store.write('manifests', decision)
                    self._decisions[identifier] = deepcopy(decision)
                    logger.info(json.dumps({'event': 'runbook_decision', 'decision_ref': reference, **decision}, sort_keys=True))
                    results.append(deepcopy(decision))
                    if decision['decision'] == 'auto':
                        actions += 1
                        try:
                            state = await self.executor.execute(self._request(decision))
                        except Exception:
                            # Intent/dispatch may have committed before a lost
                            # reply. Next tick discovers it without resending.
                            self._loaded = False
                            raise
                        results.append(state)
                        if state['phase'] not in TERMINAL:
                            self._pending[state['action_id']] = state
                if len(page) <= 100:
                    break
                last = page[99]
                after = (utc_time(last['created_at']), last['incident_id'])
            return results
