"""Software-only operator fixtures; never a real H4 grant or recovery acceptance."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.remediation.domain import ActionConflict, Blocked
from sentinelops.remediation.executor import LedgerRecovery
from sentinelops.remediation.quarantine import QuarantineReview
from sentinelops.telemetry.core import stable_id,timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_remediation_executor import setup


class ReviewJournal:
    def __init__(self,f): self.f=f;self.active=True;self.saved=None;self.calls=[];self.fail_after_commit=False
    async def check_ready(self): pass
    async def context(self,key):
        return {'state':deepcopy(self.f.journal.state),'active_action':key if self.active else None,
                'dispatch_at':self.dispatch,'now':self.f.clock()}
    async def find(self,**args):
        if self.saved:
            if args!=self.saved[0]: raise ActionConflict('fixture idempotency conflict')
            return deepcopy(self.saved[1])
    async def release(self,**args):
        payload=args.pop('payload');self.calls.append(args)
        assert payload['recovery_accepted'] is payload['dispatch_authorized'] is payload['ier_mutated'] is False
        result={'status':'RELEASED','payload':payload,'operator_actor':'fixture-operator'}
        self.saved=(args,result);self.active=False
        if self.fail_after_commit: raise ConnectionError('fixture lost receipt after commit')
        return deepcopy(result)


async def fixture(tmp_path):
    f=setup();await f.executor.execute(f.request)
    await f.journal.transition(f.journal.state['action_id'],f.journal.state['revision'],'escalated',f.journal.state['facts'])
    f.journal.state['last_event_hash']='e'*64
    journal=ReviewJournal(f);journal.dispatch=f.clock();f.time[0]+=timedelta(seconds=600)
    f.recovery.source.store=ArtifactStore(tmp_path/'original-source')
    original=f.kube.observe
    async def observe(): return replace(await original(),resource_version='18')
    f.kube.observe=observe;f.kube.calls=[]
    async def no_patch(*args,**kwargs): raise AssertionError('manual review tried to mutate Kubernetes')
    f.kube.patch=no_patch
    review=QuarantineReview(journal,f.kube,f.recovery,ArtifactStore(tmp_path/'reviews'),f.executor.policy.target,clock=f.clock)
    return f,journal,review


def test_two_observations_and_current_health_release_only_the_claim_with_idempotent_retry(tmp_path):
    async def run():
        f,j,review=await fixture(tmp_path);before=deepcopy(f.journal.state)
        opened=await review.open(before['action_id'])
        assert j.active and j.calls==[]
        pending=await review.release(opened['review_ref'],key='review-idempotency-0001')
        assert pending['status']=='PENDING' and j.active
        f.time[0]+=timedelta(minutes=4)
        result=await review.release(opened['review_ref'],key='review-idempotency-0001')
        assert result['status']=='RELEASED' and not j.active and len(j.calls)==1
        validate_payload('quarantine-release.schema.json',result['payload'])
        assert f.journal.state==before and f.kube.calls==[]
        assert await review.release(opened['review_ref'],key='review-idempotency-0001')==result
        with pytest.raises(ActionConflict): await review.release(opened['review_ref'],key='different-idempotency-key')
    asyncio.run(run())


@pytest.mark.parametrize('problem',['not-escalated','no-claim','no-dispatch','too-soon','target','source','policy','environment','image','pods','generation','resource-version'])
def test_review_rejects_unqualified_scope_and_unsettled_rollouts(tmp_path,problem):
    async def run():
        f,j,review=await fixture(tmp_path)
        if problem=='not-escalated': f.journal.state['phase']='verifying'
        if problem=='no-claim': j.active=False
        if problem=='no-dispatch': j.dispatch=None
        if problem=='too-soon': f.time[0]-=timedelta(seconds=1)
        if problem in ('target','source','policy','environment'):
            evidence=f.journal.state['intent']['evidence']
            if problem=='target': f.journal.state['intent']['policy']['target']['deployment_uid']='other'
            if problem=='source': evidence['health_stream_id']='a'*64
            if problem=='policy': evidence['health_policy_sha256']='a'*64
            if problem=='environment': evidence['health_environment']='other'
        if problem in ('image','pods','generation','resource-version'):
            original=f.kube.observe
            async def altered():
                value=await original()
                if problem=='image': return replace(value,image='registry/payment@sha256:'+'f'*64)
                if problem=='pods': return replace(value,pods=())
                if problem=='generation': return replace(value,generation=value.generation+1)
                return replace(value,resource_version=f.journal.state['intent']['evidence']['before']['resource_version'])
            f.kube.observe=altered
        with pytest.raises(Blocked): await review.open(f.journal.state['action_id'])
        assert not j.calls
    asyncio.run(run())


@pytest.mark.parametrize('problem',['hash','revision','source','pod-replaced','expired','corrupt-support'])
def test_stale_or_changed_review_never_releases(tmp_path,problem):
    async def run():
        f,j,review=await fixture(tmp_path);opened=await review.open(f.journal.state['action_id'])
        f.time[0]+=timedelta(minutes=4)
        if problem=='hash': f.journal.state['last_event_hash']='d'*64
        if problem=='revision': f.journal.state['revision']+=1
        if problem=='source': f.journal.state['intent']['evidence']['health_stream_id']='a'*64
        if problem=='expired': f.time[0]+=timedelta(seconds=361)
        if problem=='corrupt-support': f.recovery.corrupt=True
        if problem=='pod-replaced':
            original=f.kube.observe
            async def replaced():
                value=await original();return replace(value,pods=(replace(value.pods[0],uid='another-pod'),))
            f.kube.observe=replaced
        with pytest.raises(Blocked): await review.release(opened['review_ref'],key='review-idempotency-0001')
        assert j.active and not j.calls
    asyncio.run(run())


def test_commit_response_loss_retries_the_existing_receipt_without_releasing_twice(tmp_path):
    async def run():
        f,j,review=await fixture(tmp_path);opened=await review.open(f.journal.state['action_id'])
        f.time[0]+=timedelta(minutes=4);j.fail_after_commit=True
        with pytest.raises(ConnectionError): await review.release(opened['review_ref'],key='review-idempotency-0001')
        assert (await review.release(opened['review_ref'],key='review-idempotency-0001'))['status']=='RELEASED'
        assert len(j.calls)==1 and f.journal.state['phase']=='escalated'
    asyncio.run(run())


def test_a_minute_advance_during_final_observation_cannot_backdate_the_release(tmp_path):
    async def run():
        f,j,review=await fixture(tmp_path);opened=await review.open(f.journal.state['action_id'])
        f.time[0]+=timedelta(minutes=4);original=f.kube.observe;calls=[]
        async def delayed():
            value=await original();calls.append(1)
            if len(calls)==2: f.time[0]+=timedelta(minutes=1)
            return value
        f.kube.observe=delayed
        assert (await review.release(opened['review_ref'],key='review-idempotency-0001'))['status']=='PENDING'
        assert j.active and not j.calls
    asyncio.run(run())


@pytest.mark.parametrize('problem',[None,'missing','traffic','corrupt'])
def test_review_recomputes_actual_raw_lineage_and_never_counts_missing_traffic_as_healthy(tmp_path,monkeypatch,problem):
    import test_remediation_verification as raw
    async def run():
        f,j,review=await fixture(tmp_path)
        monkeypatch.setattr(raw,'START',f.clock())
        source=raw.RawLedgerFixture(tmp_path/'verified-source',[0]*6,
            missing=(3,) if problem=='missing' else (),no_traffic=(3,) if problem=='traffic' else ())
        review.recovery=LedgerRecovery(source,review.target,environment='fixture')
        evidence=f.journal.state['intent']['evidence']
        evidence['health_stream_id']=source.stream_id;evidence['health_policy_sha256']=stable_id(source.policy)
        opened=await review.open(f.journal.state['action_id']);f.time[0]+=timedelta(minutes=4)
        if problem=='corrupt':
            next((tmp_path/'verified-source'/'raw').glob('*.json')).write_text('{}')
            with pytest.raises(ValueError,match='checksum'): await review.release(opened['review_ref'],key='raw-review-idempotency-0001')
        else:
            result=await review.release(opened['review_ref'],key='raw-review-idempotency-0001')
            assert (result['status']=='RELEASED') is (problem is None)
        assert len(j.calls)==(1 if problem is None else 0)
    asyncio.run(run())


def test_review_artifacts_must_not_be_written_into_the_original_source(tmp_path):
    async def run():
        f,j,review=await fixture(tmp_path)
        with pytest.raises(ValueError,match='separate'):
            QuarantineReview(j,f.kube,f.recovery,ArtifactStore(tmp_path/'original-source'/'review'),review.target)
    asyncio.run(run())
