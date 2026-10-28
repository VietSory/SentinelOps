from datetime import timedelta
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sentinelops.contracts import validate_payload
from sentinelops.incidents.correlation_replay import replay_correlation
from sentinelops.incidents.health_replay import replay_health_manifest
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.telemetry.core import BUSINESS_ROUTES,stable_id,timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.store import ArtifactStore
from test_slo_health import END,capture,POLICY as HEALTH_POLICY

ROOT=Path(__file__).resolve().parents[1]
POLICY=json.loads((ROOT/'configs/rca_policy.json').read_text())
TOPOLOGY=yaml.safe_load((ROOT/'configs/topology.yaml').read_text())
TRACE='0123456789abcdef0123456789abcdef'


def source_scope(tmp_path,*,fresh=False,future=False,healthy=False,control=False,entry=None):
    telemetry=ArtifactStore(tmp_path/'source')
    captured=[capture(0),capture(1)]
    if healthy:
        for raw in captured:raw['queries']['errors']['response']['data']['result'][0]['value'][1]='0'
    norm=process_captures(captured,telemetry)
    base=int(END.timestamp())*1_000_000_000
    entries=[
        ('checkout-service','1','9',2,0,1_200_000_000,None),
        ('checkout-service','2','1',3,1_000_000,6_000_000,'inventory-service'),
        ('checkout-service','4','1',3,10_000_000,1_110_000_000,'payment-service'),
        ('inventory-service','3','2',2,2_000_000,5_000_000,None),
        ('inventory-service','6','3',3,2_100_000,3_100_000,'rds'),
        ('payment-service','5','4',2,15_000_000,1_015_000_000,None),
        ('payment-service','7','5',3,1_013_000_000,1_014_000_000,'rds')]
    if entry=='inventory-service':
        entries=[(*item[:5],1_002_000_000,item[6]) if item[1]=='3' else item for item in entries]
    resources=[];logs=[]
    for service in BUSINESS_ROUTES:
        spans=[]
        for owner,identifier,parent,kind,start,end,peer in entries:
            if owner!=service:continue
            attrs=[{'key':'sentinelops.correlation_id','value':{'stringValue':'probe'}}]
            if peer:attrs.append({'key':'peer.service','value':{'stringValue':peer}})
            if peer=='rds':attrs.append({'key':'db.system.name','value':{'stringValue':'postgresql'}})
            else:attrs.append({'key':'http.response.status_code','value':{'intValue':'200'}})
            if entry is not None and kind==2:
                method,route=BUSINESS_ROUTES[service]
                attrs.extend([{'key':'http.request.method','value':{'stringValue':method}},
                              {'key':'http.route','value':{'stringValue':route}}])
            spans.append({'traceId':TRACE,'spanId':identifier.zfill(16),'parentSpanId':parent.zfill(16),'kind':kind,
                'attributes':attrs,'status':{},'startTimeUnixNano':str(base+start),'endTimeUnixNano':str(base+end)})
            if kind==2:
                method,route=BUSINESS_ROUTES[service]
                logs.append({'event':'request_completed','service':service,'method':method,'route':route,
                    'trace_id':TRACE,'span_id':identifier.zfill(16),'correlation_id':'probe',
                    'request_instance_id':'req-'+service,'timestamp_unix_ms':(base+end)//1_000_000,
                    'duration_ms':(end-start)/1_000_000,'status_code':200})
        resources.append({'resource':{'attributes':[{'key':'service.name','value':{'stringValue':service}}]},'scopeSpans':[{'spans':spans}]})
    trace={'kind':'trace','trace_id':TRACE,'body':json.dumps({'result':{'resourceSpans':resources}})}
    if entry is not None:
        trace['request_entry_service']=entry
        trace['body']=json.dumps({'result':{'resourceSpans':[r for r in resources if r['resource']['attributes'][0]['value']['stringValue']==entry]}})
        logs=[row for row in logs if row['service']==entry]
    log_capture={'kind':'serving-logs','records':logs}
    if fresh:
        trace['collected_at']=log_capture['collected_at']=timestamp(END+timedelta(seconds=40 if not future else 180))
    if control:logs[0]['fault_mode']='injected-evaluation-truth'
    trace_ref=telemetry.write('raw',trace);logs_ref=telemetry.write('raw',log_capture)
    infrastructure={'schema_version':'1.0','component':'rds','environment':'local','observed_at':timestamp(END+timedelta(seconds=30)),
        'available_at':timestamp(END+timedelta(seconds=31)),'measurement_scope':'cluster-client-connections-excluding-collector',
        'source_ref':'postgres-stat://fixture:5432','metrics':{'db_connections_count':2,'max_connections':100,
            'reserved_connections':3,'db_connections_pct':2/97*100}}
    infrastructure['record_id']=stable_id({k:v for k,v in infrastructure.items() if k!='available_at'})
    infra_ref=telemetry.write('observations',infrastructure)
    source={k:v for k,v in norm.items() if k!='manifest_ref'}
    source.update(normalization_manifest_ref=norm['manifest_ref'],evidence_refs={'traces':[trace_ref],'logs':logs_ref,'infrastructure':[infra_ref]})
    source_ref=telemetry.write('manifests',source)
    health=replay_health_manifest(telemetry,source_ref,HEALTH_POLICY,tmp_path/'health')
    health_store=ArtifactStore(tmp_path/'health')
    correlated=replay_correlation(health_store,health['report_ref'],TOPOLOGY,tmp_path/'correlation')
    return telemetry,health_store,ArtifactStore(tmp_path/'correlation'),correlated['report_ref'],logs_ref


def test_verified_raw_adapter_scores_actual_fields_and_archives_a_portable_replay(tmp_path):
    source,health,correlation,ref,_=source_scope(tmp_path)
    first=replay_rca(source,health,correlation,ref,POLICY,tmp_path/'one')
    second=replay_rca(source,health,correlation,ref,POLICY,tmp_path/'two')
    assert first==second and first['group_count']==1
    result=first['results'][0]['rca'];validate_payload('rca-result.schema.json',result)
    pay=next(c for c in result['candidates'] if c['service']=='payment-service')
    assert pay['score']==pytest.approx(.25*(999_000_000/1_200_000_000)+.15)
    assert pay['components']['anomaly_strength']['value'] is None
    assert pay['components']['recent_change_support']['value'] is None
    assert result['ranking'][0]=='payment-service' and result['abstained']
    assert 'source-availability-unverified' in result['abstention_reasons']
    archived=ArtifactStore(tmp_path/'one')
    for reference in first['source_artifact_refs']:archived.read(reference)
    copied=replay_rca(archived,archived,archived,ref,POLICY,tmp_path/'portable')
    assert copied==first and first['source_health_ref'] is not None
    with pytest.raises(FileExistsError):replay_rca(source,health,correlation,ref,POLICY,tmp_path/'one')


def test_known_future_receipts_cannot_enter_the_declared_analytical_cutoff(tmp_path):
    source,health,correlation,ref,_=source_scope(tmp_path,fresh=True,future=True)
    report=replay_rca(source,health,correlation,ref,POLICY,tmp_path/'out')
    result=report['results'][0]
    pay=next(c for c in result['rca']['candidates'] if c['service']=='payment-service')
    assert pay['components']['dependency_trace_relevance']['value'] is None
    assert pay['components']['log_support']['value'] is None
    assert result['source_counts']['trace-not-available']==1


def test_no_openings_produces_no_fake_rca_or_healthy_model_result(tmp_path):
    source,health,correlation,ref,_=source_scope(tmp_path,healthy=True)
    report=replay_rca(source,health,correlation,ref,POLICY,tmp_path/'out')
    assert report['group_count']==0 and report['results']==[]
    assert report['model_outputs_available'] is False and report['actions_allowed'] is False


def test_original_log_hash_corruption_fails_before_output_or_scoring(tmp_path):
    source,health,correlation,ref,logs_ref=source_scope(tmp_path)
    path=source.root/logs_ref.removeprefix('artifact://')
    path.write_text('corrupt')
    with pytest.raises(ValueError,match='checksum'):replay_rca(source,health,correlation,ref,POLICY,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_control_truth_in_admitted_business_records_is_not_accepted_as_evidence(tmp_path):
    source,health,correlation,ref,_=source_scope(tmp_path,control=True)
    with pytest.raises(ValueError,match='control/truth'):
        replay_rca(source,health,correlation,ref,POLICY,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_trace_support_requires_completion_log_receipt_by_cutoff(tmp_path):
    from sentinelops.incidents.rca_source import business_logs, trace_observations, verify_scope
    source,health,correlation,ref,_=source_scope(tmp_path,fresh=True)
    scope=verify_scope(source,health,correlation,ref)
    rows,lookup,_=business_logs(scope)
    scope['logs']['collected_at']=timestamp(END+timedelta(seconds=180))
    observed,counts=trace_observations(scope,lookup,END,END+timedelta(seconds=120),END+timedelta(seconds=120),750)
    assert not observed and counts['trace-not-available']==1
    scope['logs'].pop('collected_at')
    observed,_=trace_observations(scope,lookup,END,END+timedelta(seconds=120),END+timedelta(seconds=120),750)
    assert len(observed)==1 and observed[0]['received_at'] is None


def test_unbounded_numeric_log_fields_are_invalid_evidence_not_adapter_crashes(tmp_path):
    from sentinelops.incidents.rca_source import business_logs,verify_scope
    source,health,correlation,ref,_=source_scope(tmp_path)
    scope=verify_scope(source,health,correlation,ref)
    scope['logs']['records'][0]['duration_ms']=10**1000
    scope['logs']['records'][1]['timestamp_unix_ms']=10**1000
    rows,_,counts=business_logs(scope)
    assert len(rows)==1 and sum(counts.values())==2


def test_db_client_outside_parent_server_is_not_duration_or_error_support(tmp_path):
    from sentinelops.incidents.rca_source import business_logs,trace_observations,verify_scope
    from sentinelops.incidents.rca_replay import _trace_support
    source,health,correlation,ref,_=source_scope(tmp_path)
    scope=verify_scope(source,health,correlation,ref)
    _,lookup,_=business_logs(scope)
    traces,_=trace_observations(scope,lookup,END,END+timedelta(seconds=120),END+timedelta(seconds=120),750)
    trace=deepcopy(traces[0]);child=trace['children']['payment-service'][0]
    child['timing']['end_unix_ns']=trace['servers']['payment-service']['timing']['end_unix_ns']+1
    assert _trace_support(trace,'rds') is None
    assert _trace_support(trace,'payment-service') is None
    trace['statuses']['payment-service']=500
    assert _trace_support(trace,'rds') is None
    assert _trace_support(trace,'payment-service') is None
