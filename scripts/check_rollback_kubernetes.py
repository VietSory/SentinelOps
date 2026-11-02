"""Actual local Kubernetes API protocol test with explicitly NONPRODUCT fixtures.

Uses a tiny HTTP container, not SentinelOps Payment/SLO/RCA evidence. No approved
known-good ledger, H4 grant, automatic controller action or G6 acceptance is made.
Every cluster/registry has a fresh owned identity; no ambient kubeconfig is used.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from uuid import uuid4

import yaml

from sentinelops.remediation.domain import Blocked,KubernetesUnavailable,Policy,Target
from sentinelops.remediation.kubernetes import LocalKubernetes
from sentinelops.telemetry.core import timestamp

PROJECT=Path(__file__).resolve().parents[1]
NODE='kindest/node:v1.34.0@sha256:7416a61b42b1662ca6ca89f02028ac133a309a2a30ba309614e8ec94d976dc5a'
OWNER='sentinelops-b-kube-api-tests'


def command(args,*,data=None,timeout=300):
    return subprocess.check_output(args,input=data,stderr=subprocess.PIPE,timeout=timeout)


def private_file(path,data):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(data)


class Sandbox:
    def __init__(self,root,kind):
        self.root,self.kind=root,kind
        self.name='sentinelops-b-actions-'+uuid4().hex[:10]
        self.registry=self.name+'-registry'
        self.kubeconfig=root/'kubeconfig'
        self.nodes=[];self.registry_created=False;self.cluster_started=False

    def docker(self,*args,data=None): return command(['docker',*args],data=data)

    def kubectl(self,*args,data=None):
        return command(['kubectl','--kubeconfig',str(self.kubeconfig),'--context','kind-'+self.name,*args],data=data)

    def require_registry(self):
        labels=json.loads(self.docker('inspect','--format','{{json .Config.Labels}}',self.registry))
        if labels.get('sentinelops.owner')!=OWNER or labels.get('sentinelops.checkout')!=str(PROJECT):
            raise ValueError('registry ownership mismatch')

    def lock_image(self,image):
        self.docker('pull',image)
        info=json.loads(self.docker('image','inspect',image))[0]
        return info['Id'],info['RepoDigests'][0]

    def create(self):
        print(json.dumps({'stage':'creating-exclusive-local-cluster','cluster':self.name}),flush=True)
        if self.docker('ps','-aq','--filter','label=io.x-k8s.kind.cluster='+self.name).strip():
            raise ValueError('refuse to reuse an existing cluster')
        registry_image,_=self.lock_image('registry:3')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0));self.port=reservation.getsockname()[1]
            self.docker('create','--name',self.registry,'--label','sentinelops.owner='+OWNER,
                '--label','sentinelops.checkout='+str(PROJECT),'--memory','128m','--cpus','0.5',
                '-p',f'127.0.0.1:{self.port}:5000',registry_image)
            self.registry_created=True
        self.require_registry();self.docker('start',self.registry)
        config={'kind':'Cluster','apiVersion':'kind.x-k8s.io/v1alpha4',
                'networking':{'apiServerAddress':'127.0.0.1'},'nodes':[{'role':'control-plane'}],
                'containerdConfigPatches':['[plugins."io.containerd.cri.v1.images".registry]\nconfig_path = "/etc/containerd/certs.d"']}
        config_path=self.root/'kind.yaml';config_path.write_text(yaml.safe_dump(config))
        self.cluster_started=True
        command([self.kind,'create','cluster','--name',self.name,'--kubeconfig',str(self.kubeconfig),
                 '--config',str(config_path),'--image',NODE,'--wait','120s'])
        self.kubeconfig.chmod(0o600)
        self.nodes=self.docker('ps','-aq','--filter','label=io.x-k8s.kind.cluster='+self.name).decode().split()
        if len(self.nodes)!=1: raise ValueError('test requires one exclusively owned node')
        try:
            self.docker('update','--cpus','2','--memory','2g','--memory-swap','2g',self.nodes[0])
        except subprocess.CalledProcessError as exc:
            # This fixed resource command has no credentials; expose its actual
            # diagnostic rather than guessing or silently dropping the bounds.
            raise RuntimeError('local node resource bound rejected: '+exc.stderr.decode(errors='replace')) from None
        self.docker('network','connect','kind',self.registry)
        directory=f'/etc/containerd/certs.d/localhost:{self.port}'
        self.docker('exec',self.nodes[0],'mkdir','-p',directory)
        body=(f'server = "http://{self.registry}:5000"\n'
              f'[host."http://{self.registry}:5000"]\ncapabilities = ["pull", "resolve"]\n').encode()
        self.docker('exec','-i',self.nodes[0],'cp','/dev/stdin',directory+'/hosts.toml',data=body)
        self.kubectl('apply','-f',str(PROJECT/'infra/kubernetes/payment-rollback-rbac.yaml'))
        self.kubectl('apply','-f',str(PROJECT/'infra/kubernetes/payment-quarantine-observer-rbac.yaml'))

    def fixture_image(self,base,version):
        print(json.dumps({'stage':'building-explicit-software-fixture','variant':version}),flush=True)
        directory=self.root/version;directory.mkdir()
        (directory/'index.html').write_text('Kubernetes API software fixture only.\n')
        instruction='["httpd","-f","-p","8080","-h","/www"]' if version=='good' else '["sh","-c","exit 1"]'
        (directory/'Dockerfile').write_text('FROM '+base+'\nLABEL sentinelops.fixture-only="true"\nCOPY index.html /www/index.html\nCMD '+instruction+'\n')
        tag=f'localhost:{self.port}/payment-fixture:{version}'
        self.docker('build','-t',tag,str(directory));self.docker('push',tag)
        digests=json.loads(self.docker('image','inspect','--format','{{json .RepoDigests}}',tag))
        return next(ref for ref in digests if ref.startswith(f'localhost:{self.port}/payment-fixture@sha256:'))

    def deploy(self,image):
        body={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'payment-service','namespace':'sentinelops-b-actions',
            'annotations':{'sentinelops.dev/fixture-only':'true'}},'spec':{'replicas':1,
            'selector':{'matchLabels':{'app':'payment-service'}},
            'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxSurge':0,'maxUnavailable':1}},
            'template':{'metadata':{'labels':{'app':'payment-service'}},'spec':{'containers':[{'name':'payment',
                'image':image,'ports':[{'containerPort':8080}],
                'resources':{'requests':{'cpu':'10m','memory':'8Mi'},'limits':{'cpu':'100m','memory':'32Mi'}},
                'readinessProbe':{'httpGet':{'path':'/','port':8080},'periodSeconds':1,'failureThreshold':1}}]}}}}
        self.kubectl('apply','-f','-',data=json.dumps(body).encode())

    def identity(self):
        cluster=json.loads(self.kubectl('get','namespace','kube-system','-o','json'))
        namespace=json.loads(self.kubectl('get','namespace','sentinelops-b-actions','-o','json'))
        deployment=json.loads(self.kubectl('-n','sentinelops-b-actions','get','deployment','payment-service','-o','json'))
        config=yaml.safe_load(self.kubeconfig.read_text())
        selected=config['clusters'][0]['cluster']
        private_file(self.root/'ca.crt',base64.b64decode(selected['certificate-authority-data']))
        token=self.kubectl('-n','sentinelops-b-actions','create','token','payment-rollback','--duration=10m').strip()
        private_file(self.root/'runtime-token',token)
        observer=self.kubectl('-n','sentinelops-b-actions','create','token','payment-quarantine-observer','--duration=10m').strip()
        private_file(self.root/'observer-token',observer)
        target=Target(cluster['metadata']['uid'],'sentinelops-b-actions',namespace['metadata']['uid'],
                      'payment-service',deployment['metadata']['uid'],'payment')
        return selected['server'],target

    def cleanup(self):
        if self.cluster_started:
            nodes=self.docker('ps','-aq','--filter','label=io.x-k8s.kind.cluster='+self.name).decode().split()
            if self.nodes and set(nodes)!=set(self.nodes): raise ValueError('cluster node identity changed; refuse cleanup')
            if nodes:
                command([self.kind,'delete','cluster','--name',self.name,'--kubeconfig',str(self.kubeconfig)])
        if self.registry_created:
            self.require_registry();self.docker('rm','-f','-v',self.registry)
        for filename in ('runtime-token','observer-token','kubeconfig'):
            (self.root/filename).unlink(missing_ok=True)  # Only credentials newly created by this test.

    def diagnostics(self):
        result={}
        for key,args in [('pods',('-n','sentinelops-b-actions','get','pods','-o','json')),
                         ('events',('-n','sentinelops-b-actions','get','events','-o','json')),
                         ('nodes',('get','nodes','-o','json'))]:
            try:
                value=json.loads(self.kubectl(*args))
                if key=='events':
                    result[key]=[{'reason':i.get('reason'),'message':i.get('message')} for i in value['items']]
                else:
                    result[key]=[{'name':i.get('metadata',{}).get('name'),'status':i.get('status',{})} for i in value['items']]
            except (subprocess.CalledProcessError,ValueError): result[key]='diagnostics-unavailable'
        with (self.root/'diagnostics.json').open('x') as stream: json.dump(result,stream,indent=2)


async def verify(sandbox,endpoint,target,good,bad):
    client=LocalKubernetes(endpoint,target,token_file=sandbox.root/'runtime-token',ca_file=sandbox.root/'ca.crt')
    observer=LocalKubernetes(endpoint,target,token_file=sandbox.root/'observer-token',ca_file=sandbox.root/'ca.crt')
    policy=Policy(target)
    async def wait_for(image,*,ready,prior=0,old=()):
        last=None;error=None
        for _ in range(120):
            try:
                observed=await client.observe();observed.validate(policy,before_dispatch=False)
                last=observed.projection()
                if observed.image==image and (not ready or observed.ready_for(image,prior_generation=prior,old_pods=old)):
                    if ready or (len(observed.pods)==1 and observed.pods[0].image==image
                                 and observed.pods[0].image_id and not observed.pods[0].ready):
                        return observed
            except KubernetesUnavailable as exc: error=str(exc)
            await asyncio.sleep(.5)
        with (sandbox.root/'last-observation.json').open('x') as stream:
            json.dump({'observation':last,'last_error':error},stream,indent=2)
        raise AssertionError('fixture deployment did not reach requested observed state; diagnostics retained')
    try:
        healthy=await wait_for(good,ready=True)
        await observer.require_observer()
        observed=await observer.observe()
        assert observed.target==target and observed.image==good
        try:
            await client.require_observer()
            raise AssertionError('writer token accepted for manual observer review')
        except Blocked: pass
        print(json.dumps({'stage':'fixture-ready-testing-real-dryrun-and-CAS'}),flush=True)
        sandbox.kubectl('-n',target.namespace,'set','image','deployment/payment-service','payment='+bad)
        before=await wait_for(bad,ready=False)
        before.validate(policy)
        try:
            await observer.patch(before,good,dry_run=False)
            raise AssertionError('quarantine observer mutated Payment image')
        except Blocked: pass
        assert (await client.observe()).image==bad
        assert not before.ready_for(bad,prior_generation=healthy.generation,old_pods=[p.uid for p in healthy.pods])
        await client.patch(before,good,dry_run=True)
        unchanged=await wait_for(bad,ready=False)
        assert unchanged.image==bad
        try:
            await client.patch(replace(unchanged,resource_version='0'),good,dry_run=False)
            raise AssertionError('stale resource-version patch succeeded')
        except Blocked: pass
        for path in ('/api/v1/namespaces/'+target.namespace+'/secrets',
            '/apis/apps/v1/namespaces/'+target.namespace+'/deployments/inventory-service',
            '/api/v1/namespaces/default'):
            response=await client.client.get(path)
            assert response.status_code==403,'runtime RBAC exceeded allowed local target/read scope'
        before=await wait_for(bad,ready=False)
        await client.patch(before,good,dry_run=False)
        after=await wait_for(good,ready=True,prior=before.generation,old=[p.uid for p in before.pods])
        return {'status':'PASS','scope':'local-kubernetes-API-software-fixture',
            'workload':'tiny-HTTP-fixture-NOT-SentinelOps-Payment','H4_accepted':False,'G6_accepted':False,
            'telemetry_recovery_verified':False,'approved_known_good_ledger_created':False,
            'successful_runtime_image_patches':1,'kind_node':NODE,'good_image':good,'bad_image':bad,
            'target':after.projection()['target'],'before':before.projection(),'after':after.projection(),
            'checks':['real TLS and scoped ServiceAccount','observer SSAR denies all target mutation verbs; writer token rejected',
                'observer image PATCH actually rejected by RBAC; bad image unchanged','server dryRun=All leaves bad image unchanged',
                'stale JSON-patch resourceVersion rejected','secrets/foreign Deployment/foreign namespace denied',
                'actual image patch replaces pod UID and observes new ready generation']}
    finally:
        try: await observer.close()
        finally: await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--kind',default=shutil.which('kind'))
    args=parser.parse_args()
    if not args.kind: parser.error('an explicit installed kind binary is required')
    root=args.output.resolve()
    if not root.is_relative_to((PROJECT/'artifacts/lane-b').resolve()):
        raise ValueError('Kubernetes test artifacts must stay in the owned ignored B root')
    root.mkdir(parents=True,exist_ok=False)
    sandbox=Sandbox(root,args.kind)
    hashes={name:hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() for name in (
        'scripts/check_rollback_kubernetes.py','src/sentinelops/remediation/kubernetes.py',
        'infra/kubernetes/payment-rollback-rbac.yaml','infra/kubernetes/payment-quarantine-observer-rbac.yaml')}
    try:
        sandbox.create()
        _,base=sandbox.lock_image('busybox:1.37.0')
        good=sandbox.fixture_image(base,'good');bad=sandbox.fixture_image(base,'bad')
        for image in (good,bad):
            try: sandbox.docker('exec',sandbox.nodes[0],'crictl','pull',image)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError('local anonymous registry CRI pull failed: '+exc.stderr.decode(errors='replace')) from None
        sandbox.deploy(good)
        endpoint,target=sandbox.identity()
        report=asyncio.run(verify(sandbox,endpoint,target,good,bad))
        report['observed_at']=timestamp(datetime.now(timezone.utc))
        report['loaded_source_sha256']=hashes
        report['observer_changed_during_run']=any(hashlib.sha256((PROJECT/name).read_bytes()).hexdigest()!=sha for name,sha in hashes.items())
        with (root/'verification.json').open('x') as stream: json.dump(report,stream,indent=2)
        print('LOCAL KUBERNETES API SOFTWARE ACCEPTANCE: PASS '+str(root/'verification.json'))
    except Exception:
        if sandbox.kubeconfig.exists(): sandbox.diagnostics()
        raise
    finally: sandbox.cleanup()


if __name__=='__main__': main()
