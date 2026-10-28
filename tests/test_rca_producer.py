from copy import deepcopy
import asyncio
from datetime import timedelta

import pytest

from sentinelops.contracts import utc_time,validate_payload
from sentinelops.incidents.rca_producer import prepare_intent,project_commands,publish_intent,verified_intent,verify_rca_report
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_rca_replay import POLICY,source_scope


def bundle(tmp_path,**options):
    source,health,correlation,ref,_=source_scope(tmp_path,**options)
    report=replay_rca(source,health,correlation,ref,POLICY,tmp_path/'bundle')
    return ArtifactStore(tmp_path/'bundle'),report


def test_intent_retries_freeze_emission_and_project_valid_legacy_ier_without_fake_model_scores(tmp_path):
    store,report=bundle(tmp_path)
    intent_ref=prepare_intent(store,report['report_ref'],policy=POLICY)
    intent,verified=verified_intent(store,intent_ref,policy=POLICY)
    first=project_commands(intent_ref,intent,verified)
    second=project_commands(intent_ref,*verified_intent(store,intent_ref,policy=POLICY))
    assert [c.fingerprint() for c in first[0]]==[c.fingerprint() for c in second[0]]
    assert [c.lifecycle_stage for c in first[0]]==['OPEN','RCA_READY','ESCALATED']
    assert [c.expected_revision for c in first[0]]==[0,1,2]
    for command in first[0]:
        validate_payload('incident-evidence-record.schema.json',command.payload)
        assert command.payload['service_signals']==[]
        assert command.payload['detected_at']==intent['emitted_at']
        assert utc_time(command.payload['detected_at'])>=utc_time(report['as_of'])
        assert command.context['availability']=='UNKNOWN' and command.context['recovery_source'] is None
        assert all(trigger['source']=='slo' and trigger['emitted_at']==intent['emitted_at'] for trigger in command.context['trigger_sources'])
    assert first[0][0].payload['root_cause_ranking']==[]
    assert first[0][-1].payload['state']=='escalated'
    assert first[0][1].payload['root_cause_ranking'][0]['service']=='payment-service'
    assert any('abstention=' in fact['fact'] and 'actions_allowed=false' in fact['fact'] for fact in first[0][-1].payload['evidence'])


def test_no_opening_projects_no_fake_persisted_incident(tmp_path):
    store,report=bundle(tmp_path,healthy=True)
    ref=prepare_intent(store,report['report_ref'],policy=POLICY)
    assert project_commands(ref,*verified_intent(store,ref,policy=POLICY))==[]


def test_rehashed_score_forgery_fails_raw_recomputation_before_intent_creation(tmp_path):
    store,report=bundle(tmp_path)
    forged=deepcopy(store.read(report['report_ref']))
    forged['results'][0]['rca']['candidates'][0]['score']=.99
    ref=store.write('manifests',forged)
    with pytest.raises(ValueError,match='does not reproduce'):
        prepare_intent(store,ref,policy=POLICY)


def test_report_must_match_selected_policy_not_a_lowered_client_threshold(tmp_path):
    store,report=bundle(tmp_path)
    policy=deepcopy(POLICY);policy['minimum_score']=0
    with pytest.raises(ValueError,match='selected policy'):
        verify_rca_report(store,report['report_ref'],policy=policy)


def test_backdated_or_extended_intent_cannot_be_projected_by_trusted_producer(tmp_path):
    store,report=bundle(tmp_path)
    ref=prepare_intent(store,report['report_ref'],policy=POLICY)
    intent=store.read(ref)
    intent['emitted_at']=timestamp(utc_time(report['as_of'])-timedelta(seconds=1))
    with pytest.raises(ValueError,match='backdated'):
        verified_intent(store,store.write('manifests',intent),policy=POLICY)
    intent['actions_allowed']=True
    with pytest.raises(ValueError,match='invalid local'):
        verified_intent(store,store.write('manifests',intent),policy=POLICY)


def test_archived_source_corruption_is_not_hidden_by_valid_rca_hashes(tmp_path):
    store,report=bundle(tmp_path)
    ref=prepare_intent(store,report['report_ref'],policy=POLICY)
    raw=next(r for r in report['source_artifact_refs'] if r.startswith('artifact://raw/'))
    (store.root/raw.removeprefix('artifact://')).write_text('{}')
    with pytest.raises(ValueError,match='checksum'):
        verified_intent(store,ref,policy=POLICY)


def test_trusted_publisher_rejects_rehashed_forgery_before_touching_repository(tmp_path):
    store,report=bundle(tmp_path)
    ref=prepare_intent(store,report['report_ref'],policy=POLICY)
    intent=store.read(ref);forged=store.read(report['report_ref'])
    forged['results'][0]['severity']='critical'
    intent['report_ref']=store.write('manifests',forged)
    class UntouchedRepository:
        async def check_ready(self):raise AssertionError('repository touched before source verification')
    with pytest.raises(ValueError,match='does not reproduce'):
        asyncio.run(publish_intent(UntouchedRepository(),store,store.write('manifests',intent),policy=POLICY))
