"""Known-good approval fixtures are never materialized into a production store."""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.registry import KnownGoodRegistry
from sentinelops.remediation.verification import verify_post_action_health
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_remediation_kubernetes import GOOD,TARGET,observation
from test_remediation_verification import RawLedgerFixture,START


def approved_fixture(tmp_path):
    store=ArtifactStore(tmp_path/'operator')
    source=RawLedgerFixture(tmp_path/'health',[0]*5)
    health=asyncio.run(verify_post_action_health(source,effective_at=START,as_of=START+timedelta(minutes=6),expected_environment='fixture'))
    before=observation(image=GOOD,observed_at=timestamp(START),pods=(replace(observation().pods[0],image=GOOD),))
    after=replace(before,observed_at=timestamp(START+timedelta(minutes=6)))
    refs={key:store.write('manifests',{'observation_version':'payment-deployment/v1','observation':obs.projection()})
          for key,obs in [('before_ref',before),('after_ref',after)]}
    record={'ledger_version':'approved-payment-images/v1','approved_by':'fixture-operator',
        'approved_at':timestamp(START+timedelta(minutes=7)),'approval_scope':'known-good-image-only',
        'action_dispatch_authorized':False,'images':[{'target':before.projection()['target'],'image':GOOD,
            'environment':'fixture',**refs,'health_ref':store.write('manifests',health)}]}
    return store,source,record


def test_approval_reproduces_health_and_pins_ready_stable_deployment_before_incident(tmp_path):
    store,source,record=approved_fixture(tmp_path)
    ref=store.write('manifests',record)
    registry=KnownGoodRegistry(store,ref,approved_by='fixture-operator',source=source,environment='fixture')
    result=asyncio.run(registry.resolve(TARGET,incident_at=START+timedelta(minutes=8)))
    assert result.image==GOOD and result.ledger_ref==ref and len(result.evidence_refs)==3
    assert record['action_dispatch_authorized'] is False
    with pytest.raises(Blocked,match='before-incident'):
        asyncio.run(registry.resolve(TARGET,incident_at=START+timedelta(minutes=7)))


@pytest.mark.parametrize('problem',['approver','environment','image','health','generation','future','target','self-authorized'])
def test_corrupt_or_unscoped_approval_is_not_a_known_good_image(tmp_path,problem):
    store,source,record=approved_fixture(tmp_path);item=record['images'][0]
    if problem=='approver': record['approved_by']='untrusted-operator'
    if problem=='environment': item['environment']='foreign'
    if problem=='image': item['image']='payment:latest'
    if problem=='target': item['target']['deployment_uid']='recreated-deployment'
    if problem=='self-authorized': record['action_dispatch_authorized']=True
    if problem in ('generation','future'):
        data=store.read(item['after_ref'])
        data['observation']['generation' if problem=='generation' else 'observed_at']=6 if problem=='generation' else timestamp(START+timedelta(minutes=9))
        item['after_ref']=store.write('manifests',data)
    if problem=='health':
        data=store.read(item['health_ref']);data['support']=data['support'][:1]
        item['health_ref']=store.write('manifests',data)
    ref=store.write('manifests',record)
    with pytest.raises((ValueError,Blocked)):
        registry=KnownGoodRegistry(store,ref,approved_by='fixture-operator',source=source,environment='fixture')
        asyncio.run(registry.resolve(TARGET,incident_at=START+timedelta(minutes=8)))
