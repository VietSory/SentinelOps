"""Qualified-authority software fixtures never constitute a production H4 grant."""
import asyncio
from copy import deepcopy
from dataclasses import asdict,replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from sentinelops.remediation.authority import Assessment,Change,NativeH4Authority
from sentinelops.remediation.domain import ActionConflict,Blocked,KubernetesUnavailable,Policy,Request
from sentinelops.remediation.executor import RollbackExecutor
from sentinelops.remediation.registry import KnownGood
from sentinelops.telemetry.core import stable_id,timestamp
from test_rca import AS_OF,END,REF,fixtures,evaluate,POLICY
from test_remediation_kubernetes import BAD,GOOD,TARGET,observation
from test_remediation_verification import POLICY as HEALTH_POLICY


class MemoryJournal:
    """Engine test double only. PostgreSQL acceptance covers actual locks/audit/CAS."""
    def __init__(self,clock): self.clock=clock;self.state=None;self.fail_arm=False;self.after_arm=None;self.current=True
    async def find(self,request,target): return deepcopy(self.state)
    async def get(self,key): return deepcopy(self.state)
    async def propose(self,request,policy,*,evidence=None,blocked_reason=None):
        if self.state is None:
            self.state={'action_id':'a'*64,'revision':1,'phase':'blocked' if blocked_reason else 'prepared',
                'facts':{'reason':blocked_reason} if blocked_reason else {},
                'intent':{'request':asdict(request),'policy':asdict(policy),'policy_sha256':policy.identity,'evidence':deepcopy(evidence)}}
        return deepcopy(self.state)
    async def arm(self,key,revision):
        if self.fail_arm: raise ConnectionError('fixture DB audit failure')
        if self.state['revision']!=revision or self.state['phase']!='prepared': raise ActionConflict('fixture CAS')
        state=await self.transition(key,revision,'dispatching',{'dispatch_started_at':timestamp(self.clock())})
        if self.after_arm: self.after_arm()
        return state
    async def transition(self,key,revision,phase,facts,**kwargs):
        if self.state['revision']!=revision: raise ActionConflict('fixture CAS')
        self.state.update(revision=revision+1,phase=phase,facts=deepcopy(facts))
        return deepcopy(self.state)
    async def incident_current(self,request): return self.current


def setup():
    time=[AS_OF];clock=lambda:time[0];switch=[True]
    facts,cards,available=fixtures()
    change_fact=next(f for f in facts if f['id']=='payment-service/recent_change_support')
    change_fact['observed_at']=timestamp(END-timedelta(seconds=60))
    available[change_fact['id']]=timestamp(END-timedelta(seconds=59))
    rca=evaluate(facts,cards,available)
    request=Request('INC-'+rca['group_id'],2,'fixture-idempotency-0001')
    good=KnownGood(TARGET,GOOD,'artifact://manifests/'+'1'*64+'.json',timestamp(END-timedelta(seconds=120)),())
    change=Change(TARGET,GOOD,BAD,5,change_fact['observed_at'],available[change_fact['id']],REF,change_fact['id'])
    assessment=Assessment(request.incident_id,2,'RCA_READY','ELIGIBLE',timestamp(clock()),
        'artifact://manifests/'+'2'*64+'.json',rca,change,True,'fixture-only-authority')
    class FixtureAuthority:
        health_stream_id='3'*64
        async def assess(self,*args,**kwargs): return replace(self.value,latest_health_at=timestamp(clock()))
    authority=FixtureAuthority();authority.value=assessment
    class FixtureRegistry:
        async def resolve(self,*args,**kwargs): return good
    class FixtureKube:
        def __init__(self): self.calls=[];self.after=False;self.timeout=False;self.deny=False;self.unready=False
        async def observe(self):
            if not self.after: return observation(observed_at=timestamp(clock()))
            old=observation().pods[0]
            return observation(image=GOOD,generation=6,observed_generation=6,observed_at=timestamp(clock()),
                pods=(replace(old,uid='new-pod',image=GOOD,ready=not self.unready,image_id='containerd://sha256:'+'a'*64),))
        async def patch(self,before,image,*,dry_run):
            self.calls.append(dry_run)
            if self.deny: raise Blocked('kubernetes-rbac-denied')
            if not dry_run:
                self.after=True
                if self.timeout: raise KubernetesUnavailable('fixture transport timeout after server commit')
            return {'image':image,'observed_at':timestamp(clock())}
    class FixtureRecovery:
        source=SimpleNamespace(stream_id='3'*64,policy=HEALTH_POLICY)
        target=TARGET
        environment='fixture'
        corrupt=False
        async def verify(self,*,effective_at,now,target):
            if now-effective_at<timedelta(minutes=4): return {'status':'pending'}
            end=now.replace(second=0,microsecond=0)-timedelta(minutes=1)
            result={'verification_version':'post-action-current-slo/v1','status':'telemetry_verified',
                'health_stream_id':self.source.stream_id,'health_policy_sha256':stable_id(self.source.policy),
                'environment':'fixture','required_buckets':3,'effective_at':timestamp(effective_at),'as_of':timestamp(now),
                'action_dispatch_authorized':False,'deployment_identity_verified':False,'recovery_accepted':False,
                'support':[{'bucket_end':timestamp(end-timedelta(minutes=2-i)),
                    'recorded_at':timestamp(end-timedelta(minutes=2-i)+timedelta(seconds=38)),
                    'signal_id':f'{i+1:064x}','batch_ref':'artifact://manifests/'+f'{i+1:064x}'+'.json',
                    'observation_ref':'artifact://observations/'+f'{i+1:064x}'+'.json'} for i in range(3)]}
            if self.corrupt: result['support'][1]=deepcopy(result['support'][0])
            return result
    journal=MemoryJournal(clock);kube=FixtureKube();recovery=FixtureRecovery();policy=Policy(TARGET,enabled=True)
    registry=FixtureRegistry();registry.source=recovery.source;registry.environment='fixture'
    executor=RollbackExecutor(journal,kube,authority,registry,recovery,policy,POLICY,enabled=lambda:switch[0],clock=clock)
    return SimpleNamespace(executor=executor,journal=journal,kube=kube,recovery=recovery,authority=authority,
                           request=request,time=time,switch=switch,clock=clock)


def test_patch_is_verifying_until_new_pod_and_current_post_action_telemetry_succeed():
    async def run():
        f=setup()
        sent=await f.executor.execute(f.request)
        assert sent['phase']=='verifying' and f.kube.calls==[True,False]
        ready=await f.executor.reconcile(sent['action_id'])
        assert ready['phase']=='verifying' and ready['facts']['effective_at']==timestamp(f.clock())
        f.time[0]+=timedelta(minutes=4)
        result=await f.executor.execute(f.request)
        assert result['phase']=='recovered' and f.kube.calls.count(False)==1
        assert await f.executor.execute(f.request)==result
    asyncio.run(run())


@pytest.mark.parametrize('problem',['disabled','unqualified','abstained','wrong-change','wrong-generation','stale','dryrun','revision'])
def test_policy_rejections_never_dispatch(problem):
    async def run():
        f=setup();a=f.authority.value
        if problem=='disabled': f.switch[0]=False
        if problem=='unqualified': f.authority.value=replace(a,dispatch_authority=False,missing_reason='rca-abstained')
        if problem=='abstained': f.authority.value=replace(a,rca=evaluate([],[],{}))
        if problem=='wrong-change': f.authority.value=replace(a,change=replace(a.change,after_image=GOOD))
        if problem=='wrong-generation': f.authority.value=replace(a,change=replace(a.change,after_generation=6))
        if problem=='stale': f.time[0]+=timedelta(seconds=181)
        if problem=='dryrun': f.kube.deny=True
        if problem=='revision': f.authority.value=replace(a,revision=3)
        result=await f.executor.execute(f.request)
        assert result['phase']=='blocked' and False not in f.kube.calls
    asyncio.run(run())


def test_db_failure_before_dispatch_and_kill_switch_after_arm_never_send_patch():
    async def run():
        f=setup();f.journal.fail_arm=True
        with pytest.raises(ConnectionError): await f.executor.execute(f.request)
        assert f.kube.calls==[True] and f.journal.state['phase']=='prepared'
        f.journal.fail_arm=False;f.journal.after_arm=lambda:f.switch.__setitem__(0,False)
        result=await f.executor.execute(f.request)
        assert result['phase']=='escalated' and result['facts']['dispatch_cancelled_before_send']
        assert False not in f.kube.calls
    asyncio.run(run())


def test_timeout_after_server_commit_and_restart_reconcile_without_resending():
    async def run():
        f=setup();f.kube.timeout=True
        uncertain=await f.executor.execute(f.request)
        assert uncertain['phase']=='dispatching' and uncertain['facts']['reason']=='kubernetes-delivery-uncertain'
        # New controller instance over the same durable journal; disabled after
        # sending must still reconcile the already observed mutation.
        f.switch[0]=False
        f.executor.policy=replace(f.executor.policy,enabled=False)
        result=await f.executor.execute(f.request)
        assert result['phase']=='verifying' and f.kube.calls.count(False)==1
        f.time[0]+=timedelta(minutes=4)
        assert (await f.executor.execute(f.request))['phase']=='recovered'
    asyncio.run(run())


def test_committed_dispatch_survives_database_failure_after_patch_receipt():
    async def run():
        f=setup();original=f.journal.transition
        async def lose_database(key,revision,phase,facts,**kwargs):
            if phase=='verifying': raise ConnectionError('fixture database failed after Kubernetes applied image')
            return await original(key,revision,phase,facts,**kwargs)
        f.journal.transition=lose_database
        with pytest.raises(ConnectionError): await f.executor.execute(f.request)
        assert f.journal.state['phase']=='dispatching' and f.kube.calls.count(False)==1
        f.journal.transition=original
        assert (await f.executor.execute(f.request))['phase']=='verifying'
        assert f.kube.calls.count(False)==1
    asyncio.run(run())


def test_prepared_intent_resumes_with_a_new_receipt_clock_but_identical_resource_version():
    async def run():
        f=setup();f.journal.fail_arm=True
        with pytest.raises(ConnectionError): await f.executor.execute(f.request)
        f.time[0]+=timedelta(seconds=10);f.journal.fail_arm=False
        assert (await f.executor.execute(f.request))['phase']=='verifying'
        assert f.kube.calls.count(False)==1
    asyncio.run(run())


def test_concurrent_consumers_only_one_dispatch_and_never_double_recovery():
    async def run():
        f=setup()
        await asyncio.gather(*(f.executor.execute(f.request) for _ in range(8)))
        assert f.kube.calls.count(False)==1
        await f.executor.reconcile(f.journal.state['action_id'])
        f.time[0]+=timedelta(minutes=4)
        await asyncio.gather(*(f.executor.execute(f.request) for _ in range(8)))
        assert f.journal.state['phase']=='recovered' and f.kube.calls.count(False)==1
    asyncio.run(run())


def test_ready_deployment_without_telemetry_times_out_and_no_bad_image_compensation():
    async def run():
        f=setup();sent=await f.executor.execute(f.request)
        assert (await f.executor.reconcile(sent['action_id']))['phase']=='verifying'
        f.time[0]+=timedelta(seconds=600)
        result=await f.executor.reconcile(sent['action_id'])
        assert result['phase']=='escalated' and f.kube.calls.count(False)==1
        assert result['facts']['reason']=='verification-timeout-target-quarantined'
    asyncio.run(run())


def test_forged_recovery_persistence_is_escalated_not_recovered():
    async def run():
        f=setup();sent=await f.executor.execute(f.request)
        await f.executor.reconcile(sent['action_id'])
        f.time[0]+=timedelta(minutes=4);f.recovery.corrupt=True
        result=await f.executor.reconcile(sent['action_id'])
        assert result['phase']=='escalated' and 'recovery' in result['facts']['reason']
    asyncio.run(run())


def test_controller_rejects_cross_source_health_binding_before_any_kubernetes_request():
    f=setup()
    foreign=SimpleNamespace(source=SimpleNamespace(stream_id='foreign-stream',policy=HEALTH_POLICY),environment='fixture')
    with pytest.raises(ValueError,match='binding'):
        RollbackExecutor(f.journal,f.kube,f.authority,foreign,f.recovery,f.executor.policy,POLICY)
    assert f.kube.calls==[]


def test_native_mathematical_qualification_never_becomes_an_action_grant():
    async def run():
        f=setup()
        class Connection:
            async def fetchval(self,*args): return 'fixture-accepted-job'
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        class Pool:
            def acquire(self): return Connection()
        async def evidence(*args):
            return {},{'result_ref':f.authority.value.result_ref},{'rca':f.authority.value.rca}
        async def snapshot(*args):
            return {'revision':2,'context':{'lifecycle_stage':'RCA_READY','availability':'ELIGIBLE'}}
        async def checkpoint(): return None,{'last_bucket_end':timestamp(f.clock())}
        journal=SimpleNamespace(pool=Pool(),source=f.recovery.source,consumer_id='fixture-consumer',
            verified_evidence=evidence,repository=SimpleNamespace(get=snapshot),checkpoint=checkpoint)
        result=await NativeH4Authority(journal).assess(f.request,TARGET,now=f.clock())
        assert result.rca['abstained'] is False and result.dispatch_authority is False
        assert result.missing_reason=='native-h4-contract-not-activated' and result.change is None
    asyncio.run(run())


def test_minute_anchor_crossing_during_final_kubernetes_read_does_not_backdate_recovery():
    async def run():
        f=setup();sent=await f.executor.execute(f.request)
        await f.executor.reconcile(sent['action_id'])
        f.time[0]+=timedelta(minutes=4,seconds=29)
        original=f.kube.observe;calls=0
        async def delayed():
            nonlocal calls
            calls+=1
            if calls==2: f.time[0]+=timedelta(seconds=2)
            return await original()
        f.kube.observe=delayed
        result=await f.executor.reconcile(sent['action_id'])
        assert calls==2 and result['phase']=='verifying'
    asyncio.run(run())


def test_an_external_spec_change_after_dispatch_is_quarantined_not_accepted_as_our_rollout():
    async def run():
        f=setup();sent=await f.executor.execute(f.request)
        original=f.kube.observe
        async def foreign_generation(): return replace(await original(),generation=7,observed_generation=7)
        f.kube.observe=foreign_generation
        result=await f.executor.reconcile(sent['action_id'])
        assert result['phase']=='escalated' and result['facts']['reason']=='unexpected-external-generation-change'
        assert f.kube.calls.count(False)==1
    asyncio.run(run())


@pytest.mark.parametrize('problem',['no-ledger','no-approved-image','approved-after-incident'])
def test_absent_known_good_blocks_before_any_kubernetes_request(problem):
    # TECHNICAL_SPEC §13 mandatory safety case "absent known-good": expected blocked, no observe/patch.
    async def run():
        f=setup()
        if problem=='no-ledger':
            f.executor.registry=None
        else:
            class Missing:
                source,environment=f.recovery.source,'fixture'
                async def resolve(self,*args,**kwargs):
                    raise Blocked('no-approved-known-good-for-target' if problem=='no-approved-image'
                                  else 'known-good-not-approved-before-incident')
            f.executor.registry=Missing()
        observed=[]
        original=f.kube.observe
        async def spy():
            observed.append(True);return await original()
        f.kube.observe=spy
        result=await f.executor.execute(f.request)
        assert result['phase']=='blocked' and f.kube.calls==[] and observed==[]
    asyncio.run(run())


# Q-10 side effects at the executor level (B review 2026-11-10): the domain tests above prove the
# rejection; these prove that no dry-run or patch reaches Kubernetes and that the journal records why.
@pytest.mark.parametrize('observed',[
    dict(target=replace(TARGET,namespace_uid='foreign-namespace-uid')),
    dict(target=replace(TARGET,deployment_uid='recreated-deployment')),
    dict(target=replace(TARGET,cluster_uid='other-cluster')),
    dict(replicas=2),dict(max_surge=1),dict(hpa=True),dict(paused=True),dict(pods=()),dict(strategy='Recreate'),
],ids=['namespace-uid','deployment-uid','cluster-uid',
       'replicas','surge','hpa','paused','no-pods','recreate'])
def test_wrong_identity_or_blast_radius_never_reaches_kubernetes_mutation(observed):
    async def run():
        f=setup()
        async def foreign(): return observation(observed_at=timestamp(f.clock()),**observed)
        f.kube.observe=foreign
        result=await f.executor.execute(f.request)
        assert result['phase']=='blocked' and result['facts'].get('reason')
        assert f.kube.calls==[]
        again=await f.executor.execute(f.request)
        assert again['phase']=='blocked' and f.kube.calls==[]
    asyncio.run(run())


@pytest.mark.parametrize('field,value',[('namespace','other-namespace'),('deployment','checkout-service'),
                                         ('container','sidecar'),('deployment_uid','')])
def test_target_outside_the_sandbox_cannot_even_be_constructed(field,value):
    with pytest.raises(Blocked,match='sandbox'): replace(TARGET,**{field:value})


@pytest.mark.parametrize('failure',['forged-recovery','new-pod-never-ready','no-telemetry'])
def test_verification_failure_escalates_once_without_a_second_or_compensating_patch(failure):
    async def run():
        f=setup();sent=await f.executor.execute(f.request)
        assert f.kube.calls==[True,False]
        if failure=='new-pod-never-ready': f.kube.unready=True
        if failure=='no-telemetry':
            async def pending(**kwargs): return {'status':'pending'}
            f.recovery.verify=pending
        await f.executor.reconcile(sent['action_id'])
        if failure=='forged-recovery':
            f.time[0]+=timedelta(minutes=4);f.recovery.corrupt=True
        else:
            f.time[0]+=timedelta(seconds=600)
        result=await f.executor.reconcile(sent['action_id'])
        assert result['phase']=='escalated' and result['facts']['reason']
        assert f.kube.calls==[True,False]
        f.time[0]+=timedelta(minutes=30)
        assert (await f.executor.reconcile(sent['action_id']))['phase']=='escalated'
        assert (await f.executor.execute(f.request))['phase']=='escalated'
        assert f.kube.calls==[True,False]
    asyncio.run(run())
