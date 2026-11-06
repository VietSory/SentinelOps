"""Software source/replay tests; actual Kubernetes coverage lives in native CI."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from sentinelops.contracts import utc_time
from sentinelops.remediation.domain import Target
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE, queries, source_configuration_hash
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.kubernetes_evidence import (
    KubernetesIncidentEvidence, kubernetes_completions, project_inventory, trace_pods,
    verify_kubernetes_binding, verify_pod_counter,
)
from test_incident_evidence import completion
from test_prometheus_capture import END, source_response
from test_slo_health import POLICY as HEALTH_POLICY

IMAGE = 'registry.local/application@sha256:' + 'a'*64
TARGET = Target('cluster','sentinelops-b-actions','ns','payment-service','payment-service-uid','payment')


def inventory():
    deps, sets, pods = {}, {}, {}
    for service in BUSINESS_ROUTES:
        dep = service+'-uid';rs = service+'-rs';pod = service+'-pod'
        container = {'name':service.removesuffix('-service'),'image':IMAGE}
        deps[service] = {'metadata':{'name':service,'namespace':TARGET.namespace,'uid':dep,'generation':1},
            'spec':{'replicas':1,'template':{'spec':{'containers':[container]}}}}
        sets[service] = [{'metadata':{'uid':rs,'ownerReferences':[{'kind':'Deployment','uid':dep,'controller':True}]}}]
        pods[service] = [{'metadata':{'name':pod,'namespace':TARGET.namespace,'uid':pod+'-uid',
            'ownerReferences':[{'kind':'ReplicaSet','uid':rs,'controller':True}]},'spec':{'containers':[container]},
            'status':{'containerStatuses':[{'name':container['name'],'imageID':'containerd://sha256:'+'a'*64,
                                           'state':{'running':{'startedAt':timestamp(END-timedelta(minutes=5))}}}]}}]
    return {'deployments':deps,'replicasets':sets,'pods':pods}


def raw_capture(service):
    return {'capture_version':'1.0','source_profile':SOURCE_PROFILE,'service':service,
        'bucket_end':timestamp(END),'collected_at':timestamp(END+timedelta(seconds=30)),
        'queries':{k:{'expression':q,'response':source_response(k)} for k,q in queries(service).items()}}


def projected(api=None):
    api=api or inventory()
    return project_inventory(**api,target=TARGET,deployment_uids={s:s+'-uid' for s in BUSINESS_ROUTES})


def pod_response(pod, count=120):
    return {'status':'success','data':{'resultType':'vector','result':[
        {'metric':{'k8s_pod_uid':pod['pod_uid'],'k8s_namespace_name':pod['namespace']},
         'value':[END.timestamp(),str(count)]}]}}


@pytest.mark.parametrize('problem',['parent','namespace','image','replicas','duplicate','deployment','container','not-running'])
def test_inventory_rejects_uid_rollover_or_ambiguous_nonbusiness_workload(problem):
    api=inventory();s='payment-service';dep=api['deployments'][s];pod=api['pods'][s][0]
    if problem=='parent':pod['metadata']['ownerReferences'][0]['uid']='different-rs'
    if problem=='namespace':pod['metadata']['namespace']='other'
    if problem=='image':pod['spec']['containers'][0]=dict(pod['spec']['containers'][0],image='mutable:latest')
    if problem=='replicas':dep['spec']['replicas']=2
    if problem=='duplicate':api['pods'][s].append(deepcopy(pod))
    if problem=='deployment':dep['metadata']['uid']='recreated-deployment'
    if problem=='container':dep['spec']['template']['spec']['containers'][0]['name']='sidecar'
    if problem=='not-running':pod['status']['containerStatuses'][0]['state']={'terminated':{}}
    with pytest.raises(ValueError):projected(api)


@pytest.mark.parametrize('problem',['uid','namespace','clock','extra','denominator','nan','zero','warnings','missing'])
def test_pod_binding_rejects_other_identity_or_missing_traffic_instead_of_healthy(problem):
    pod=projected()['payment-service'];body=pod_response(pod);row=body['data']['result'][0]
    if problem=='uid':row['metric']['k8s_pod_uid']='other'
    if problem=='namespace':row['metric']['k8s_namespace_name']='other'
    if problem=='clock':row['value'][0]-=1
    if problem=='extra':body['data']['result'].append(deepcopy(row))
    if problem=='denominator':row['value'][1]='119'
    if problem=='nan':row['value'][1]='NaN'
    if problem=='zero':row['value'][1]='0'
    if problem=='warnings':body['warnings']=['partial source']
    if problem=='missing':body['data']['result']=[]
    with pytest.raises(ValueError):verify_pod_counter(body,pod,raw_capture('payment-service'))


def original_log(pod):
    return completion(pod['service'], timestamp_unix_ms=int(END.timestamp())*1000,duration_ms=100.,
        **{'k8s.pod.uid':pod['pod_uid'],'k8s.pod.name':pod['pod_name'],'k8s.namespace.name':pod['namespace']})


@pytest.mark.parametrize('problem',['uid','name','namespace','truth','extra','clock'])
def test_original_log_identity_and_receipt_are_required(problem):
    pod=projected()['payment-service'];row=original_log(pod)
    if problem=='uid':row['k8s.pod.uid']='other'
    if problem=='name':row['k8s.pod.name']='other'
    if problem=='namespace':row['k8s.namespace.name']='other'
    if problem=='truth':row['root_cause_ground_truth']='fixture'
    if problem=='extra':row['token']='synthetic-not-secret'
    if problem=='clock':row['timestamp_unix_ms']+=120000
    with pytest.raises(ValueError):kubernetes_completions(json.dumps(row).encode(),pod,END,END,END)


def test_trace_checks_every_resource_and_rejects_duplicate_attributes():
    pods=projected();p=pods['payment-service']
    attrs={'service.name':'payment-service','service.instance.id':p['pod_uid'],
           'k8s.pod.uid':p['pod_uid'],'k8s.pod.name':p['pod_name'],'k8s.namespace.name':p['namespace']}
    resource={'resource':{'attributes':[{'key':k,'value':{'stringValue':v}} for k,v in attrs.items()]}}
    body={'result':{'resourceSpans':[resource]}}
    assert trace_pods(json.dumps(body),pods)=={'payment-service'}
    resource['resource']['attributes'].append(resource['resource']['attributes'][0])
    with pytest.raises(ValueError,match='duplicate'):trace_pods(json.dumps(body),pods)


def test_full_readonly_collector_and_replay_pin_original_health_and_pod_logs(tmp_path,monkeypatch):
    from sentinelops.workers import incident_evidence, kubernetes_evidence
    now=END+timedelta(seconds=60)
    monkeypatch.setattr(incident_evidence,'now',lambda:now)
    monkeypatch.setattr(kubernetes_evidence,'now',lambda:now)
    store=ArtifactStore(tmp_path);api=inventory();pods=projected(api)
    config={'global':{'scrape_interval':'5s'},'scrape_configs':[]}
    identity={'environment':'local','endpoint_hash':stable_id({'endpoint':'http://127.0.0.1:9090'}),
              'source_profile':SOURCE_PROFILE,'prometheus_configuration_hash':source_configuration_hash(config)}
    normalization=store.write('manifests',{'raw_refs':[store.write('raw',raw_capture(s)) for s in BUSINESS_ROUTES]})
    batch=store.write('manifests',{'identity':identity,'stream_id':stable_id(identity),
        'bucket_end':timestamp(END),'normalization_manifest_ref':normalization})
    state=store.write('manifests',{'last_batch_ref':batch})
    class FakeKube:
        target=TARGET
        def __init__(self):self.writes=[];self.client=httpx.AsyncClient(base_url="https://127.0.0.1:6443",transport=httpx.MockTransport(self.log))
        async def require_observer(self):pass
        async def observe(self):pass
        async def close(self):await self.client.aclose()
        def log(self,request):
            assert request.method=='GET' and request.url.path.endswith('/log')
            pod=next(p for p in pods.values() if '/'+p['pod_name']+'/' in request.url.path)
            return httpx.Response(200,text=json.dumps(original_log(pod)))
        async def _request(self,method,path,**kwargs):
            assert method=='GET'
            if '/deployments/' in path:return deepcopy(api['deployments'][path.rsplit('/',1)[1]])
            key='replicasets' if path.endswith('/replicasets') else 'pods'
            service=kwargs['params']['labelSelector'][4:]
            return {'items':deepcopy(api[key][service])}
    async def run():
        kube=FakeKube();collector=KubernetesIncidentEvidence(kube,{s:s+'-uid' for s in BUSINESS_ROUTES},
            prometheus_endpoint='http://127.0.0.1:9090',jaeger_endpoint='http://127.0.0.1:16686')
        async def http(url,**kwargs):
            if url.endswith('/api/v1/status/config'):
                import yaml
                return json.dumps({'status':'success','data':{'yaml':yaml.safe_dump(config)}}).encode()
            params=parse_qs(urlsplit(url).query)
            service=next(s for s in pods if s in params['query'][0])
            return json.dumps(pod_response(pods[service])).encode()
        collector._http=http
        try:
            from test_rca_replay import POLICY
            ref=await collector.capture(store,source_state_ref=state,job_id=stable_id('job'),intent_ref=state,
                group={'first_onset_at':timestamp(END-timedelta(seconds=60))},policy=POLICY,
                health_identity=identity,latency_ms=HEALTH_POLICY['latency_ms'])
            captured=store.read(ref)
            assert captured['capture_version']=='continuous-incident-evidence/v3'
            assert captured['actions_allowed'] is False
            assert verify_kubernetes_binding(store,captured,identity)==pods
            for problem in ('state','counter','log','inventory'):
                bad=deepcopy(captured)
                if problem=='state':bad['physical_binding']['feature_batch_ref']=state
                if problem=='counter':bad['physical_binding']['pod_counters'][0]['response']['data']['result'][0]['value'][1]='1'
                if problem=='inventory':bad['source_containers']['payment-service']['pod_uid']='forged'
                if problem=='log':
                    logs=deepcopy(store.read(bad['evidence_refs']['logs']))
                    logs['source_receipts'][0]['identity_records'][0]['k8s.pod.uid']='other'
                    bad['evidence_refs']['logs']=store.write('raw',logs)
                with pytest.raises((ValueError,KeyError)):verify_kubernetes_binding(store,bad,identity)
        finally:await collector.close()
    asyncio.run(run())

@pytest.mark.parametrize('with_model',[False,True])
def test_continuous_rca_replays_kubernetes_versions_without_docker_identity_alias(with_model,tmp_path):
    from test_incident_evidence import continuous_fixture
    from sentinelops.ml.registry import ModelRegistry
    from sentinelops.workers.incident_rca import derive_continuous
    store,intent_ref,intent,closed,captured,profile,identity=continuous_fixture(tmp_path)
    api=inventory();pods=projected(api)
    identity.update(source_profile=SOURCE_PROFILE,endpoint_hash=stable_id({'endpoint':'http://127.0.0.1:9090'}))
    closed['scope']['health_stream_id']=stable_id(identity)
    closed['scope_ref']=store.write('manifests',closed['scope'])
    from sentinelops.incidents.correlation import correlate_health
    closed['correlation']=correlate_health([x['signal'] for x in closed['opening_objects']],
        closed['correlation']['topology_snapshot'],as_of=utc_time(closed['correlation']['as_of']),
        source_scope_sha256=stable_id(closed['scope']))
    intent['group_id']=closed['correlation']['groups'][0]['group_id']
    intent.update(scope_ref=closed['scope_ref'],correlation_ref=store.write('manifests',closed['correlation']))
    intent_ref=store.write('manifests',intent)
    logs=deepcopy(store.read(captured['evidence_refs']['logs']))
    receipts=[]
    for service,pod in pods.items():
        rows=[r for r in logs['records'] if r['service']==service]
        originals=[dict(r,**{'k8s.pod.uid':pod['pod_uid'],'k8s.pod.name':pod['pod_name'],
                           'k8s.namespace.name':pod['namespace']}) for r in rows]
        receipts.append(dict(pod,collected_at=logs['collected_at'],selection={},identity_records=originals))
    logs['source_receipts']=receipts
    captured['evidence_refs']['logs']=store.write('raw',logs)
    # No trace is also a supported unavailable modality; identity-positive trace
    # collection is exercised by the independent original-resource test and CI.
    captured['evidence_refs']['traces']=[];captured['evidence_refs']['infrastructure']=[]
    profile.update(profile_version='kubernetes-incident-evidence/v1',target=vars(TARGET),
        deployment_uids={s:s+'-uid' for s in pods},prometheus_endpoint='http://127.0.0.1:9090',infrastructure_source=None)
    # Use this RCA fixture's clocks; they deliberately differ from the HTTP fixture's.
    end=utc_time(captured['window_end'])
    raws=[];counters=[]
    for service in pods:
        raw=raw_capture(service);raw['bucket_end']=timestamp(end);raw['collected_at']=timestamp(end+timedelta(seconds=30))
        for k,q in raw['queries'].items():q['response']=source_response(k,end)
        ref=store.write('raw',raw);raws.append(ref)
        body=pod_response(pods[service]);body['data']['result'][0]['value'][0]=end.timestamp()
        from sentinelops.workers.kubernetes_evidence import pod_query
        counters.append({'raw_ref':ref,'expression':pod_query(service),'response':body,
                         'collected_at':captured['analyzed_at']})
    norm=store.write('manifests',{'raw_refs':raws,'feature_refs':[]})
    batch=store.write('manifests',{'identity':identity,'stream_id':stable_id(identity),
        'bucket_end':timestamp(end),'normalization_manifest_ref':norm})
    state=store.write('manifests',{'last_batch_ref':batch})
    captured.update(capture_version='continuous-incident-evidence/v4' if with_model else 'continuous-incident-evidence/v3',
        health_stream_id=stable_id(identity),intent_ref=intent_ref,source_containers={**pods,'kubernetes_api':api},
        physical_binding={'source_state_ref':state,'feature_batch_ref':batch,'pod_counters':counters,'inventory_after':api})
    registry=None
    if with_model:
        registry=ModelRegistry();profile['model_release_ref']=registry.identity
        captured['model_evidence']={'release_ref':registry.identity,'source_state_ref':state,'feature_batch_ref':batch,'signals':[]}
    result=derive_continuous(store,intent_ref=intent_ref,intent=intent,closed=closed,
        capture_ref=store.write('manifests',captured),profile=profile,health_identity=identity,
        health_policy=HEALTH_POLICY,model_registry=registry)
    assert result['rca']['abstained'] is True
