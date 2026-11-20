"""Guarded OOM remediation: genuine reason, approval, CAS, crash and HTTP proof."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sentinelops.incidents.domain import IncidentNotFound, check_transition
from sentinelops.remediation import approvals
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.pod_memory import (
    AFTER, BEFORE, NAME, NAMESPACE, RUNBOOK, MemoryController, MemoryTarget,
    ObservationPending, memory_patch, project, read_state, review_change,
)
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore


class Repository:
    def __init__(self): self.rows = {}; self.history = []
    async def get(self, identifier):
        if identifier not in self.rows: raise IncidentNotFound(identifier)
        return deepcopy(self.rows[identifier])
    async def write(self, command):
        prior = self.rows.get(command.payload['incident_id'])
        check_transition(prior['context']['lifecycle_stage'] if prior else None, command,
                         prior['payload'] if prior else None)
        assert command.expected_revision == (prior['revision'] if prior else 0)
        result = {'incident_id': command.payload['incident_id'], 'revision': command.expected_revision + 1,
                  'payload': deepcopy(command.payload), 'context': {**command.context, 'lifecycle_stage': command.lifecycle_stage}}
        self.rows[result['incident_id']] = result
        self.history.append(result)
        return deepcopy(result)


def fixture(tmp_path):
    now = [datetime(2026, 11, 20, 12, tzinfo=timezone.utc)]
    target = MemoryTarget('cluster', 'namespace', 'deployment', 'registry.invalid/demo@sha256:' + '1' * 64)
    store = ArtifactStore(tmp_path)
    class Kubernetes:
        def __init__(self):
            self.patches = []; self.http_ok = True; self.failure = None; self.lose_reply = False
            self.observation = {'observed_at': timestamp(now[0]), 'target': asdict(target),
                'resource_version': '10', 'generation': 3, 'observed_generation': 3,
                'template_sha256': 'c' * 64, 'memory': BEFORE, 'pod_uid': 'dead-pod',
                'restart_count': 3, 'oom': True, 'fresh_oom': True, 'ready': False}
        async def observe(self, clock):
            if self.failure: raise self.failure
            return deepcopy(self.observation)
        async def patch(self, observation, *, dry_run):
            assert observation['memory'] == BEFORE
            assert observation['resource_version'] == self.observation['resource_version']
            if not dry_run:
                assert list((store.root / 'runbook-approvals/used').glob('*.json'))
                assert any(read_state(store, p.stem)['phase'] == 'dispatching'
                           for p in (store.root / 'pod-memory').glob('*.json'))
                self.patches.append(memory_patch(target, observation))
                self.observation.update(memory=AFTER, generation=4, observed_generation=4,
                    pod_uid='replacement', restart_count=0, oom=False, fresh_oom=False, ready=True)
                if self.lose_reply: raise httpx.ReadTimeout('lost reply')
        async def probe(self): return self.http_ok
    kube, repo = Kubernetes(), Repository()
    kwargs = dict(target=target, kube=kube, store=store, incidents=repo,
                  kill_switch=tmp_path / 'kill', enabled=True, clock=lambda: now[0])
    controller = MemoryController(**kwargs)
    async def prepare():
        state = await controller.tick()
        proposal = approvals.find_proposal(store, state['proposal_id'])
        return proposal
    return now, target, store, kube, repo, kwargs, controller, prepare


def approve(store, proposal, clock, decision='approve'):
    approvals.record_approval(store, proposal_id=proposal['proposal_id'], approved_by='oncall',
        decision=decision, reason='verified OOM and fixed memory cap', confirm_oncall_approval=True, now=clock)


def test_real_approval_single_patch_durable_audit_restart_and_verified_incident(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare()
        assert review_change(p)['memory_after'] == AFTER
        for _ in range(3): assert (await c.tick())['phase'] == 'pending'
        assert not kube.patches
        approve(store, p, now[0])
        state = await c.tick()
        assert state['phase'] == 'verifying' and len(kube.patches) == 1
        c = MemoryController(**kwargs)
        for _ in range(15):
            now[0] += timedelta(seconds=2)
            state = await c.tick()
        assert state['phase'] == 'recovered' and state['finalized'] and state['dispatches'] == 1
        assert len(kube.patches) == 1
        snapshot = await repo.get(p['incident_id'])
        assert snapshot['context']['lifecycle_stage'] == 'CLOSED'
        assert snapshot['context']['recovery_source'] == 'action'
        assert snapshot['context']['trigger_sources'][0]['source'] == 'kubernetes'
        assert snapshot['payload']['service_signals'] == []
        assert snapshot['payload']['root_cause_ranking'] == []
        assert (await c.tick())['phase'] == 'observing'
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['reject', 'expired', 'kill', 'stale', 'target', 'native_recovery'])
def test_no_patch_without_current_valid_approved_failure(tmp_path, failure):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare()
        approve(store, p, now[0], 'reject' if failure == 'reject' else 'approve')
        if failure == 'expired': now[0] += timedelta(minutes=15)
        if failure == 'kill': kwargs['kill_switch'].touch()
        if failure == 'stale': repo.rows[p['incident_id']]['revision'] += 1
        if failure == 'target': kube.observation['template_sha256'] = 'd' * 64
        if failure == 'native_recovery': kube.observation['ready'] = True
        state = await c.tick()
        assert state['phase'] in ('blocked', 'expired', 'rejected') and not kube.patches
    asyncio.run(scenario())


def test_uncertain_patch_is_reconciled_never_resent(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare(); approve(store, p, now[0]); kube.lose_reply = True
        assert (await c.tick())['phase'] == 'dispatching'
        now[0] += timedelta(seconds=2)
        assert (await MemoryController(**kwargs).tick())['phase'] == 'verifying'
        assert len(kube.patches) == 1
    asyncio.run(scenario())


def test_uncertain_dispatch_that_did_not_apply_is_never_retried(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare(); approve(store, p, now[0])
        attempts = []
        async def disconnected(observation, *, dry_run):
            if not dry_run:
                attempts.append(observation)
                raise httpx.ReadTimeout('unknown server outcome')
        kube.patch = disconnected
        assert (await c.tick())['phase'] == 'dispatching'
        restarted = MemoryController(**kwargs)
        assert (await restarted.tick())['phase'] == 'escalated'
        await restarted.tick()
        assert len(attempts) == 1 and kube.observation['memory'] == BEFORE
    asyncio.run(scenario())


def test_resource_version_refreshed_after_durable_incident_transitions(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare(); approve(store, p, now[0])
        original_write = repo.write
        async def status_changes(command):
            result = await original_write(command)
            if command.lifecycle_stage == 'MITIGATING':
                kube.observation['resource_version'] = '11'
            return result
        repo.write = status_changes
        result = await c.tick()
        assert result['phase'] == 'verifying' and len(kube.patches) == 1
        assert result['before']['resource_version'] == '11'
        assert {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '11'} in kube.patches[0]
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['template', 'pod', 'generation', 'ready', 'expired', 'kill', 'stale'])
def test_changes_during_incident_writes_block_the_actual_patch(tmp_path, failure):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare(); approve(store, p, now[0])
        original_write = repo.write
        async def changed(command):
            result = await original_write(command)
            if command.lifecycle_stage == 'MITIGATING':
                if failure == 'template': kube.observation['template_sha256'] = 'd' * 64
                if failure == 'pod': kube.observation['pod_uid'] = 'different'
                if failure == 'generation': kube.observation['generation'] += 1
                if failure == 'ready': kube.observation['ready'] = True
                if failure == 'expired': now[0] += timedelta(minutes=15)
                if failure == 'kill': kwargs['kill_switch'].touch()
                if failure == 'stale': repo.rows[p['incident_id']]['revision'] += 1
            return result
        repo.write = changed
        state = await c.tick()
        assert state['phase'] == 'blocked' and state['dispatches'] == 0
        assert approvals.read_slot(store, 'used', p['proposal_id']) is None
        assert not kube.patches
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['RECOVERED', 'CLOSED'])
@pytest.mark.parametrize('committed', [False, True])
def test_terminal_database_failure_resumes_without_reopening_or_repatch(tmp_path, stage, committed):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare(); approve(store, p, now[0]); await c.tick()
        original_write, failed = repo.write, []
        async def unavailable(command):
            if command.lifecycle_stage == stage and not failed:
                failed.append(True)
                if committed: await original_write(command)
                raise ValueError('database response unavailable')
            return await original_write(command)
        repo.write = unavailable
        for _ in range(14):
            now[0] += timedelta(seconds=2); await c.tick()
        now[0] += timedelta(seconds=2)
        with pytest.raises(ValueError): await c.tick()
        state = read_state(store, p['proposal_id'])
        assert state['phase'] == 'recovered' and not state.get('finalized')
        state = await MemoryController(**kwargs).tick()
        assert state['finalized'] and state['phase'] == 'recovered'
        assert (await repo.get(p['incident_id']))['context']['lifecycle_stage'] == 'CLOSED'
        assert len(kube.patches) == 1
    asyncio.run(scenario())


def test_restart_after_closed_before_finalized_pointer_save(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare(); approve(store, p, now[0]); await c.tick()
        original_save = c._save
        def crash(state, **changes):
            if changes.get('finalized'): raise OSError('stopped before final pointer write')
            return original_save(state, **changes)
        c._save = crash
        for _ in range(14):
            now[0] += timedelta(seconds=2); await c.tick()
        now[0] += timedelta(seconds=2)
        with pytest.raises(OSError): await c.tick()
        assert (await repo.get(p['incident_id']))['context']['lifecycle_stage'] == 'CLOSED'
        state = await MemoryController(**kwargs).tick()
        assert state['phase'] == 'recovered' and state['finalized'] and len(kube.patches) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('committed', [False, True])
def test_interrupted_initial_incident_write_keeps_one_proposal_identity(tmp_path, committed):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        original_write, failed = repo.write, []
        async def unavailable(command):
            if not failed:
                failed.append(True)
                if committed: await original_write(command)
                raise ValueError('initial database response unavailable')
            return await original_write(command)
        repo.write = unavailable
        with pytest.raises(ValueError): await c.tick()
        now[0] += timedelta(seconds=2)
        state = await MemoryController(**kwargs).tick()
        assert state['phase'] == 'pending'
        assert list(repo.rows) == ['INC-OOM-' + state['proposal_id']]
        assert len([row for row in repo.history if row['context']['lifecycle_stage'] == 'OPEN']) == 1
        assert not kube.patches
    asyncio.run(scenario())


def test_rollout_gap_is_pending_and_outage_or_probe_failure_resets_verification(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = fixture(tmp_path)
        p = await prepare(); approve(store, p, now[0]); await c.tick()
        for _ in range(10): now[0] += timedelta(seconds=2); await c.tick()
        kube.failure = ObservationPending('replacement not scheduled')
        now[0] += timedelta(seconds=2)
        assert (await c.tick())['healthy_since'] is None
        kube.failure = None; now[0] += timedelta(seconds=2)
        assert (await c.tick())['successful_probes'] == 1
        kube.http_ok = False; now[0] += timedelta(seconds=2)
        assert (await c.tick())['successful_probes'] == 0
        kube.http_ok = True; now[0] += timedelta(seconds=40)
        state = await c.tick()
        assert state['phase'] == 'verifying' and state['successful_probes'] == 1
        assert len(kube.patches) == 1
    asyncio.run(scenario())


def inventory(target, now):
    container = {'name': 'oom-demo', 'image': target.image, 'resources': {
        'requests': {'cpu': '25m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': BEFORE}}}
    deployment = {'metadata': {'name': NAME, 'namespace': NAMESPACE, 'uid': target.deployment_uid,
        'generation': 3, 'resourceVersion': '10'}, 'spec': {'replicas': 1,
        'selector': {'matchLabels': {'app': NAME}},
        'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}},
        'template': {'metadata': {'labels': {'app': NAME, 'sentinelops.io/demo': 'oom'}},
                     'spec': {'containers': [container]}}}, 'status': {'observedGeneration': 3}}
    pods = [{'metadata': {'uid': 'pod', 'ownerReferences': [{'kind': 'ReplicaSet', 'uid': 'rs', 'controller': True}]},
        'spec': {'nodeName': 'node', 'containers': [deepcopy(container)]}, 'status': {'phase': 'Running',
            'containerStatuses': [{'name': 'oom-demo', 'restartCount': 3, 'ready': False,
                'lastState': {'terminated': {'reason': 'OOMKilled', 'exitCode': 137, 'finishedAt': timestamp(now)}}}]}}]
    rs = [{'metadata': {'uid': 'rs', 'ownerReferences': [{'kind': 'Deployment', 'uid': target.deployment_uid, 'controller': True}]}}]
    node = {'metadata': {'name': 'node'}, 'status': {'conditions': [
        {'type': 'Ready', 'status': 'True'}, {'type': 'MemoryPressure', 'status': 'False'}]}}
    return deployment, pods, rs, node, []


@pytest.mark.parametrize('failure', ['signal137', 'stale', 'future', 'pressure', 'owner', 'image', 'replicas', 'hpa'])
def test_oom_and_scope_require_actual_kubernetes_facts(tmp_path, failure):
    now, target, *_ = fixture(tmp_path)
    d, pods, rs, node, hpas = inventory(target, now[0])
    t = pods[0]['status']['containerStatuses'][0]['lastState']['terminated']
    if failure == 'signal137': t['reason'] = 'Error'
    if failure == 'stale': t['finishedAt'] = timestamp(now[0] - timedelta(seconds=181))
    if failure == 'future': t['finishedAt'] = timestamp(now[0] + timedelta(seconds=1))
    if failure == 'pressure': node['status']['conditions'][1]['status'] = 'True'
    if failure == 'owner': rs[0]['metadata']['ownerReferences'][0]['uid'] = 'other'
    if failure == 'image': d['spec']['template']['spec']['containers'][0]['image'] = 'different'
    if failure == 'replicas': d['spec']['replicas'] = 2
    if failure == 'hpa': hpas.append({'spec': {'scaleTargetRef': {'kind': 'Deployment', 'name': NAME}}})
    if failure in ('signal137', 'stale', 'future'):
        assert not project(target, d, pods, rs, node, hpas, now=now[0])['fresh_oom']
    else:
        with pytest.raises(Blocked): project(target, d, pods, rs, node, hpas, now=now[0])


def test_patch_only_replaces_memory_with_uid_version_image_and_resource_preconditions(tmp_path):
    now, target, *_ = fixture(tmp_path)
    result = project(target, *inventory(target, now[0]), now=now[0])
    patch = memory_patch(target, result)
    assert [p for p in patch if p['op'] != 'test'] == [{
        'op': 'replace', 'path': '/spec/template/spec/containers/0/resources/limits/memory', 'value': AFTER}]
    assert {p['path'] for p in patch if p['op'] == 'test'} >= {
        '/metadata/uid', '/metadata/resourceVersion', '/metadata/generation', '/spec/replicas'}


def test_review_rejects_a_mutable_image_without_raising(tmp_path):
    async def scenario():
        *_, prepare = fixture(tmp_path)
        proposal = await prepare()
        proposal['target']['image'] = 'registry.invalid/demo:latest'
        assert review_change(proposal) is None
    asyncio.run(scenario())
