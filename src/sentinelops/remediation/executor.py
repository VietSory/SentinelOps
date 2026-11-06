"""Bounded one-attempt rollback with durable intent-before-side-effect.

No public dispatch endpoint or enabled production action policy is installed.
The internal H4 provider must replay accepted Kubernetes/model/change evidence;
Docker-only or unqualified evidence remains blocked.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import logging

from opentelemetry import metrics

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.rca import validate_policy
from sentinelops.remediation.authority import qualify
from sentinelops.remediation.domain import ActionConflict, Blocked, KubernetesUnavailable, Request, RUNBOOK
from sentinelops.remediation.registry import deployment_from_projection
from sentinelops.remediation.verification import verify_post_action_health, validate_current_support
from sentinelops.telemetry.core import stable_id, timestamp

logger=logging.getLogger('sentinelops-remediation')


class LedgerRecovery:
    def __init__(self,source,target,*,environment='local'):
        self.source,self.target,self.environment=source,target,environment

    async def verify(self,*,effective_at,now,target):
        if target!=self.target: raise Blocked('recovery-health-target-mismatch')
        return await verify_post_action_health(self.source,effective_at=effective_at,as_of=now,
            expected_environment=self.environment,max_batches=10000,require_current=True)


class RollbackExecutor:
    def __init__(self,journal,kubernetes,authority,registry,recovery,policy,rca_policy,*,
                 enabled=lambda:False,clock=lambda:datetime.now(timezone.utc)):
        validate_policy(rca_policy)
        if registry is not None and (registry.source.stream_id!=recovery.source.stream_id
            or authority.health_stream_id!=recovery.source.stream_id or recovery.target!=policy.target
            or stable_id(registry.source.policy)!=stable_id(recovery.source.policy)
            or registry.environment!=recovery.environment):
            raise ValueError('rollback health source/target/environment binding mismatch')
        self.journal,self.kube,self.authority,self.registry,self.recovery=journal,kubernetes,authority,registry,recovery
        self.policy,self.rca_policy,self.enabled,self.clock=policy,json.loads(json.dumps(rca_policy)),enabled,clock
        self.counter=metrics.get_meter('sentinelops-remediation').create_counter('sentinelops_remediation_state_observations_total')

    def _emit(self,state):
        logger.info(json.dumps({'event':'action_state_observed','action_id':state['action_id'],
            'revision':state['revision'],'phase':state['phase']},sort_keys=True))
        self.counter.add(1,{'phase':state['phase'],'runbook':RUNBOOK})
        return state

    def _enabled(self):
        if self.policy.enabled is not True or self.enabled() is not True:
            raise Blocked('runbook-disabled-or-kill-switch')

    async def _prepare(self,request):
        self._enabled()
        assessment=await self.authority.assess(request,self.policy.target,now=self.clock())
        if assessment.dispatch_authority is not True: raise Blocked(assessment.missing_reason)
        validate_payload('rca-result.schema.json',assessment.rca)
        if self.registry is None: raise Blocked('known-good-ledger-unavailable')
        group=next(g for g in assessment.rca['correlation']['groups'] if g['group_id']==assessment.rca['group_id'])
        good=await self.registry.resolve(self.policy.target,incident_at=utc_time(group['first_onset_at']))
        before=await self.kube.observe()
        before.validate(self.policy)
        qualify(assessment,request,before,good,self.policy,self.rca_policy,now=self.clock())
        self._enabled()
        await self.kube.patch(before,good.image,dry_run=True)
        evidence={'before':before.projection(),'known_good':asdict(good),'rca_ref':assessment.result_ref,
                  'rca_result_id':assessment.rca['result_id'],'health_stream_id':self.recovery.source.stream_id,
                  'health_policy_sha256':stable_id(self.recovery.source.policy),'health_environment':self.recovery.environment}
        return before,good,assessment,evidence

    async def execute(self,request):
        state=await self.journal.find(request,self.policy.target)
        prepared=None
        if state is None:
            try:
                prepared=await self._prepare(request)
            except Blocked as exc:
                return self._emit(await self.journal.propose(request,self.policy,blocked_reason=str(exc)))
            state=await self.journal.propose(request,self.policy,evidence=prepared[3])
            self._emit(state)
        if state['phase']!='prepared': return await self.reconcile(state['action_id'])
        try:
            if state['intent']['policy_sha256']!=self.policy.identity: raise Blocked('prepared-policy-changed')
            before,good,assessment,evidence=prepared or await self._prepare(request)
            pinned=json.loads(json.dumps(state['intent']['evidence']))
            comparison=json.loads(json.dumps(evidence))
            pinned['before'].pop('observed_at');comparison['before'].pop('observed_at')
            if comparison!=pinned: raise Blocked('prepared-evidence-or-deployment-changed')
            state=await self.journal.arm(state['action_id'],state['revision'])
            self._emit(state)
        except Blocked as exc:
            return self._emit(await self.journal.transition(state['action_id'],state['revision'],
                'blocked',{'reason':str(exc)},release=True))
        except ActionConflict:
            return await self.reconcile(state['action_id'])
        # A dispatch record consumes the sole attempt BEFORE HTTP. A crash here
        # is conservatively ambiguous: recovery observes state, never resends.
        try:
            latest=await self.authority.assess(request,self.policy.target,now=self.clock())
            qualify(latest,request,before,good,self.policy,self.rca_policy,now=self.clock())
            if latest.result_ref!=assessment.result_ref or not await self.journal.incident_current(request):
                raise Blocked('incident-or-rca-changed-before-dispatch')
            if self.clock()-utc_time(state['facts']['dispatch_started_at'])>timedelta(seconds=self.policy.verification_seconds):
                raise Blocked('dispatch-expired-before-send')
            self._enabled()  # Recheck immediately before handing the request to transport.
        except Blocked as exc:
            return self._emit(await self.journal.transition(state['action_id'],state['revision'],'escalated',
                {**state['facts'],'reason':str(exc),'dispatch_cancelled_before_send':True},release=True))
        try:
            receipt=await self.kube.patch(before,good.image,dry_run=False)
        except KubernetesUnavailable:
            return self._emit(await self.journal.transition(state['action_id'],state['revision'],'dispatching',
                {**state['facts'],'reason':'kubernetes-delivery-uncertain'}))
        except Blocked as exc:
            return self._emit(await self.journal.transition(state['action_id'],state['revision'],'escalated',
                {**state['facts'],'reason':str(exc)}))
        try:
            return self._emit(await self.journal.transition(state['action_id'],state['revision'],'verifying',
                {**state['facts'],'patch_receipt':receipt,'effective_at':None}))
        except ActionConflict:
            return await self.journal.get(state['action_id'])

    async def reconcile(self,action_id):
        try:
            return await self._reconcile(action_id)
        except Blocked as exc:
            current=await self.journal.get(action_id)
            if current is None or current['phase'] in ('blocked','recovered','escalated'): raise
            return self._emit(await self.journal.transition(action_id,current['revision'],'escalated',
                {**current['facts'],'reason':str(exc)}))
        except ActionConflict:
            current=await self.journal.get(action_id)
            if current is None: raise
            return current

    async def _reconcile(self,action_id):
        state=await self.journal.get(action_id)
        if state is None: raise ActionConflict('action not found')
        if state['phase'] in ('blocked','recovered','escalated','prepared'): return state
        now=self.clock();facts=state['facts']
        if now-utc_time(facts['dispatch_started_at'])>=timedelta(seconds=self.policy.verification_seconds):
            return self._emit(await self.journal.transition(action_id,state['revision'],'escalated',
                {**facts,'reason':'verification-timeout-target-quarantined'}))
        pinned_policy=dict(state['intent']['policy']);active_policy=asdict(self.policy)
        pinned_policy.pop('enabled');active_policy.pop('enabled')
        if pinned_policy!=active_policy:
            raise Blocked('inflight-policy-changed-use-original-controller-configuration')
        evidence=state['intent']['evidence']
        if (evidence['health_stream_id']!=self.recovery.source.stream_id
            or evidence['health_policy_sha256']!=stable_id(self.recovery.source.policy)
            or evidence['health_environment']!=self.recovery.environment):
            raise Blocked('inflight-health-source-binding-changed')
        before=deployment_from_projection({'observation_version':'payment-deployment/v1','observation':evidence['before']})
        good=evidence['known_good']['image']
        try:
            current=await self.kube.observe()
            current.validate(self.policy,before_dispatch=False)
            if current.image not in (before.image,good): raise Blocked('unexpected-external-image-change')
            expected_generation=before.generation if current.image==before.image else before.generation+1
            if current.generation!=expected_generation: raise Blocked('unexpected-external-generation-change')
        except Blocked as exc:
            return self._emit(await self.journal.transition(action_id,state['revision'],'escalated',{**facts,'reason':str(exc)}))
        if current.image==before.image:
            return state  # Could be an unsent crash or a request still in flight. Never guess/resend.
        if state['phase']=='dispatching':
            state=await self.journal.transition(action_id,state['revision'],'verifying',{**facts,'effective_at':None,
                'reconciled_rollout':current.projection()})
            self._emit(state);facts=state['facts']
        if not current.ready_for(good,prior_generation=before.generation,old_pods=[p.uid for p in before.pods]):
            return state
        if facts.get('effective_at') is None:
            state=await self.journal.transition(action_id,state['revision'],'verifying',{**facts,
                'effective_at':current.observed_at,'ready_rollout':current.projection()})
            self._emit(state);facts=state['facts']
        # Disabling a runbook cannot undo an already sent mutation. Continue
        # reconciliation/verification with the original policy, never compensate.
        cutoff=self.clock()
        verdict=await self.recovery.verify(effective_at=utc_time(facts['effective_at']),now=cutoff,target=self.policy.target)
        if verdict['status']=='telemetry_verified':
            ends=validate_current_support(verdict,source=self.recovery.source,environment=self.recovery.environment,
                effective_at=facts['effective_at'],as_of=cutoff)
            final=await self.kube.observe()
            final.validate(self.policy,before_dispatch=False)
            if final.image!=good or final.generation!=before.generation+1:
                raise Blocked('unexpected-external-generation-change')
            if not final.ready_for(good,prior_generation=before.generation,old_pods=[p.uid for p in before.pods]):
                return state
            completed=self.clock()
            if completed-utc_time(facts['dispatch_started_at'])>=timedelta(seconds=self.policy.verification_seconds):
                return self._emit(await self.journal.transition(action_id,state['revision'],'escalated',
                    {**facts,'reason':'verification-expired-during-source-read'}))
            expected=datetime.fromtimestamp(int((completed-timedelta(seconds=30)).timestamp())//60*60,timezone.utc)
            if ends[-1]!=expected: return state  # Minute advanced during verification; do not backdate completion.
            return self._emit(await self.journal.transition(action_id,state['revision'],'recovered',
                {**facts,'telemetry':verdict,'verified_rollout':final.projection(),
                 'verification_decided_at':timestamp(completed)},release=True))
        if verdict['status']=='inconclusive':
            return self._emit(await self.journal.transition(action_id,state['revision'],'escalated',
                {**facts,'telemetry':verdict,'reason':'telemetry-inconclusive-target-quarantined'}))
        if verdict['status']!='pending': raise Blocked('unknown-recovery-verdict')
        return state
