"""API protocol fixtures; no real cluster/action/RCA acceptance is asserted."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import certifi
import httpx
import pytest

from sentinelops.remediation.domain import Blocked, Deployment, KubernetesUnavailable, Pod, Policy, Target, rollback_patch
from sentinelops.remediation.kubernetes import LocalKubernetes, local_server

GOOD = 'localhost:5001/payment@sha256:' + 'a' * 64
BAD = 'localhost:5001/payment@sha256:' + 'b' * 64
TARGET = Target('cluster-fixture', 'sentinelops-b-actions', 'namespace-fixture',
                'payment-service', 'deployment-fixture', 'payment')


def observation(**changes):
    body = dict(target=TARGET, resource_version='125', generation=5, observed_generation=5,
        image=BAD, container_index=0, replicas=1, updated_replicas=1, ready_replicas=1,
        available_replicas=1, max_surge=0, max_unavailable=1, strategy='RollingUpdate',
        paused=False, hpa=False, pods=(Pod('old-pod', BAD, True, 'containerd://sha256:'+'b'*64),),
        observed_at='2026-01-01T00:00:00Z')
    return Deployment(**{**body, **changes})


def api_objects():
    deployment = {'apiVersion':'apps/v1', 'kind':'Deployment', 'metadata':{'uid':TARGET.deployment_uid,
        'name':TARGET.deployment, 'namespace':TARGET.namespace, 'resourceVersion':'125', 'generation':5},
        'spec':{'replicas':1, 'selector':{'matchLabels':{'app':'payment-service'}},
            'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxSurge':0,'maxUnavailable':1}},
            'template':{'spec':{'containers':[{'name':'payment', 'image':BAD,
                'env':[{'name':'SECRET_FIXTURE','value':'must-not-enter-audit'}]}]}}},
        'status':{'observedGeneration':5,'updatedReplicas':1,'readyReplicas':1,'availableReplicas':1}}
    sets = {'items':[{'metadata':{'uid':'owned-rs','ownerReferences':[
        {'controller':True,'kind':'Deployment','uid':TARGET.deployment_uid}]}}]}
    pods = {'items':[{'metadata':{'uid':'old-pod','ownerReferences':[
        {'controller':True,'kind':'ReplicaSet','uid':'owned-rs'}]},
        'spec':{'containers':[{'name':'payment','image':BAD}]},
        'status':{'containerStatuses':[{'name':'payment','ready':True,'state':{'running':{}},
            'imageID':'containerd://sha256:'+'b'*64}]}}]}
    return deployment, sets, pods


def client(tmp_path, handler):
    token = tmp_path/'token'; token.write_text('fixture-private-token-'+'x'*32); token.chmod(0o600)
    ca = tmp_path/'ca.crt'
    # A real, parseable CA bundle that exists on every host (certifi ships with httpx);
    # the system store can be empty in a venv, which made this fixture host-dependent.
    ca.write_bytes(Path(certifi.where()).read_bytes())
    return LocalKubernetes('https://127.0.0.1:6443', TARGET, token_file=token, ca_file=ca,
                           transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('status',[{'allowed':True},{},{'allowed':False,'evaluationError':'unverified'}])
def test_quarantine_observer_rejects_mutation_or_unverified_authorization(tmp_path,status):
    def handler(request):
        assert request.method=='POST' and request.url.path.endswith('/selfsubjectaccessreviews')
        return httpx.Response(201,json={'status':status})
    async def run():
        kube=client(tmp_path,handler)
        try:
            with pytest.raises(Blocked): await kube.require_observer()
        finally: await kube.close()
    asyncio.run(run())


def test_quarantine_observer_checks_all_mutating_target_verbs_without_sending_them(tmp_path):
    verbs=[]
    def handler(request):
        assert request.method=='POST' and request.url.path.endswith('/selfsubjectaccessreviews')
        attrs=json.loads(request.content)['spec']['resourceAttributes']
        assert attrs['namespace']==TARGET.namespace and attrs['name']==TARGET.deployment
        verbs.append(attrs['verb'])
        return httpx.Response(201,json={'status':{'allowed':False}})
    async def run():
        kube=client(tmp_path,handler)
        try: await kube.require_observer()
        finally: await kube.close()
    asyncio.run(run())
    assert verbs==['patch','update','delete','create','deletecollection']


@pytest.mark.parametrize('url', ['http://127.0.0.1:6443', 'https://eks.example.com:6443',
    'https://127.0.0.1:6443/extra', 'https://secret@127.0.0.1:6443', 'https://127.0.0.1:6443?token=secret'])
def test_no_ambient_or_cloud_cluster_and_no_credentials_in_endpoint(url):
    with pytest.raises(ValueError): local_server(url)


@pytest.mark.parametrize('change', [dict(replicas=2), dict(max_surge=1), dict(hpa=True),
    dict(paused=True), dict(pods=()), dict(strategy='Recreate')])
def test_blast_radius_and_external_scaling_are_blocked(change):
    with pytest.raises(Blocked): observation(**change).validate(Policy(TARGET))


def test_patch_is_only_a_digest_image_replace_with_server_side_identity_tests():
    before = observation()
    before.validate(Policy(TARGET))
    patch = rollback_patch(before, GOOD)
    assert [item for item in patch if item['op'] != 'test'] == [
        {'op':'replace','path':'/spec/template/spec/containers/0/image','value':GOOD}]
    assert {'op':'test','path':'/metadata/uid','value':TARGET.deployment_uid} in patch
    assert {'op':'test','path':'/metadata/resourceVersion','value':'125'} in patch
    for invalid in (BAD, 'payment:latest', 'other/payment@sha256:'+'a'*64):
        with pytest.raises(Blocked): rollback_patch(before, invalid)


def test_observation_uses_exact_parent_ownership_and_discards_environment_secrets(tmp_path):
    deployment, sets, pods = api_objects()
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith('/kube-system'): body={'metadata':{'uid':TARGET.cluster_uid}}
        elif request.url.path.endswith('/namespaces/'+TARGET.namespace):
            body={'metadata':{'uid':TARGET.namespace_uid,'labels':{'sentinelops.dev/lane':'B'}}}
        elif request.url.path.endswith('/replicasets'): body=sets
        elif request.url.path.endswith('/pods'): body=pods
        elif request.url.path.endswith('/horizontalpodautoscalers'): body={'items':[]}
        else: body=deployment
        return httpx.Response(200,json=body)
    async def run():
        kube=client(tmp_path,handler)
        try: return await kube.observe()
        finally: await kube.close()
    actual=asyncio.run(run())
    actual.validate(Policy(TARGET))
    assert actual.pods[0].uid == 'old-pod'
    assert 'SECRET' not in json.dumps(actual.projection()) and 'must-not-enter-audit' not in json.dumps(actual.projection())
    assert paths.count('/apis/apps/v1/namespaces/'+TARGET.namespace+'/deployments/payment-service') == 2


@pytest.mark.parametrize('problem', ['parent','namespace','uid','changed','pagination'])
def test_foreign_parent_identity_or_incomplete_inventory_rejected(tmp_path,problem):
    deployment, sets, pods=api_objects(); calls=0
    if problem=='parent': pods['items'][0]['metadata']['ownerReferences'][0]['uid']='foreign-rs'
    if problem=='uid': deployment['metadata']['uid']='recreated-deployment'
    if problem=='pagination': pods['metadata']={'continue':'not-complete'}
    def handler(request):
        nonlocal calls
        if request.url.path.endswith('/kube-system'): body={'metadata':{'uid':TARGET.cluster_uid}}
        elif request.url.path.endswith('/namespaces/'+TARGET.namespace):
            body={'metadata':{'uid':'foreign' if problem=='namespace' else TARGET.namespace_uid,'labels':{'sentinelops.dev/lane':'B'}}}
        elif request.url.path.endswith('/replicasets'): body=sets
        elif request.url.path.endswith('/pods'): body=pods
        elif request.url.path.endswith('/horizontalpodautoscalers'): body={'items':[]}
        else:
            calls+=1;body=deepcopy(deployment)
            if problem=='changed' and calls>1: body['metadata']['resourceVersion']='126'
        return httpx.Response(200,json=body)
    async def run():
        kube=client(tmp_path,handler)
        try: await kube.observe()
        finally: await kube.close()
    with pytest.raises((Blocked,KubernetesUnavailable)): asyncio.run(run())


def test_server_dry_run_is_all_and_failed_precondition_has_no_fallback_patch(tmp_path):
    calls=[];deployment,_,_=api_objects()
    def handler(request):
        calls.append(request)
        assert request.method=='PATCH' and request.headers['Content-Type']=='application/json-patch+json'
        if request.url.params.get('dryRun')=='All':
            actual=deepcopy(deployment);actual['spec']['template']['spec']['containers'][0]['image']=GOOD
            return httpx.Response(200,json=actual)
        return httpx.Response(409,json={'message':'fixture conflict; do not expose provider output'})
    async def run():
        kube=client(tmp_path,handler)
        try:
            await kube.patch(observation(),GOOD,dry_run=True)
            with pytest.raises(Blocked,match='precondition'): await kube.patch(observation(),GOOD,dry_run=False)
        finally: await kube.close()
    asyncio.run(run())
    assert len(calls)==2 and len(json.loads(calls[0].content))==7


def test_deployment_ready_alone_or_old_pod_does_not_prove_new_image_rollout():
    before=observation()
    assert not before.ready_for(GOOD,prior_generation=5,old_pods=('old-pod',))
    updated=replace(before,generation=6,observed_generation=6,image=GOOD,
        pods=(Pod('new-pod',GOOD,True,'containerd://sha256:'+'a'*64),))
    assert updated.ready_for(GOOD,prior_generation=5,old_pods=('old-pod',))
    assert not replace(updated,pods=(replace(updated.pods[0],uid='old-pod'),)).ready_for(
        GOOD,prior_generation=5,old_pods=('old-pod',))
