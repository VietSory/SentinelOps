"""Offline orchestration checks; no cloud, kubectl or executor bypass."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from scripts import run_runbook_demo as demo
from sentinelops.remediation.domain import Target

TARGET = Target('cluster', 'sentinelops-b-actions', 'namespace', 'payment-service', 'deployment', 'payment')
GOOD = 'registry.invalid/sentinelops/application@sha256:' + 'a' * 64
BAD = 'registry.invalid/sentinelops/payment-regression@sha256:' + 'b' * 64


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
        card = {'decision_version': 'runbook-decision/v1', 'decision': 'auto',
            'controller_version': 'auto-runbook-controller/v1',
            'runbook_id': demo.RUNBOOK_ID, 'executor_runbook': 'payment-rollback-known-good/v1',
            'score': 1., 'margin': 1., 'symptoms': {'severity': 'low'},
            'incident_id': f'incident-{number}', 'incident_revision': 1, 'reasons': []}
        if self.kill:
            card.update(decision='engineer', reasons=['runbook-disabled-or-kill-switch'])
        return [(f'artifact://manifests/{number:064x}.json', self.change(card))]

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
    {'score': True}, {'decision': 'oncall'}, {'runbook_id': 'manual'},
    {'executor_runbook': 'other'}, {'symptoms': {'severity': 'medium'}},
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
