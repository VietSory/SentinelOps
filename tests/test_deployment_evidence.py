"""Observed-event/RCA/H4 software integration; never a real model/action approval."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from sentinelops.remediation.authority import NativeH4Authority, qualify
from sentinelops.remediation.domain import Blocked
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.deployment_evidence import (
    DeploymentIncidentEvidence, add_deployment_support, verify_deployment_evidence,
)
from test_deployment_changes import setup, change
from test_rca import POLICY, END, fixtures, evaluate
from test_remediation_executor import setup as executor_fixture
from test_remediation_kubernetes import TARGET


def test_observed_change_joins_exact_source_and_preserves_interval_in_rca(tmp_path,monkeypatch):
    async def run():
        review,source,kube,clock=setup(tmp_path)
        from sentinelops.workers.health import HealthWorker
        worker=HealthWorker(None,source.store,source.policy,endpoint='http://localhost:9090',
                           environment='fixture',configuration_hash='a'*64)
        assert worker.stream_id==source.stream_id
        clock[0]=END-timedelta(seconds=90)
        baseline=await review.open();clock[0]+=timedelta(seconds=20);change(kube)
        event=await review.complete(baseline['baseline_ref'])
        store=ArtifactStore(tmp_path/'journal')
        batch=store.write('manifests',{'bucket_end':timestamp(END+timedelta(seconds=60))})
        state=store.write('manifests',{'last_batch_ref':batch})
        base={'capture_version':'continuous-incident-evidence/v3','source_containers':{
            'payment-service':{'deployment_uid':TARGET.deployment_uid,'generation':kube.value.generation,'image':kube.value.image}},
            'physical_binding':{'source_state_ref':state}}
        class FixtureCollector:
            profile={'profile_version':'kubernetes-incident-evidence/v1','target':asdict(TARGET)}
            async def capture(self,store,**kwargs):return store.write('manifests',base)
        wrapper=DeploymentIncidentEvidence(FixtureCollector(),source,review.store)
        from sentinelops.workers import deployment_evidence
        monkeypatch.setattr(deployment_evidence,'now',lambda:END+timedelta(minutes=5))
        ref=await wrapper.capture(store,source_state_ref=state,health_identity=worker.identity,
                                 group={'first_onset_at':timestamp(END)})
        captured=store.read(ref)
        assert captured['deployment_changes']==[event['change_ref']]
        verified=verify_deployment_evidence(store,captured,worker.identity,source.policy,{'first_onset_at':timestamp(END)})
        facts,cards,availability=fixtures();rca=evaluate(facts,cards,availability)
        result=add_deployment_support(store,{'rca':rca},verified,policy=POLICY,as_of=END+timedelta(hours=1))
        component=next(c for c in result['rca']['candidates'] if c['service']=='payment-service')['components']['recent_change_support']
        assert component['value']==1 and result['rca']['actions_allowed'] is False
        fact=next(f for f in result['rca']['evidence'] if f['id']==result['deployment_change']['fact_id'])
        assert fact['source_ref']==event['change_ref']
        assert result['deployment_change']['not_before']==timestamp(END-timedelta(seconds=90))
        assert fact['observed_at']==timestamp(END-timedelta(seconds=70))
        assert 'not an exact deployment time' in fact['fact']
        # Changing the current image or accepted bucket cannot reuse that event.
        for problem in ('image','generation','policy','root','time','duplicate'):
            bad=deepcopy(captured);identity=deepcopy(worker.identity)
            if problem=='image':bad['source_containers']['payment-service']['image']='other@sha256:'+'b'*64
            if problem=='generation':bad['source_containers']['payment-service']['generation']+=1
            if problem=='policy':identity['policy_sha256']='0'*64
            if problem=='root':identity['artifact_root_hash']='0'*64
            if problem=='time':bad['analyzed_at']=timestamp(END-timedelta(seconds=80))
            if problem=='duplicate':bad['deployment_changes']*=2
            with pytest.raises((ValueError,Blocked)):
                verify_deployment_evidence(store,bad,identity,source.policy,{'first_onset_at':timestamp(END)})
    asyncio.run(run())


@pytest.mark.parametrize('offset',[-10,-60,-61,1])
def test_known_good_must_predate_entire_observed_mutation_interval(offset):
    async def run():
        f=executor_fixture();assessment=f.authority.value
        from sentinelops.contracts import utc_time
        upper=utc_time(assessment.change.observed_at)
        bounded=replace(assessment,change=replace(assessment.change,not_before=timestamp(upper+timedelta(seconds=offset))))
        before=await f.kube.observe();good=await f.executor.registry.resolve(f.request,TARGET,now=f.clock())
        if offset in (-10,-60):
            qualify(bounded,f.request,before,good,f.executor.policy,POLICY,now=f.clock())
        else:
            with pytest.raises(Blocked,match='interval'):
                qualify(bounded,f.request,before,good,f.executor.policy,POLICY,now=f.clock())
    asyncio.run(run())


@pytest.mark.parametrize('version,model,abstained,expected',[
    ('continuous-incident-evidence/v6',True,False,True),
    ('continuous-incident-evidence/v6',False,False,False),
    ('continuous-incident-evidence/v5',True,False,False),
    ('continuous-incident-evidence/v2',True,False,False),
    ('continuous-incident-evidence/v7',True,False,False),
    ('continuous-incident-evidence/v6',True,True,False),
])
def test_native_h4_requires_verified_kubernetes_model_change_combination(tmp_path,version,model,abstained,expected):
    async def run():
        f=executor_fixture();store=ArtifactStore(tmp_path);assessment=f.authority.value
        result=deepcopy(assessment.rca);result['abstained']=abstained
        class Connection:
            async def fetchval(self,*args):return 'accepted-job'
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        class Pool:
            def acquire(self):return Connection()
        capture=store.write('manifests',{'capture_version':version})
        async def evidence(*args):
            return {},{'result_ref':assessment.result_ref,'capture_ref':capture}, {'rca':result,'deployment_change':asdict(assessment.change)}
        async def snapshot(*args):return {'revision':2,'context':{'lifecycle_stage':'RCA_READY','availability':'ELIGIBLE'}}
        async def checkpoint():return None,{'last_bucket_end':timestamp(f.clock())}
        journal=SimpleNamespace(pool=Pool(),source=f.recovery.source,consumer_id='fixture',store=store,
            model_registry=SimpleNamespace(available=model),verified_evidence=evidence,
            repository=SimpleNamespace(get=snapshot),checkpoint=checkpoint)
        actual=await NativeH4Authority(journal).assess(f.request,TARGET,now=f.clock())
        assert actual.dispatch_authority is expected
        assert (actual.change is not None) is expected
    asyncio.run(run())


def test_expired_source_passes_through_model_and_change_wrappers_without_using_them(tmp_path):
    from sentinelops.workers.model_signals import ModelIncidentEvidence
    async def run():
        store=ArtifactStore(tmp_path/'journal')
        class Collector:
            profile={'profile_version':'kubernetes-incident-evidence/v1'}
            async def capture(self,store,**kwargs):
                return store.write('manifests',{'capture_version':'continuous-incident-evidence/v7',
                    'profile':self.profile,'actions_allowed':False})
        source=SimpleNamespace(store=ArtifactStore(tmp_path/'source'))
        wrapped=DeploymentIncidentEvidence(Collector(),source,ArtifactStore(tmp_path/'changes'))
        registry=SimpleNamespace(identity='fixture-pinned-release-only')
        modeled=ModelIncidentEvidence(wrapped,registry)
        ref=await modeled.capture(store,source_state_ref='no-source-model-read-allowed')
        result=store.read(ref)
        assert result['capture_version']=='continuous-incident-evidence/v7' and result['profile']==modeled.profile
        assert result['actions_allowed'] is False
        assert 'model_evidence' not in result and 'deployment_changes' not in result
    asyncio.run(run())
