"""WP1 acceptance (frozen): proposals wait for a human decision, never a countdown.

Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP1 (R1.1-R1.8, A1.1-A1.12).
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from sentinelops.contracts import utc_time
from sentinelops.remediation import approvals
from sentinelops.remediation.approvals import read_slot, record_approval
from sentinelops.remediation.authority import qualify
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.pod_memory import MemoryController
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

from test_remediation_controller import BAD, fixture as controller_fixture
from test_remediation_executor import setup as executor_fixture
import test_pod_memory as oom

V2 = 'runbook-proposal/v2'
POLICY = 'pending-until-decision/v1'


def _approve(c, proposal, now=None, decision='approve'):
    return record_approval(c.store, proposal_id=proposal['proposal_id'], approved_by='oncall-test',
                           decision=decision, confirm_oncall_approval=True, now=now or c.f.clock())


def _root_breaching(c, value=True):
    c.f.authority.value = replace(c.f.authority.value, root_breaching=value)


def test_new_rollback_proposal_is_v2_without_expiry(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        assert proposal['proposal_version'] == V2
        assert proposal['approval_policy'] == POLICY
        assert 'expires_at' not in proposal
        assert approvals.find_proposal(c.store, proposal['proposal_id']) == proposal
    asyncio.run(run())


def test_v2_approval_two_hours_later_dispatches_once(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        c.f.time[0] += timedelta(hours=2)
        _root_breaching(c)
        _approve(c, proposal)
        result = (await c.controller.tick())[-1]
        assert result['phase'] == 'verifying'
        assert c.f.kube.calls == [True, False]
        assert read_slot(c.store, 'outcome', proposal['proposal_id']) is None
        await c.controller.tick()
        assert c.f.kube.calls.count(False) == 1
    asyncio.run(run())


def test_v2_pending_becomes_obsolete_when_incident_recovers(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        c.f.time[0] += timedelta(minutes=30)
        c.snapshot['revision'] += 1
        await c.controller.tick()
        outcome = read_slot(c.store, 'outcome', proposal['proposal_id'])
        assert outcome['phase'] == 'obsolete' and outcome['reason'] == 'incident-changed'
        assert c.f.kube.calls == []
        with pytest.raises(ValueError):
            _approve(c, proposal)
        await c.controller.tick()
        assert read_slot(c.store, 'outcome', proposal['proposal_id']) == outcome
    asyncio.run(run())


def test_v2_pending_becomes_obsolete_after_newer_deploy(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        original = c.f.kube.observe
        async def observe():
            value = await original()
            return replace(value, image=BAD.split('@')[0] + '@sha256:' + 'f' * 64)
        monkeypatch.setattr(c.f.kube, 'observe', observe)
        await c.controller.tick()
        outcome = read_slot(c.store, 'outcome', proposal['proposal_id'])
        assert outcome['phase'] == 'obsolete' and outcome['reason'] == 'deployment-changed'
        assert c.f.kube.calls == []
    asyncio.run(run())


def test_stale_rca_blocked_when_root_service_no_longer_breaching(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        c.f.time[0] += timedelta(hours=2)
        _root_breaching(c, False)
        _approve(c, proposal)
        await c.controller.tick()
        outcome = read_slot(c.store, 'outcome', proposal['proposal_id'])
        assert outcome['phase'] == 'escalated' and outcome['reason'] == 'evidence-stale-or-future'
        assert c.f.kube.calls == []
    asyncio.run(run())


def _qualify_inputs():
    f = executor_fixture()
    good = asyncio.run(f.executor.registry.resolve())
    return f, good


@pytest.mark.parametrize('problem', [None, 'not-breaching', 'not-rca-ready', 'health-stale', 'deployment-stale'])
def test_stale_rca_allowed_only_with_fresh_health_and_deployment(problem):
    f, good = _qualify_inputs()
    f.time[0] += timedelta(hours=2)
    now = f.clock()
    assessment = replace(f.authority.value, latest_health_at=timestamp(now), root_breaching=problem != 'not-breaching')
    if problem == 'not-rca-ready':
        assessment = replace(assessment, lifecycle='VERIFYING')
    if problem == 'health-stale':
        assessment = replace(assessment, latest_health_at=timestamp(now - timedelta(seconds=901)))
    before = asyncio.run(f.kube.observe())
    if problem == 'deployment-stale':
        before = replace(before, observed_at=timestamp(now - timedelta(seconds=901)))
    args = (assessment, f.request, before, good, f.executor.policy, f.executor.rca_policy)
    if problem is None:
        qualify(*args, now=now)
    else:
        with pytest.raises(Blocked):
            qualify(*args, now=now)
    if problem == 'not-breaching':
        with pytest.raises(Blocked, match='evidence-stale-or-future'):
            qualify(*args, now=now)


def test_fresh_rca_does_not_need_root_breaching():
    f, good = _qualify_inputs()
    now = f.clock()
    assessment = replace(f.authority.value, latest_health_at=timestamp(now))
    assert assessment.root_breaching is False
    qualify(assessment, f.request, asyncio.run(f.kube.observe()), good, f.executor.policy,
            f.executor.rca_policy, now=now)


def test_v2_second_approval_never_dispatches_twice(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        _approve(c, proposal)
        assert (await c.controller.tick())[-1]['phase'] == 'verifying'
        with pytest.raises(ValueError):
            _approve(c, proposal)
        await c.controller.tick()
        assert c.f.kube.calls == [True, False]
    asyncio.run(run())


def _v1(proposal):
    legacy = {k: v for k, v in proposal.items() if k != 'approval_policy'}
    legacy['proposal_version'] = approvals.PROPOSAL_VERSION
    legacy['expires_at'] = timestamp(utc_time(proposal['created_at']) + timedelta(minutes=15))
    return legacy


def test_v1_proposal_still_expires(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        legacy = _v1((await c.controller.tick())[0])
        c.f.time[0] += timedelta(minutes=16)
        with pytest.raises(Blocked, match='approval-expired'):
            c.controller._approval(legacy)
    asyncio.run(run())


def test_api_v2_status_never_expired(tmp_path):
    from sentinelops.remediation.proposal_api import ProposalStore
    store = ArtifactStore(tmp_path)
    created = utc_time('2026-01-01T00:00:00Z')
    proposal = {'proposal_version': V2, 'approval_policy': POLICY, 'proposal_id': 'a' * 64,
        'incident_id': 'INC-review', 'incident_revision': 2, 'runbook_id': 'payment-bad-deploy-rollback',
        'catalog_sha256': 'c' * 64, 'executor_runbook': 'payment-rollback-known-good/v1',
        'decision': 'oncall', 'score': .95, 'margin': .4, 'symptoms': {'severity': 'high'},
        'created_at': timestamp(created),
        'current_image': 'registry.invalid/payment@sha256:' + '1' * 64,
        'rollback_image': 'registry.invalid/payment@sha256:' + '2' * 64}
    store.write('manifests', proposal)
    proposals = ProposalStore(tmp_path)
    assert [v['proposal_id'] for v in proposals.proposals()] == [proposal['proposal_id']]
    assert proposals.view(proposal)['status'] == 'pending'
    approvals.create_slot(store, 'outcome', proposal['proposal_id'], {
        'outcome_version': 'runbook-approval-outcome/v1', 'proposal_id': proposal['proposal_id'],
        'incident_id': proposal['incident_id'], 'incident_revision': proposal['incident_revision'],
        'phase': 'obsolete', 'reason': 'incident-changed', 'recorded_at': timestamp(created)})
    view = proposals.view(proposal)
    assert view['status'] == 'obsolete' and view['outcome']['reason'] == 'incident-changed'


def test_record_approval_v2_late_and_early(tmp_path):
    store = ArtifactStore(tmp_path)
    created = utc_time('2026-01-01T00:00:00Z')
    proposal = {'proposal_version': V2, 'approval_policy': POLICY, 'proposal_id': 'b' * 64,
                'incident_id': 'INC-x', 'incident_revision': 1, 'runbook_id': 'r',
                'created_at': timestamp(created)}
    store.write('manifests', proposal)
    with pytest.raises(ValueError):
        record_approval(store, proposal_id='b' * 64, approved_by='oncall', decision='approve',
                        confirm_oncall_approval=True, now=created - timedelta(seconds=1))
    receipt = record_approval(store, proposal_id='b' * 64, approved_by='oncall', decision='approve',
                              confirm_oncall_approval=True, now=created + timedelta(hours=3))
    assert receipt['proposal_sha256'] == stable_id(proposal)


def test_v2_kill_switch_blocks_at_approval(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        _approve(c, proposal)
        c.kwargs['kill_switch'].touch()
        await c.controller.tick()
        outcome = read_slot(c.store, 'outcome', proposal['proposal_id'])
        assert outcome['phase'] == 'escalated' and outcome['reason'] == 'runbook-disabled-or-kill-switch'
        assert c.f.kube.calls == []
    asyncio.run(run())


def test_oom_v2_late_approval_dispatches(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = oom.fixture(tmp_path)
        p = await prepare()
        assert p['proposal_version'] == V2 and 'expires_at' not in p
        now[0] += timedelta(hours=1)
        oom.approve(store, p, now[0])
        state = await c.tick()
        assert state['phase'] == 'verifying' and len(kube.patches) == 1
    asyncio.run(scenario())


def test_oom_v2_pending_becomes_obsolete_when_pod_recovers(tmp_path):
    async def scenario():
        now, target, store, kube, repo, kwargs, c, prepare = oom.fixture(tmp_path)
        p = await prepare()
        kube.observation.update(ready=True, fresh_oom=False)
        now[0] += timedelta(minutes=20)
        state = await c.tick()
        assert state['phase'] == 'obsolete' and state['reason'] == 'pod-recovered'
        assert not kube.patches
        with pytest.raises(ValueError):
            oom.approve(store, p, now[0])
    asyncio.run(scenario())
