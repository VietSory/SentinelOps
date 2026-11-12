"""Q-10 executor/adapter integration over HTTP fixtures, not native action evidence."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest

from sentinelops.remediation.domain import rollback_patch
from test_remediation_executor import setup
from test_remediation_kubernetes import GOOD, TARGET, api_objects, client, observation
from test_remediation_worker import resume


class KubernetesWire:
    """Record actual adapter requests, including an applied patch with a lost response."""

    def __init__(self):
        self.deployment, self.sets, self.pods = api_objects()
        self.cluster_uid = TARGET.cluster_uid
        self.namespace_uid = TARGET.namespace_uid
        self.hpas = []
        self.requests = []
        self.mutations = 0
        self.lose_patch_response = False
        self.deny_dry_run = False

    @property
    def patches(self):
        return [request for request in self.requests if request.method == 'PATCH']

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.method == 'PATCH':
            assert path.endswith('/deployments/payment-service')
            assert request.headers['Content-Type'] == 'application/json-patch+json'
            assert json.loads(request.content) == rollback_patch(observation(), GOOD)
            if request.url.params.get('dryRun') == 'All':
                if self.deny_dry_run:
                    return httpx.Response(403, json={'message': 'fixture denial'})
                result = deepcopy(self.deployment)
                result['spec']['template']['spec']['containers'][0]['image'] = GOOD
                return httpx.Response(200, json=result)
            assert not request.url.params
            self.mutations += 1
            self.deployment['metadata'].update(resourceVersion='126', generation=6)
            self.deployment['spec']['template']['spec']['containers'][0]['image'] = GOOD
            self.deployment['status']['observedGeneration'] = 6
            self.sets['items'][0]['metadata']['uid'] = 'new-owned-rs'
            pod = self.pods['items'][0]
            pod['metadata']['uid'] = 'new-pod'
            pod['metadata']['ownerReferences'][0]['uid'] = 'new-owned-rs'
            pod['spec']['containers'][0]['image'] = GOOD
            pod['status']['containerStatuses'][0]['imageID'] = 'containerd://sha256:' + 'a' * 64
            if self.lose_patch_response:
                raise httpx.ReadTimeout('fixture: server committed before response was lost', request=request)
            return httpx.Response(200, json=self.deployment)
        assert request.method == 'GET', 'executor must not introduce another mutation verb'
        if path.endswith('/kube-system'):
            body = {'metadata': {'uid': self.cluster_uid}}
        elif path.endswith('/namespaces/' + TARGET.namespace):
            body = {'metadata': {'uid': self.namespace_uid, 'labels': {'sentinelops.dev/lane': 'B'}}}
        elif path.endswith('/replicasets'):
            body = self.sets
        elif path.endswith('/pods'):
            body = self.pods
        elif path.endswith('/horizontalpodautoscalers'):
            body = {'items': self.hpas}
        else:
            assert path.endswith('/deployments/payment-service')
            body = self.deployment
        return httpx.Response(200, json=body)


def attach(fixture, tmp_path, wire):
    kube = client(tmp_path, wire)
    kube.clock = fixture.clock
    fixture.kube = fixture.executor.kube = kube
    return kube


@pytest.mark.parametrize('problem', ['cluster-uid', 'namespace-uid', 'deployment-uid', 'replicas', 'hpa'])
def test_executor_rejects_adapter_identity_and_blast_radius_before_any_patch(tmp_path, problem):
    async def run():
        f, wire = setup(), KubernetesWire()
        if problem == 'cluster-uid':
            wire.cluster_uid = 'foreign-cluster'
        elif problem == 'namespace-uid':
            wire.namespace_uid = 'foreign-namespace'
        elif problem == 'deployment-uid':
            wire.deployment['metadata']['uid'] = 'recreated-deployment'
        elif problem == 'replicas':
            wire.deployment['spec']['replicas'] = 2
        else:
            wire.hpas = [{'spec': {'scaleTargetRef': {'kind': 'Deployment', 'name': TARGET.deployment}}}]
        kube = attach(f, tmp_path, wire)
        try:
            result = await f.executor.execute(f.request)
            assert result['phase'] == 'blocked' and result['facts']['reason']
            assert wire.requests and not wire.patches and wire.mutations == 0
            assert await f.executor.execute(f.request) == result
            assert not wire.patches
        finally:
            await kube.close()
    asyncio.run(run())


def test_executor_preserves_adapter_dry_run_denial_without_sending_a_real_patch(tmp_path):
    async def run():
        f, wire = setup(), KubernetesWire()
        wire.deny_dry_run = True
        kube = attach(f, tmp_path, wire)
        try:
            result = await f.executor.execute(f.request)
            assert result['phase'] == 'blocked'
            assert result['facts']['reason'] == 'kubernetes-rbac-denied'
            assert len(wire.patches) == 1 and wire.patches[0].url.params.get('dryRun') == 'All'
            assert wire.mutations == 0
        finally:
            await kube.close()
    asyncio.run(run())


@pytest.mark.parametrize('telemetry_available', [True, False], ids=['recovered', 'escalated'])
def test_lost_http_patch_receipt_reconciles_after_restart_without_resending(tmp_path, telemetry_available):
    async def run():
        f, wire = setup(), KubernetesWire()
        wire.lose_patch_response = True
        kube = attach(f, tmp_path, wire)
        try:
            state = await f.executor.execute(f.request)
            assert state['phase'] == 'dispatching'
            assert state['facts']['reason'] == 'kubernetes-delivery-uncertain'
            assert wire.mutations == 1
            # New disabled controller over the same journal: no authority or known-good provider.
            worker = resume(f)
            async def active(target):
                state = await f.journal.get(None)
                # The memory fixture has no target table. Model the real journal's released
                # claim after recovery; an escalation continues to quarantine the target.
                return state if state['phase'] not in ('blocked', 'recovered') else None
            f.journal.active = active
            assert (await worker.tick())['phase'] == 'verifying'
            if not telemetry_available:
                async def pending(**kwargs):
                    return {'status': 'pending'}
                f.recovery.verify = pending
            f.time[0] += timedelta(seconds=240 if telemetry_available else 600)
            outcome = 'recovered' if telemetry_available else 'escalated'
            assert (await worker.tick())['phase'] == outcome
            again = await worker.tick()
            if telemetry_available:
                assert again == {'status': 'IDLE'}
            else:
                assert again['phase'] == 'escalated'
            assert (await f.executor.execute(f.request))['phase'] == outcome
            assert [request.url.params.get('dryRun') for request in wire.patches] == ['All', None]
            assert wire.mutations == 1  # No retry and no compensating patch after failed verification.
        finally:
            await kube.close()
    asyncio.run(run())
