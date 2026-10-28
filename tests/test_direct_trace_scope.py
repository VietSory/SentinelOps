from datetime import timedelta

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.incidents.rca_source import business_logs,trace_observations,verify_scope
from sentinelops.trace_evidence import validate_trace
from sentinelops.trace_query import decode_v3_trace
from test_rca_replay import END,POLICY,TRACE,source_scope
from test_experiment_protocol import run_fixture


def direct_scope(tmp_path,service='payment-service'):
    source,health,correlation,ref,_=source_scope(tmp_path,entry=service)
    scope=verify_scope(source,health,correlation,ref)
    raw=scope['traces'][0][1]
    return source,health,correlation,ref,scope,decode_v3_trace(raw['body'],TRACE)


@pytest.mark.parametrize('service',['payment-service','inventory-service'])
def test_explicit_direct_route_has_one_native_server_and_owned_db_client(tmp_path,service):
    *_,decoded=direct_scope(tmp_path,service)
    servers=validate_trace(decoded,TRACE,'probe',{service:200},root_service=service)
    assert set(servers)=={service}
    # A caller cannot reinterpret the same snapshot as a complete Checkout cascade.
    with pytest.raises((ValueError,KeyError)):
        validate_trace(decoded,TRACE,'probe',{service:200},require_timing=True)


@pytest.mark.parametrize('failure',['missing-timing','bad-duration','wrong-route','wrong-status','missing-error',
                                    'foreign-span','db-parent','db-outside-parent','internal-root-parent'])
def test_direct_profile_rejects_incomplete_identity_time_route_and_parentage(tmp_path,failure):
    *_,decoded=direct_scope(tmp_path)
    trace=decoded['data'][0];root,client=trace['spans'];statuses={'payment-service':200}
    if failure=='missing-timing':client['timing']=None
    elif failure=='bad-duration':client['timing']['duration_ns']+=1
    elif failure=='wrong-route':
        next(tag for tag in root['tags'] if tag['key']=='http.route')['value']='/internal/faults'
    elif failure=='wrong-status':statuses['payment-service']=500
    elif failure=='missing-error':
        next(tag for tag in root['tags'] if tag['key']=='http.response.status_code')['value']=500
        statuses['payment-service']=500
    elif failure=='foreign-span':client['traceID']='a'*32
    elif failure=='db-parent':client['references'][0]['spanID']='a'*16
    elif failure=='db-outside-parent':
        client['timing']['end_unix_ns']=root['timing']['end_unix_ns']+1
        client['timing']['duration_ns']=client['timing']['end_unix_ns']-client['timing']['start_unix_ns']
    elif failure=='internal-root-parent':root['references'][0]['spanID']=client['spanID']
    with pytest.raises(ValueError):
        validate_trace(decoded,TRACE,'probe',statuses,root_service='payment-service')


def test_direct_source_needs_explicit_request_entry_not_automatic_partial_cascade_salvage(tmp_path):
    _,_,_,_,scope,_=direct_scope(tmp_path)
    _,lookup,_=business_logs(scope)
    admitted,_=trace_observations(scope,lookup,END,END+timedelta(seconds=120),END+timedelta(seconds=120),750)
    assert len(admitted)==1 and admitted[0]['entry_service']=='payment-service'
    scope['traces'][0][1].pop('request_entry_service')
    admitted,counts=trace_observations(scope,lookup,END,END+timedelta(seconds=120),END+timedelta(seconds=120),750)
    assert not admitted and counts['unsupported-or-incomplete-trace']==1


def test_verified_direct_route_gets_versioned_support_and_unrelated_trace_is_not_reused(tmp_path):
    source,health,correlation,ref,_,_=direct_scope(tmp_path/'pay')
    report=replay_rca(source,health,correlation,ref,POLICY,tmp_path/'pay-result')
    pay=next(c for c in report['results'][0]['rca']['candidates'] if c['service']=='payment-service')
    assert pay['components']['dependency_trace_relevance']['method']=='native-direct-span-duration-share/v1'
    assert pay['components']['dependency_trace_relevance']['value']==pytest.approx(.999)
    assert pay['score']==pytest.approx(.25*.999+.15)
    source,health,correlation,ref,_,_=direct_scope(tmp_path/'inv','inventory-service')
    report=replay_rca(source,health,correlation,ref,POLICY,tmp_path/'inv-result')
    assert report['results'][0]['source_counts']['trace-outside-group-dependency-path']==1
    for candidate in report['results'][0]['rca']['candidates']:
        assert candidate['components']['dependency_trace_relevance']['value'] is None


def s5_manifest():
    run=run_fixture(scenario='S1')
    run.update(scenario_id='S5',root_cause_ground_truth=None,
        root_causes_ground_truth=['payment-service','inventory-service'],
        affected_services_ground_truth=['checkout-service','payment-service','inventory-service'],fault_method='multi-fault')
    return run


def test_s5_truth_has_two_explicit_roots_while_legacy_single_root_manifests_stay_valid():
    validate_payload('experiment-run.schema.json',s5_manifest())
    for scenario in ('S0','S1','S3'):
        validate_payload('experiment-run.schema.json',run_fixture(scenario=scenario))


@pytest.mark.parametrize('change',[{'root_cause_ground_truth':'payment-service'},
    {'root_causes_ground_truth':['payment-service']},
    {'root_causes_ground_truth':['payment-service','payment-service']},
    {'affected_services_ground_truth':['checkout-service','payment-service']},
    {'fault_method':'sandbox-latency'}])
def test_s5_cannot_hide_multiple_roots_in_a_scalar_or_inconsistent_truth(change):
    run=s5_manifest();run.update(change)
    with pytest.raises(ValueError):validate_payload('experiment-run.schema.json',run)


def test_multi_root_truth_is_rejected_by_both_serving_boundaries():
    from sentinelops.experiments.verification import reject_truth
    from sentinelops.incidents.rca_source import reject_control
    for validator in (reject_truth,reject_control):
        for value in ({'nested':{'root_causes_ground_truth':['payment-service','inventory-service']}},
                      {'key':'sentinelops.root_causes_ground_truth','value':{'stringValue':'hidden label'}}):
            with pytest.raises(ValueError):validator(value)


def test_claimed_direct_snapshot_with_captured_checkout_logs_is_not_a_valid_direct_scope(tmp_path):
    _,_,_,_,scope,_=direct_scope(tmp_path)
    extra={**scope['logs']['records'][0],'service':'checkout-service','request_instance_id':'another-request',
           'span_id':'1'.zfill(16),'method':'POST','route':'/v1/checkout'}
    scope['logs']['records'].append(extra)
    _,lookup,_=business_logs(scope)
    admitted,counts=trace_observations(scope,lookup,END,END+timedelta(seconds=120),END+timedelta(seconds=120),750)
    assert not admitted and counts['unsupported-or-incomplete-trace']==1
