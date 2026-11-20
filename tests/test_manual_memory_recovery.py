"""One integrated explicit-manual recovery, without inventing OOM evidence."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import httpx

from sentinelops.api import create_app
from sentinelops.api_settings import Settings
from sentinelops.remediation import approvals
from sentinelops.remediation.pod_memory import (
    AFTER, BEFORE, RUNBOOK, MemoryController, MemoryTarget, memory_patch, project,
    read_state, write_state,
)
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_pod_memory import Repository, inventory


def test_expired_missing_oom_manual_child_api_to_actual_baseline_recovery_once(tmp_path, monkeypatch):
    async def scenario():
        now = [datetime.now(timezone.utc)]
        target = MemoryTarget('cluster', 'namespace', 'deployment', 'registry.invalid/demo@sha256:' + '1' * 64)
        store = ArtifactStore(tmp_path)
        parent = {'proposal_version': approvals.PROPOSAL_VERSION, 'proposal_id': 'a' * 64,
            'executor_runbook': RUNBOOK, 'runbook_id': 'pod-oom-memory-limit',
            'incident_id': 'INC-expired-manual-review', 'incident_revision': 1,
            'created_at': timestamp(now[0] - timedelta(minutes=20)),
            'expires_at': timestamp(now[0] - timedelta(minutes=5)),
            'target': asdict(target), 'current_image': target.image,
            'resource_change': {'memory_before': BEFORE, 'memory_after': AFTER}, 'decision': 'oncall'}
        # The parent deliberately has no cause, failed-pod, generation, template
        # hash or observation ref. Its original terminal history is immutable.
        store.write('manifests', parent)
        write_state(store, {'runbook': RUNBOOK, 'proposal_id': parent['proposal_id'],
            'phase': 'expired', 'dispatches': 0, 'reason': 'approval-expired', 'finalized': True,
            'updated_at': timestamp(now[0] - timedelta(minutes=5))})
        approvals.create_slot(store, 'outcome', parent['proposal_id'],
            {'proposal_id': parent['proposal_id'], 'runbook': RUNBOOK, 'phase': 'expired', 'dispatches': 0})

        class ReviewRepository(Repository):
            async def check_ready(self): pass
            async def get(self, identifier, revision=None):
                if revision is not None:
                    for row in self.history:
                        if row['incident_id'] == identifier and row['revision'] == revision:
                            return deepcopy(row)
                return await super().get(identifier)

        deployment, pods, replica_sets, node, hpas = inventory(target, now[0])
        deployment['metadata'].update(generation=7, resourceVersion='actual-before')
        deployment['status'].update(observedGeneration=7, readyReplicas=1, updatedReplicas=1)
        pods[0]['metadata']['uid'] = 'actual-current-pod'
        pods[0]['status']['containerStatuses'][0].update(ready=True, restartCount=0, lastState={})

        class Kubernetes:
            patches = 0
            dry_runs = 0
            async def observe(self, clock):
                return project(target, deployment, pods, replica_sets, node, hpas, now=clock)
            async def patch(self, observation, *, dry_run):
                changes = memory_patch(target, observation)
                for item in changes:
                    if item['op'] == 'test':
                        actual = deployment
                        for part in item['path'].strip('/').split('/'):
                            actual = actual[int(part)] if isinstance(actual, list) else actual[part]
                        assert actual == item['value']
                assert [p for p in changes if p['op'] != 'test'] == [{
                    'op': 'replace', 'path': '/spec/template/spec/containers/0/resources/limits/memory',
                    'value': AFTER}]
                if dry_run:
                    self.dry_runs += 1
                    return
                self.patches += 1
                deployment['metadata'].update(generation=8, resourceVersion='actual-after')
                deployment['status']['observedGeneration'] = 8
                container = deployment['spec']['template']['spec']['containers'][0]
                container['resources']['limits']['memory'] = AFTER
                pods[0]['spec']['containers'] = [deepcopy(container)]
                pods[0]['metadata']['uid'] = 'actual-ready-replacement'
            async def probe(self): return True

        kube, repo = Kubernetes(), ReviewRepository()
        kwargs = dict(target=target, kube=kube, store=store, incidents=repo,
            kill_switch=tmp_path / 'kill', enabled=True, clock=lambda: now[0])
        controller = MemoryController(**kwargs)
        await controller._incident(parent, 'OPEN')
        await controller._incident(parent, 'ESCALATED', state='escalated')
        old_pointer = (tmp_path / 'pod-memory' / (parent['proposal_id'] + '.json')).read_bytes()
        old_outcome = approvals.read_slot(store, 'outcome', parent['proposal_id'])
        token = 'operator-' + 'x' * 40
        app = create_app(Settings(environment='test', operator_token=token,
            runbook_proposal_store=str(tmp_path)), repository=repo)
        headers = {'Authorization': 'Bearer ' + token, 'If-Match': '"' + stable_id(parent) + '"'}
        path = '/v1/runbook-proposals/' + parent['proposal_id']
        body = {'decision': 'approve', 'reason': 'Restore the pinned RAM cap manually; OOM evidence is incomplete.',
                'approval_mode': approvals.MANUAL_MEMORY_RECOVERY}
        real_record = approvals.record_approval
        interrupted = []
        def interrupted_approval(*args, **kwargs):
            if not interrupted:
                interrupted.append(True)
                raise ValueError('simulated interruption after child identity publication')
            return real_record(*args, **kwargs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            view = (await client.get(path, headers=headers)).json()
            assert view['status'] == 'expired' and view['review_change'] is None
            assert view['recovery_change']['memory_after'] == AFTER and view['manual_recovery_available']
            assert (await client.post(path + '/decision', json=body)).status_code == 401
            assert (await client.post(path + '/decision', headers={**headers, 'If-Match': '"' + '0' * 64 + '"'}, json=body)).status_code == 412
            assert (await client.post(path + '/decision', headers=headers,
                json={**body, 'decision': 'reject'})).status_code == 422
            monkeypatch.setattr(approvals, 'record_approval', interrupted_approval)
            assert (await client.post(path + '/decision', headers=headers, json=body)).status_code == 409
            interrupted_slot = approvals.read_slot(store, 'manual-memory-recovery', parent['proposal_id'])
            assert interrupted_slot and not (await client.get(path, headers=headers)).json()['manual_recovery_available']
            response = await client.post(path + '/decision', headers=headers, json=body)
            assert response.status_code == 201, response.text
            result = response.json(); child = result['proposal']; pid = child['proposal_id']
            assert pid == interrupted_slot['manual_recovery_proposal_id']
            assert response.headers['etag'] == '"' + stable_id(child) + '"'
            assert pid != parent['proposal_id'] and child['incident_id'] == 'INC-OOM-MANUAL-' + pid
            assert result['manual_recovery_of'] == child['manual_recovery_of'] == parent['proposal_id']
            assert child['origin_proposal_sha256'] == stable_id(parent)
            assert child['origin_incident_revision'] == 2 and child['incident_revision'] == 1
            assert child['target'] == parent['target'] and child['resource_change'] == parent['resource_change']
            receipt = approvals.read_slot(store, 'approval', pid)
            assert receipt['approval_mode'] == approvals.MANUAL_MEMORY_RECOVERY
            assert receipt['proposal_sha256'] == stable_id(child)
            assert (await client.post(path + '/decision', headers=headers, json=body)).status_code == 409
            assert not (await client.get(path, headers=headers)).json()['manual_recovery_available']
        assert len([p for p in (tmp_path / 'manifests').glob('*.json')
                    if store.read('artifact://manifests/' + p.name).get('manual_recovery_of') == parent['proposal_id']]) == 1
        before = await kube.observe(now[0])
        assert before['memory'] == BEFORE and before['ready'] and not before['oom'] and not before['fresh_oom']
        now[0] = datetime.now(timezone.utc) + timedelta(seconds=1)
        state = await controller.tick()
        assert state['phase'] == 'verifying' and kube.patches == kube.dry_runs == 1
        assert state['recovery_baseline']['generation'] == 7
        assert state['recovery_baseline']['pod_uid'] == 'actual-current-pod'
        assert state['approval_mode'] == approvals.MANUAL_MEMORY_RECOVERY
        for _ in range(15):
            now[0] += timedelta(seconds=2)
            state = await MemoryController(**kwargs).tick()
        assert state['phase'] == 'recovered' and state['finalized'] and state['dispatches'] == 1
        for kind in ('approval', 'used', 'outcome'):
            assert approvals.read_slot(store, kind, pid)['approval_mode'] == approvals.MANUAL_MEMORY_RECOVERY
        assert (await repo.get(child['incident_id']))['context']['recovery_source'] == 'manual'
        assert all('Kubernetes observed OOMKilled' not in row['payload']['evidence'][0]['fact'] for row in repo.history)
        assert all(row['payload']['root_cause_ranking'] == [] for row in repo.history)
        assert (await MemoryController(**kwargs).tick())['phase'] == 'observing'
        assert kube.patches == 1 and approvals.read_slot(store, 'approval', parent['proposal_id']) is None
        assert approvals.read_slot(store, 'used', parent['proposal_id']) is None
        assert approvals.read_slot(store, 'outcome', parent['proposal_id']) == old_outcome
        assert (tmp_path / 'pod-memory' / (parent['proposal_id'] + '.json')).read_bytes() == old_pointer
    asyncio.run(scenario())
