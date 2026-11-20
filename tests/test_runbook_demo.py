"""Offline orchestration checks; no cloud, kubectl or executor bypass."""
import asyncio
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from scripts import run_runbook_demo as demo
from sentinelops.remediation.domain import Target
from sentinelops.telemetry.core import stable_id

TARGET = Target('cluster', 'sentinelops-b-actions', 'namespace', 'payment-service', 'deployment', 'payment')
GOOD = 'registry.invalid/sentinelops/application@sha256:' + 'a' * 64
# Same repository as GOOD: the executor only rolls back to a distinct same-repository digest.
BAD = 'registry.invalid/sentinelops/application@sha256:' + 'b' * 64


class Clock:
    seconds = 0.

    def __init__(self):
        self.sleeps = []

    def __call__(self):
        return self.seconds

    def now(self):
        return datetime(2026, 11, 18, tzinfo=timezone.utc) + timedelta(seconds=self.seconds)

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.seconds += seconds


class Backend:
    regression_image = BAD

    def __init__(self, *, kill=False):
        self.round, self.reads, self.dispatch_count = 0, 0, 0
        self.kill, self.mutated = kill, False
        self.events, self.deployed = [], []
        self.change = lambda card: card
        self.state_change = lambda state: state
        self.fresh = True
        self.baseline_read = True

    async def known_good(self, instant):
        self.events.append('known-good')
        if self.kill:
            self.round += 1
            self.baseline_read = True
        return SimpleNamespace(image=GOOD, ledger_ref='artifact://manifests/' + 'a' * 64 + '.json')

    async def deployment(self):
        return SimpleNamespace(image=GOOD, target=TARGET, resource_version='changed' if self.mutated else '1',
            generation=self.round * 2 + 1, ready_for=lambda *a, **kw: True)

    async def deploy(self, before):
        self.events.append('deploy')
        self.round += 1
        self.deployed.append(self.round)
        return {'status': 'CHANGE_OBSERVED', 'change_ref': f'artifact://manifests/{self.round:064x}.json'}

    async def decisions(self):
        self.reads += 1
        if self.kill and self.baseline_read:
            self.baseline_read = False
            return []
        if not self.round and (not self.kill or self.reads == 1):
            return []
        number = max(1, self.round)
        card = {'decision_version': 'runbook-decision/v1', 'decision': 'oncall',
            'proposal_version': 'runbook-proposal/v1', 'proposal_id': stable_id(['proposal', number]),
            'created_at': '2026-11-18T00:00:00Z', 'expires_at': '2026-11-18T01:00:00Z',
            'controller_version': 'auto-runbook-controller/v1',
            'runbook_id': demo.RUNBOOK_ID, 'executor_runbook': 'payment-rollback-known-good/v1',
            'score': 1., 'margin': 1., 'symptoms': {'severity': 'low'},
            'incident_id': f'incident-{number}', 'incident_revision': 1, 'reasons': []}
        if self.kill:
            card.update(decision='engineer', reasons=['runbook-disabled-or-kill-switch'])
        ref = self.store.write('manifests', card)
        return [(ref, self.change(card))]

    async def incident_after(self, card, started):
        return self.fresh

    async def action(self, decision):
        self.dispatch_count = self.round
        state = {'action_id': f'action-{self.round}', 'revision': 4, 'phase': 'recovered',
            'last_event_hash': 'c' * 64,
            'intent': {'request': {'incident_id': decision['incident_id'], 'incident_revision': 1},
                'policy': {'target': asdict(TARGET)},
                'evidence': {'before': {'image': BAD}, 'known_good': {'image': GOOD}}},
            'facts': {'telemetry': {'status': 'telemetry_verified'}}}
        return self.state_change(state)

    async def dispatches(self):
        return self.dispatch_count

    def kill_active(self):
        return self.kill


def execute(tmp_path, backend=None, **kwargs):
    backend = backend or Backend()
    backend.store = demo.ArtifactStore(tmp_path / 'store')
    clock = Clock()
    result = asyncio.run(demo.run_demo(backend, output=tmp_path, timeout=20., poll=10.,
        clock=clock, sleep=clock.sleep, now=clock.now, **kwargs))
    return result, backend, clock


def test_three_rounds_require_ledger_before_deploy_and_observed_recovery(tmp_path):
    receipts, backend, clock = execute(tmp_path)
    assert backend.events == ['known-good', 'deploy'] * 3
    assert backend.deployed == [1, 2, 3] and clock.sleeps == [600, 600]
    assert len(receipts) == 3
    for number, receipt in enumerate(receipts, 1):
        assert json.loads((tmp_path / f'{number}.json').read_text()) == receipt
        assert receipt['phase'] == 'recovered'
        assert receipt['telemetry']['status'] == 'telemetry_verified'
        assert receipt['dispatches_after'] - receipt['dispatches_before'] == 1
        assert receipt['demo_mode'] and not receipt['G6_accepted'] and not receipt['q09_eligible']


@pytest.mark.parametrize('change', [
    {'score': .79}, {'margin': .14}, {'score': float('nan')}, {'score': float('inf')},
    {'score': True}, {'decision': 'auto'}, {'runbook_id': 'manual'},
    {'executor_runbook': 'other'}, {'symptoms': {'severity': 'unknown'}}, {'symptoms': None},
    {'incident_revision': '1'}, {'incident_id': None}])
def test_unqualified_decisions_never_produce_success_receipts(tmp_path, change):
    backend = Backend()
    backend.change = lambda card: {**card, **change}
    with pytest.raises(TimeoutError):
        execute(tmp_path, backend)
    assert not list(tmp_path.glob('*.json'))


def test_stale_incident_cannot_satisfy_new_round(tmp_path):
    backend = Backend(); backend.fresh = False
    with pytest.raises(TimeoutError):
        execute(tmp_path, backend)


@pytest.mark.parametrize('failure', ['telemetry', 'incident', 'revision', 'image', 'known-good', 'target', 'blocked'])
def test_recovered_phase_alone_is_insufficient(tmp_path, failure):
    backend = Backend()
    def corrupt(state):
        if failure == 'telemetry':
            state['facts']['telemetry']['status'] = 'inconclusive'
        elif failure == 'incident':
            state['intent']['request']['incident_id'] = 'old-incident'
        elif failure == 'revision':
            state['intent']['request']['incident_revision'] = 2
        elif failure == 'image':
            state['intent']['evidence']['before']['image'] = GOOD
        elif failure == 'known-good':
            state['intent']['evidence']['known_good']['image'] = BAD
        elif failure == 'target':
            state['intent']['policy']['target']['deployment_uid'] = 'different'
        else:
            state['phase'] = 'blocked'
        return state
    backend.state_change = corrupt
    with pytest.raises(ValueError):
        execute(tmp_path, backend)
    assert not list(tmp_path.glob('*.json'))


def test_missing_action_times_out(tmp_path):
    backend = Backend(); backend.state_change = lambda state: None
    with pytest.raises(TimeoutError):
        execute(tmp_path, backend)


def test_existing_receipt_refused_before_mutation(tmp_path):
    (tmp_path / '2.json').write_text('existing')
    backend = Backend()
    with pytest.raises(FileExistsError):
        execute(tmp_path, backend)
    assert not backend.events


@pytest.mark.parametrize('cooldown', [0, 599, float('nan'), float('inf')])
def test_cooldown_cannot_be_shortened(tmp_path, cooldown):
    backend = Backend()
    with pytest.raises(ValueError):
        execute(tmp_path, backend, cooldown=cooldown)
    assert not backend.events


def test_kill_switch_observes_full_window_with_zero_deploy_or_dispatch(tmp_path):
    receipts, backend, clock = execute(tmp_path, Backend(kill=True), kill_switch_run=True)
    assert len(receipts) == 3
    for receipt in receipts:
        assert receipt['mutation_count'] == 0
        assert receipt['dispatches_after'] == receipt['dispatches_before'] == 0
        assert receipt['resource_version_before'] == receipt['resource_version_after']
    assert not backend.deployed and clock.seconds >= 1260


def test_kill_switch_run_requires_actual_shared_switch(tmp_path):
    backend = Backend()
    with pytest.raises(ValueError, match='already be active'):
        execute(tmp_path, backend, kill_switch_run=True)
    assert not backend.deployed


@pytest.mark.parametrize('failure', ['dispatch', 'resource-version', 'auto'])
def test_kill_switch_cannot_claim_zero_mutation_when_evidence_changes(tmp_path, failure):
    backend = Backend(kill=True)
    original = backend.decisions
    async def changed():
        result = await original()
        if backend.reads > 1:
            if failure == 'dispatch':
                backend.dispatch_count += 1
            elif failure == 'resource-version':
                backend.mutated = True
            else:
                result[0][1]['decision'] = 'auto'
        return result
    backend.decisions = changed
    with pytest.raises(ValueError):
        execute(tmp_path, backend, kill_switch_run=True)
    assert not backend.deployed and not list(tmp_path.glob('*.json'))


def test_backend_decision_store_verifies_hashes(tmp_path):
    store = demo.ArtifactStore(tmp_path)
    expected = {'decision_version': 'runbook-decision/v1', 'decision': 'engineer'}
    ref = store.write('manifests', expected)
    store.write('manifests', {'unrelated': True})
    args = SimpleNamespace(store=tmp_path, regression_image=BAD)
    backend = demo.Backend(args, None, None, None)
    assert asyncio.run(backend.decisions()) == [(ref, expected)]
    (tmp_path / 'manifests' / ref.rsplit('/', 1)[1]).write_text('{}')
    with pytest.raises(ValueError, match='checksum mismatch'):
        asyncio.run(backend.decisions())


def test_default_demo_prints_pod_approval_command_without_writing_approvals(tmp_path, capsys):
    _, backend, _ = execute(tmp_path)
    output = capsys.readouterr().out
    assert output.count('On-call approval required:') == 3
    assert 'exec deploy/remediation-controller -- python scripts/approve_runbook.py' in output
    assert '--store /artifacts/remediation' in output and '--confirm-oncall-approval' in output
    assert not (backend.store.root / 'runbook-approvals').exists()


def test_explicit_approve_as_records_rehearsal_identity(tmp_path):
    _, backend, _ = execute(tmp_path, approve_as='named-oncall')
    from sentinelops.remediation.approvals import read_slot
    for number in range(1, 4):
        receipt = read_slot(backend.store, 'approval', stable_id(['proposal', number]))
        assert receipt['approved_by'] == 'named-oncall' and receipt['decision'] == 'approve'


@pytest.mark.parametrize('name', ['', ' \t'])
def test_empty_approve_as_fails_before_regression(tmp_path, name):
    backend = Backend()
    with pytest.raises(ValueError): execute(tmp_path, backend, approve_as=name)
    assert backend.deployed == []


@pytest.mark.parametrize('image', [GOOD, 'registry.invalid/sentinelops/payment-regression@sha256:' + 'b' * 64])
def test_unrecoverable_regression_image_fails_before_deploy(tmp_path, image):
    backend = Backend()
    backend.regression_image = image
    with pytest.raises(ValueError, match='distinct digest in the known-good image repository'):
        execute(tmp_path, backend)
    assert backend.deployed == []


@pytest.mark.parametrize('severity', ['low', 'medium', 'high', 'critical'])
def test_operator_flow_accepts_every_severity(tmp_path, severity):
    backend = Backend()
    backend.change = lambda card: {**card, 'symptoms': {'severity': severity}}
    receipts, _, _ = execute(tmp_path, backend)
    assert [r['phase'] for r in receipts] == ['recovered'] * 3


class FailedVerifyBackend(Backend):
    def __init__(self):
        super().__init__()
        self.action_reads = 0
        self.fault_active = False
        self.terminal = 'escalated'
        self.reason = 'verification-timeout-target-quarantined'

    async def action(self, decision):
        state = await super().action(decision)
        self.action_reads += 1
        phase = 'verifying' if self.action_reads == 1 else self.terminal
        state.update(phase=phase)
        state['facts'] = {'reason': self.reason, 'telemetry': {'status': 'pending'}}
        if phase == 'recovered':
            state['facts']['telemetry']['status'] = 'telemetry_verified'
        self.events.append(phase)
        return self.state_change(state)

    async def start_verify_fault(self, action):
        assert action['phase'] == 'verifying' and self.dispatch_count == 1
        self.fault_active = True
        self.events.append('fault-on')
        return {'pod_uid': 'replacement', 'mode': 'error'}

    async def hold_verify_fault(self, fault):
        assert self.fault_active and fault['pod_uid'] == 'replacement'

    async def clear_verify_fault(self):
        self.events.append('fault-off')
        self.fault_active = False
        return {'pod_uid': 'replacement', 'mode': 'healthy', 'delay_ms': 0}


def test_failed_verify_escalates_after_dispatch_and_cleans_after_verdict(tmp_path):
    backend = FailedVerifyBackend()
    receipts, _, _ = execute(tmp_path, backend, failed_verify_run=True)
    assert backend.events == ['known-good', 'deploy', 'verifying', 'fault-on', 'escalated', 'fault-off']
    assert not backend.fault_active and backend.deployed == [1]
    receipt, = receipts
    assert receipt == json.loads((tmp_path / '1.json').read_text())
    assert receipt['phase'] == 'escalated' and receipt['constraint'] == 'C-R08'
    assert receipt['telemetry']['status'] != 'telemetry_verified'
    assert receipt['action_id'] == 'action-1' and receipt['last_event_hash'] == 'c' * 64
    assert receipt['dispatches_after'] - receipt['dispatches_before'] == 1
    assert receipt['fault']['pod_uid'] == receipt['fault_cleanup']['pod_uid']
    assert receipt['fault']['injected_at'] <= receipt['verdict_observed_at'] <= receipt['fault_cleared_at']


@pytest.mark.parametrize('phase,reason', [
    ('recovered', 'verification-timeout-target-quarantined'),
    ('blocked', 'verification-timeout-target-quarantined'),
    ('escalated', 'kubernetes-rbac-denied'), ('verifying', 'pending')])
def test_failed_verify_never_accepts_recovery_guard_failure_or_timeout(tmp_path, phase, reason):
    backend = FailedVerifyBackend()
    backend.terminal, backend.reason = phase, reason
    with pytest.raises((ValueError, TimeoutError)):
        execute(tmp_path, backend, failed_verify_run=True)
    assert not backend.fault_active and backend.events[-1] == 'fault-off'
    assert not list(tmp_path.glob('*.json'))


def test_failed_verify_without_observed_fault_cannot_pass(tmp_path):
    backend = FailedVerifyBackend()
    async def not_ready(action):
        return None
    backend.start_verify_fault = not_ready
    with pytest.raises(ValueError, match='C-R08'):
        execute(tmp_path, backend, failed_verify_run=True)


def test_failed_verify_cleanup_failure_prevents_success_receipt(tmp_path):
    backend = FailedVerifyBackend()
    async def cleanup_failed():
        raise ValueError('cleanup failed')
    backend.clear_verify_fault = cleanup_failed
    with pytest.raises(ValueError, match='cleanup failed'):
        execute(tmp_path, backend, failed_verify_run=True)
    assert not list(tmp_path.glob('*.json'))


@pytest.mark.parametrize('mode', ['reject_run', 'failed_verify_run'])
def test_negative_modes_cannot_be_combined_before_mutation(tmp_path, mode):
    backend = Backend()
    with pytest.raises(ValueError, match='mutually exclusive'):
        execute(tmp_path, backend, kill_switch_run=True, **{mode: True})
    assert not backend.events


class RejectBackend(Backend):
    async def action(self, decision):
        return None

    async def rejection(self, decision):
        from sentinelops.remediation.approvals import create_slot, read_slot
        pid = decision['proposal_id']
        if read_slot(self.store, 'approval', pid) is None:
            demo.record_approval(self.store, proposal_id=pid, approved_by='external-oncall',
                decision='reject', confirm_oncall_approval=True, now=Clock().now())
        if read_slot(self.store, 'outcome', pid) is None:
            create_slot(self.store, 'outcome', pid, {
                'outcome_version': 'runbook-approval-outcome/v1', 'proposal_id': pid,
                'incident_id': decision['incident_id'], 'incident_revision': decision['incident_revision'],
                'phase': 'escalated', 'reason': 'approval-rejected'})
        return await demo.Backend.rejection(self, decision)


@pytest.mark.parametrize('actor', [None, 'named-oncall'])
def test_rejection_observes_controller_outcome_with_zero_rollback(tmp_path, capsys, actor):
    receipts, backend, clock = execute(tmp_path, RejectBackend(), reject_run=True, approve_as=actor)
    receipt, = receipts
    assert receipt['phase'] == 'engineer' and receipt['reason'] == 'approval-rejected'
    assert receipt['dispatches_before'] == receipt['dispatches_after'] == receipt['mutation_count'] == 0
    assert receipt['rejection']['approval']['decision'] == 'reject'
    assert receipt['rejection']['outcome']['phase'] == 'escalated'
    assert receipt['rejection']['approval']['approved_by'] == (actor or 'external-oncall')
    assert clock.seconds == 20 and backend.deployed == [1]
    assert '--decision reject' in capsys.readouterr().out


@pytest.mark.parametrize('failure', ['late-dispatch', 'action', 'image', 'generation', 'no-outcome', 'used', 'binding'])
def test_rejection_cannot_pass_without_bound_outcome_or_when_action_changes(tmp_path, failure):
    backend = RejectBackend()
    original = backend.rejection
    async def rejection(decision):
        result = await original(decision)
        if failure == 'late-dispatch':
            backend.dispatch_count += 1
        elif failure == 'action':
            backend.action = lambda card: asyncio.sleep(0, result={'phase': 'prepared'})
        elif failure in ('image', 'generation'):
            snapshot = await backend.deployment()
            setattr(snapshot, failure, BAD if failure == 'image' else 99)
            backend.deployment = lambda: asyncio.sleep(0, result=snapshot)
        elif failure == 'no-outcome':
            return None
        elif failure == 'used':
            from sentinelops.remediation.approvals import create_slot, read_slot
            if read_slot(backend.store, 'used', decision['proposal_id']) is None:
                create_slot(backend.store, 'used', decision['proposal_id'], {'used': True})
        elif failure == 'binding':
            return await demo.Backend.rejection(backend, {**decision, 'incident_revision': 99})
        return result
    backend.rejection = rejection
    with pytest.raises((ValueError, TimeoutError)):
        execute(tmp_path, backend, reject_run=True)
    assert not list(tmp_path.glob('*.json'))


class FaultKube:
    def __init__(self):
        self.mode, self.calls = 'healthy', []
        self.uncertain = False
        self.inventory = {'items': [{'metadata': {'name': 'payment-new', 'uid': 'replacement'}}]}

    async def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith('/pods'):
            return self.inventory
        if method == 'POST':
            self.mode = kwargs['json']['mode']
            if self.uncertain and self.mode == 'error':
                raise OSError('lost POST response')
        return {'service': 'payment-service', 'mode': self.mode, 'delay_ms': 0}


def fault_backend(tmp_path):
    kube = FaultKube()
    backend = demo.Backend(SimpleNamespace(store=tmp_path, regression_image=BAD), None, None, None,
                           fault_kube=kube)
    current = SimpleNamespace(image=GOOD, generation=3, target=TARGET,
        pods=[SimpleNamespace(uid='replacement')], ready_for=lambda *a, **kw: True)
    backend.deployment = lambda **kw: asyncio.sleep(0, result=current)
    state = {'phase': 'verifying', 'intent': {'evidence': {
        'before': {'generation': 2, 'pods': [{'uid': 'regression'}]}, 'known_good': {'image': GOOD}}},
        'facts': {'patch_receipt': {'image': GOOD, 'generation': 3}}}
    return backend, kube, current, state


def test_fault_posts_to_new_pod_proxy_then_checks_and_cleans(tmp_path):
    backend, kube, current, state = fault_backend(tmp_path)
    async def run():
        fault = await backend.start_verify_fault(state)
        await backend.hold_verify_fault(fault)
        return fault, await backend.clear_verify_fault()
    fault, cleanup = asyncio.run(run())
    assert fault['pod_uid'] == cleanup['pod_uid'] == 'replacement'
    posts = [(path, kw['json']) for method, path, kw in kube.calls if method == 'POST']
    path = '/api/v1/namespaces/sentinelops-b-actions/pods/payment-new:8000/proxy/internal/faults'
    assert posts == [(path, {'mode': 'error', 'delay_ms': 0}), (path, {'mode': 'healthy', 'delay_ms': 0})]
    assert kube.mode == 'healthy' and backend.pending_fault is None


@pytest.mark.parametrize('failure', ['not-verifying', 'no-patch', 'wrong-patch', 'not-ready',
    'generation', 'pod-missing', 'pagination', 'existing-fault'])
def test_fault_never_posts_before_sent_rollback_and_ready_new_pod(tmp_path, failure):
    backend, kube, current, state = fault_backend(tmp_path)
    if failure == 'not-verifying': state['phase'] = 'prepared'
    elif failure == 'no-patch': state['facts'] = {}
    elif failure == 'wrong-patch': state['facts']['patch_receipt']['image'] = BAD
    elif failure == 'not-ready': current.ready_for = lambda *a, **kw: False
    elif failure == 'generation': current.generation = 4
    elif failure == 'pod-missing': kube.inventory['items'] = []
    elif failure == 'pagination': kube.inventory['metadata'] = {'continue': 'more'}
    elif failure == 'existing-fault': kube.mode = 'latency'
    if failure == 'not-ready':
        assert asyncio.run(backend.start_verify_fault(state)) is None
    else:
        with pytest.raises(ValueError): asyncio.run(backend.start_verify_fault(state))
    assert all(method != 'POST' for method, _, _ in kube.calls)


def test_uncertain_fault_post_still_cleans_up(tmp_path):
    backend, kube, _, state = fault_backend(tmp_path)
    kube.uncertain = True
    async def run():
        try:
            await backend.start_verify_fault(state)
        finally:
            await backend.clear_verify_fault()
    with pytest.raises(OSError): asyncio.run(run())
    assert kube.mode == 'healthy' and backend.pending_fault is None


@pytest.mark.parametrize('failure', ['pod', 'mode'])
def test_fault_cannot_disappear_before_verdict(tmp_path, failure):
    backend, kube, current, state = fault_backend(tmp_path)
    async def run():
        fault = await backend.start_verify_fault(state)
        if failure == 'pod': current.pods = [SimpleNamespace(uid='different')]
        else: kube.mode = 'healthy'
        try:
            await backend.hold_verify_fault(fault)
        finally:
            await backend.clear_verify_fault()
    with pytest.raises(ValueError): asyncio.run(run())
    assert kube.mode == 'healthy'


def test_fault_wait_uses_inflight_inventory_validation(tmp_path):
    calls = []
    snapshot = SimpleNamespace(validate=lambda policy, **kw: calls.append(kw))
    kube = SimpleNamespace(target=TARGET, observe=lambda: asyncio.sleep(0, result=snapshot))
    backend = demo.Backend(SimpleNamespace(store=tmp_path, regression_image=BAD), None, kube, None)
    asyncio.run(backend.deployment())
    asyncio.run(backend.deployment(inflight=True))
    assert calls == [{'before_dispatch': True}, {'before_dispatch': False}]


def test_wait_for_retries_transient_kubernetes_unavailable_then_succeeds():
    # A rolling Deployment makes observe() report KubernetesUnavailable between reads;
    # the demo must keep waiting instead of aborting right after its own PATCH.
    from sentinelops.remediation.domain import KubernetesUnavailable
    from scripts.run_runbook_demo import wait_for
    calls, t = [], [0.0]
    async def read():
        calls.append(1)
        if len(calls) < 3:
            raise KubernetesUnavailable('deployment-changed-during-inventory-retry-snapshot')
        return {'status': 'CHANGE_OBSERVED'}
    async def sleep(seconds):
        t[0] += seconds
    value = asyncio.run(wait_for(read, lambda v: v['status'] == 'CHANGE_OBSERVED', timeout=30, poll=2,
                                 clock=lambda: t[0], sleep=sleep))
    assert value == {'status': 'CHANGE_OBSERVED'} and len(calls) == 3


def test_wait_for_reraises_kubernetes_unavailable_at_the_deadline():
    from sentinelops.remediation.domain import KubernetesUnavailable
    from scripts.run_runbook_demo import wait_for
    t = [0.0]
    async def read():
        raise KubernetesUnavailable('kubernetes-transport-unavailable')
    async def sleep(seconds):
        t[0] += seconds
    with pytest.raises(KubernetesUnavailable):
        asyncio.run(wait_for(read, lambda v: True, timeout=6, poll=2, clock=lambda: t[0], sleep=sleep))
    assert t[0] >= 6


def test_cli_reports_the_guard_message_but_not_other_exception_text(monkeypatch, capsys):
    import scripts.run_runbook_demo as demo
    monkeypatch.setattr(sys, 'argv', ['run_runbook_demo'] + [a for pair in (
        ('--regression-image', 'x'), ('--server', 'x'), ('--health-stream', 'x'), ('--ledger-ref', 'x'),
        ('--approved-by', 'x'), ('--environment', 'dev'), ('--source-store', 'x'), ('--review-store', 'x'), ('--store', 'x'),
        ('--deployment-change-store', 'x'), ('--target', 'x'), ('--token-file', 'x'), ('--ca-file', 'x'),
        ('--observer-token-file', 'x'), ('--observer-ca-file', 'x'), ('--kill-switch', 'x')) for a in pair])
    async def guard(args):
        raise ValueError('action is missing matching regression evidence')
    monkeypatch.setattr(demo, 'run', guard)
    with pytest.raises(SystemExit) as exc:
        demo.main()
    assert str(exc.value) == 'runbook demo failed: ValueError: action is missing matching regression evidence'
    async def other(args):
        raise RuntimeError('postgresql://user:secret@host/db')
    monkeypatch.setattr(demo, 'run', other)
    with pytest.raises(SystemExit) as exc:
        demo.main()
    assert str(exc.value) == 'runbook demo failed: RuntimeError'


def test_safe_reason_never_echoes_library_value_errors():
    import scripts.run_runbook_demo as demo
    from sentinelops.remediation.domain import Blocked
    assert demo.safe_reason(ValueError('regression must be a distinct digest in the known-good image repository'))
    assert demo.safe_reason(Blocked('deployment-change-baseline-mismatch')) == ': deployment-change-baseline-mismatch'
    for leaky in (ValueError('postgresql://user:password@host/db'), Blocked('abc-def-123'),
                  ValueError("image: 'https://user:password@host/x' does not match"),
                  Blocked('postgresql://user:password@host/db'), TimeoutError('token=abc')):
        assert demo.safe_reason(leaky) == ''
