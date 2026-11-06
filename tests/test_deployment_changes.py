"""Observer/ledger software fixtures; never source parity or action acceptance."""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.remediation.domain import Blocked
from sentinelops.telemetry.deployment_changes import DeploymentChangeReview, FLAGS, verify_change
from sentinelops.telemetry.store import ArtifactStore
from test_payment_image_review import setup as image_setup
from test_remediation_kubernetes import BAD, GOOD, TARGET


def setup(tmp_path):
    _, source, kube, clock = image_setup(tmp_path)
    review = DeploymentChangeReview(source, kube, ArtifactStore(tmp_path/'changes'), TARGET, clock=lambda: clock[0])
    return review, source, kube, clock


def change(kube, **kwargs):
    before = kube.value
    kube.value = replace(before, image=BAD, generation=before.generation+1,
                         observed_generation=before.generation+1, resource_version='126',
                         pods=(replace(before.pods[0], uid='replacement-pod', image=BAD),), **kwargs)


def events(store):
    return [store.read('artifact://manifests/'+p.name) for p in (store.root/'manifests').glob('*.json')
            if store.read('artifact://manifests/'+p.name).get('record_version')=='payment-deployment-change/v1']


def test_observed_transition_replays_original_snapshots_and_never_grants_authority(tmp_path):
    async def run():
        review, source, kube, clock = setup(tmp_path)
        opened = await review.open()
        assert (await review.complete(opened['baseline_ref']))['status']=='PENDING'
        clock[0] += timedelta(seconds=20)
        change(kube)
        result = await review.complete(opened['baseline_ref'])
        assert result['status']=='CHANGE_OBSERVED'
        assert result['observed_before_image']==GOOD and result['observed_after_image']==BAD
        assert not hasattr(kube,'patch')
        event=review.store.read(result['change_ref']);validate_payload('payment-deployment-change.schema.json',event)
        actual=verify_change(review.store,result['change_ref'],source=source,target=TARGET,as_of=clock[0])
        assert (actual['change_interval'][1]-actual['change_interval'][0]).total_seconds()==20
        assert all(result[k] is actual[k] is False for k in FLAGS)
        assert await review.complete(opened['baseline_ref'])==result
        with pytest.raises(ValueError):validate_payload('payment-known-good.schema.json',event)
        assert len(events(review.store))==1
    asyncio.run(run())


@pytest.mark.parametrize('problem',['no-change','generation-gap','non-image','future','expired','host','scope','corrupt','writer','source-unavailable'])
def test_unknown_or_invalid_transition_is_not_published_as_a_change(tmp_path,monkeypatch,problem):
    async def run():
        review, source, kube, clock = setup(tmp_path)
        opened=await review.open();clock[0]+=timedelta(seconds=20)
        if problem=='no-change':
            assert (await review.complete(opened['baseline_ref']))['status']=='PENDING'
            assert not events(review.store);return
        change(kube)
        if problem=='generation-gap':kube.value=replace(kube.value,generation=9,observed_generation=9)
        elif problem=='non-image':kube.value=replace(kube.value,image=GOOD)
        elif problem=='future':clock[0]-=timedelta(seconds=30)
        elif problem=='expired':clock[0]+=timedelta(seconds=181)
        elif problem=='host':monkeypatch.setattr('sentinelops.telemetry.deployment_changes.observer_boot',lambda:'c'*64)
        elif problem=='scope':source.stream_id='c'*64
        elif problem=='corrupt':
            record=review.store.read(opened['baseline_ref'])
            (review.store.root/record['before_ref'].removeprefix('artifact://')).write_text('{}')
            existing={p.name for p in (review.store.root/'manifests').glob('*.json')}
            with pytest.raises(ValueError,match='checksum'):
                await review.complete(opened['baseline_ref'])
            assert {p.name for p in (review.store.root/'manifests').glob('*.json')}==existing
            return
        elif problem=='writer':kube.allowed=False
        else:
            async def unavailable():raise ConnectionError('fixture source unavailable')
            kube.observe=unavailable
        with pytest.raises((ValueError,ConnectionError)):await review.complete(opened['baseline_ref'])
        assert not events(review.store)
    asyncio.run(run())


@pytest.mark.parametrize('field',['action_dispatch_authorized','known_good_approved','physical_binding_verified','rca_support_authorized'])
def test_client_cannot_upgrade_an_observation_to_an_approval(tmp_path,field):
    async def run():
        review, _, kube, clock=setup(tmp_path);opened=await review.open()
        clock[0]+=timedelta(seconds=20);change(kube);result=await review.complete(opened['baseline_ref'])
        event=review.store.read(result['change_ref']);event[field]=True
        with pytest.raises(ValueError):validate_payload('payment-deployment-change.schema.json',event)
    asyncio.run(run())


def test_source_store_cannot_be_used_as_change_output(tmp_path):
    review, source, kube, _ = setup(tmp_path)
    with pytest.raises(ValueError):DeploymentChangeReview(source,kube,source.store,TARGET)


@pytest.mark.parametrize('field',['change_not_before','change_not_after','recorded_at','before_ref','target'])
def test_replay_rejects_rebound_or_inconsistent_receipt(tmp_path,field):
    async def run():
        review, source, kube, clock=setup(tmp_path);opened=await review.open()
        clock[0]+=timedelta(seconds=20);change(kube);result=await review.complete(opened['baseline_ref'])
        event=review.store.read(result['change_ref'])
        if field=='before_ref':event[field]=event['after_ref']
        elif field=='target':event[field]['deployment_uid']='foreign-deployment'
        elif field=='change_not_before':event[field]=event['change_not_after']
        else:event[field]=event['opened_at']
        ref=review.store.write('manifests',event)
        with pytest.raises(ValueError):verify_change(review.store,ref,source=source,target=TARGET,as_of=clock[0])
    asyncio.run(run())


def test_unsettled_baseline_is_not_misrepresented_as_a_before_image(tmp_path):
    async def run():
        review, _, kube, _=setup(tmp_path)
        kube.value=replace(kube.value,pods=(replace(kube.value.pods[0],ready=False),))
        with pytest.raises(Blocked,match='baseline-not-settled'):await review.open()
        assert not (review.store.root/'manifests').exists()
    asyncio.run(run())


def test_change_interval_starts_before_the_baseline_api_read_not_at_its_receipt(tmp_path):
    from sentinelops.telemetry.core import timestamp
    async def run():
        review, source, kube, clock=setup(tmp_path)
        beginning=clock[0]
        async def slow_read():
            clock[0]+=timedelta(seconds=5)
            return replace(kube.value,observed_at=timestamp(clock[0]))
        kube.observe=slow_read
        opened=await review.open();clock[0]+=timedelta(seconds=15);change(kube)
        result=await review.complete(opened['baseline_ref'])
        verified=verify_change(review.store,result['change_ref'],source=source,target=TARGET,as_of=clock[0])
        assert verified['before'].observed_at==timestamp(beginning+timedelta(seconds=5))
        assert verified['change_interval']==(beginning,beginning+timedelta(seconds=25))
        assert verified['available_at']==clock[0]
    asyncio.run(run())
