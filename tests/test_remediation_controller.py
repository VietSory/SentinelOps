"""Controller contract tests use a matching stub with the interface signatures."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from sentinelops.remediation import controller as module
from sentinelops.remediation import matching as real_matching
from sentinelops.contracts import utc_time
from sentinelops.incidents.rca_replay import group_severity as original_group_severity
from sentinelops.remediation.controller import AutoRunbookController
from sentinelops.remediation.domain import Blocked, RUNBOOK
from sentinelops.remediation.approvals import record_approval, read_slot, create_slot
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from test_remediation_executor import setup as executor_fixture
from test_remediation_kubernetes import BAD, GOOD, TARGET


def fixture(tmp_path, monkeypatch, *, verdict='oncall', latency=1000.):
    f = executor_fixture()
    store = ArtifactStore(tmp_path / 'evidence')
    decision_store = ArtifactStore(tmp_path / 'decisions')
    group = next(g for g in f.authority.value.rca['correlation']['groups']
                 if g['group_id'] == f.authority.value.rca['group_id'])
    services = group['affected_services']
    opening = []
    latest = {}
    for index, identifier in enumerate(group['signal_ids']):
        service = services[index % len(services)]
        refs = [store.write('observations', {'bucket_end': end, 'payload': {'p95_latency_ms': latency, 'error_rate': .01},
                                             'quality': {'eligible_for_inference': True}})
                for end in ('2026-01-01T00:02:00Z', '2026-01-01T00:01:00Z')]
        signal = {'signal_id': identifier, 'service': service, 'availability': 'ELIGIBLE', 'evidence_refs': refs}
        opening.append({'signal_ref': store.write('signals', signal), 'signal': signal})
        latest[service] = signal
    # Every affected service must have a current consumer health state.
    for service in services:
        latest.setdefault(service, {'availability': 'ELIGIBLE', 'evidence_refs': refs, 'service': service})
    intent_ref = store.write('manifests', {'opening_objects': opening})
    accepted = {'capture_ref': store.write('manifests', {'capture': True}),
                'result_ref': store.write('manifests', f.authority.value.rca)}
    snapshot = {'incident_id': f.request.incident_id, 'revision': f.request.incident_revision,
                'created_at': '2026-01-01T00:00:00Z', 'payload': {'severity': 'low', 'affected_services': services},
                'context': {'lifecycle_stage': 'RCA_READY', 'availability': 'ELIGIBLE', 'source_refs': list(accepted.values())}}
    class Incidents:
        async def get(self, identifier):
            assert identifier == snapshot['incident_id']
            return deepcopy(snapshot)
        async def list_current(self, *, limit=50, after=None):
            return [deepcopy(snapshot)] if after is None else []
    incidents = Incidents()
    class Connection:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def fetchval(self, sql, identifier, consumer):
            assert 'sentinel_control.incident_jobs' in sql
            assert identifier == snapshot['incident_id'] and consumer == 'consumer'
            return 'job' if evidence.has_job else None
    class Pool:
        def acquire(self): return Connection()
    class Source:
        async def next(self, checkpoint): return {} if evidence.backlog else None
    async def verified(job_id):
        assert job_id == 'job'
        evidence.reads += 1
        if evidence.error: raise ValueError('untrusted provider details must not be logged')
        return {'incident_id': snapshot['incident_id'], 'intent_ref': intent_ref}, accepted, {
            'rca': deepcopy(f.authority.value.rca), 'severity': 'low'}
    async def checkpoint(): return None, {'service_states': latest}
    evidence = SimpleNamespace(pool=Pool(), consumer_id='consumer', verified_evidence=verified,
        checkpoint=checkpoint, source=Source(), store=store, reads=0, error=False, has_job=True, backlog=False)
    matching = ModuleType('sentinelops.remediation.matching')
    calls = []
    def extract_symptoms(*, derived, observations):
        for values in observations.values():
            assert values == sorted(values, key=lambda v: v['bucket_end'])
        calls.append(deepcopy(derived))
        return {'severity': derived['severity']}
    def decide(symptoms, catalog):
        assert catalog == {'catalog_version': 'fixture'}
        decision = verdict
        return {'decision_version': 'runbook-decision/v1', 'decision': decision,
            'runbook_id': 'payment-bad-deploy-rollback', 'executor_runbook': RUNBOOK,
            'score': .95, 'margin': .9, 'reasons': [], 'ranking': [], 'symptoms': deepcopy(symptoms),
            'catalog_sha256': stable_id(catalog), 'fingerprint': stable_id(symptoms)}
    matching.extract_symptoms, matching.decide = extract_symptoms, decide
    import sentinelops.remediation as package
    monkeypatch.setitem(sys.modules, matching.__name__, matching)
    monkeypatch.setattr(package, 'matching', matching, raising=False)
    current_severity = ['low']
    monkeypatch.setattr(module, 'group_severity', lambda *args: current_severity[0])
    kwargs = dict(incidents=incidents, evidence=evidence, executor=f.executor,
                  catalog={'catalog_version': 'fixture'}, kill_switch=tmp_path / 'KILL_SWITCH',
                  store=decision_store, clock=f.clock)
    controller = AutoRunbookController(**kwargs)
    return SimpleNamespace(controller=controller, kwargs=kwargs, f=f, evidence=evidence, snapshot=snapshot,
                           severity=current_severity, store=decision_store, calls=calls)


def approve(c, decision='approve'):
    proposal = next(iter(c.controller._decisions.values()))
    return record_approval(c.store, proposal_id=proposal['proposal_id'], approved_by='oncall-test',
        decision=decision, confirm_oncall_approval=True, now=c.f.clock())


@pytest.mark.parametrize('verdict', ['oncall', 'engineer'])
@pytest.mark.parametrize('severity', ['low', 'medium', 'high', 'critical'])
def test_proposals_never_dispatch_without_approval_and_survive_restart(tmp_path, monkeypatch, verdict, severity):
    async def run():
        c = fixture(tmp_path, monkeypatch, verdict=verdict)
        c.severity[0] = c.snapshot['payload']['severity'] = severity
        executed = []
        async def unexpected_execute(request):
            executed.append(request)
            raise AssertionError('executor called without approval')
        monkeypatch.setattr(c.f.executor, 'execute', unexpected_execute)
        results = await c.controller.tick()
        decision = results[0]
        assert decision['decision'] == verdict
        assert c.f.kube.calls == [] and executed == []
        assert c.f.executor.authorize == c.controller.authorize
        if verdict == 'oncall':
            assert decision['proposal_id'] == stable_id([c.f.request.incident_id, 2, decision['runbook_id']])
            from sentinelops.contracts import utc_time
            assert utc_time(decision['expires_at']) - utc_time(decision['created_at']) == timedelta(minutes=15)
        artifacts = list((c.store.root / 'manifests').glob('*.json'))
        assert len(artifacts) == 1
        before = artifacts[0].read_bytes()
        c.snapshot['revision'] += 1
        c.snapshot['payload']['severity'] = 'high'
        await c.controller.tick()
        restarted = AutoRunbookController(**c.kwargs)
        await restarted.tick()
        assert artifacts[0].read_bytes() == before
        assert len(list((c.store.root / 'manifests').glob('*.json'))) == 1
        assert c.f.kube.calls == [] and executed == []
    asyncio.run(run())


def use_real_matching(c, monkeypatch):
    import sentinelops.remediation as package
    monkeypatch.setitem(sys.modules, real_matching.__name__, real_matching)
    monkeypatch.setattr(package, 'matching', real_matching)
    c.controller.catalog = real_matching.load_catalog(
        Path(__file__).resolve().parents[1] / 'configs/runbook_catalog.json')


def test_proposal_pins_server_target_images_and_ledger_in_approval_hash(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        use_real_matching(c, monkeypatch)
        proposal = (await c.controller.tick())[0]
        good = await c.f.executor.registry.resolve(TARGET, incident_at=c.f.clock())
        assert proposal['target'] == asdict(TARGET)
        assert proposal['current_image'] == BAD
        assert proposal['rollback_image'] == GOOD
        assert proposal['known_good_ledger_ref'] == good.ledger_ref
        assert c.f.kube.calls == [] and c.f.journal.state is None
        approve(c)
        receipt = read_slot(c.store, 'approval', proposal['proposal_id'])
        assert receipt['proposal_sha256'] == stable_id(proposal)
        assert (await c.controller.tick())[-1]['phase'] == 'verifying'
        intent = c.f.journal.state['intent']['evidence']
        assert intent['before']['target'] == proposal['target']
        assert intent['before']['image'] == proposal['current_image']
        assert intent['known_good']['image'] == proposal['rollback_image']
        assert intent['known_good']['ledger_ref'] == proposal['known_good_ledger_ref']
    asyncio.run(run())


@pytest.mark.parametrize('timing', ['before-execute', 'before-patch'])
@pytest.mark.parametrize('change', ['revision', 'digest', 'ledger', 'rollback-image', 'ledger-content',
                                  'deployment_uid', 'namespace_uid', 'cluster_uid', 'expired'])
def test_approval_invalidates_revision_target_ledger_and_expiry(tmp_path, monkeypatch, timing, change):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        use_real_matching(c, monkeypatch)
        proposal = (await c.controller.tick())[0]
        approve(c)
        receipt = read_slot(c.store, 'approval', proposal['proposal_id'])
        changed = [False]
        original_observe, original_resolve = c.f.kube.observe, c.f.executor.registry.resolve
        async def observe():
            value = await original_observe()
            if changed[0] and change == 'digest':
                return replace(value, image=BAD.split('@')[0] + '@sha256:' + 'e' * 64)
            if changed[0] and change in ('deployment_uid', 'namespace_uid', 'cluster_uid'):
                return replace(value, target=replace(value.target, **{change: 'recreated-target'}))
            return value
        async def resolve(*args, **kwargs):
            value = await original_resolve(*args, **kwargs)
            if changed[0]:
                if change == 'ledger':
                    return replace(value, ledger_ref='artifact://manifests/' + 'e' * 64 + '.json')
                if change == 'rollback-image':
                    return replace(value, image=GOOD.split('@')[0] + '@sha256:' + 'e' * 64)
                if change == 'ledger-content':
                    raise Blocked('known-good-ledger-changed')
            return value
        monkeypatch.setattr(c.f.kube, 'observe', observe)
        monkeypatch.setattr(c.f.executor.registry, 'resolve', resolve)
        def invalidate():
            changed[0] = True
            if change == 'revision': c.snapshot['revision'] += 1
            if change == 'expired': c.f.time[0] += timedelta(minutes=15)
        if timing == 'before-execute':
            invalidate()
        else:
            c.f.journal.after_arm = invalidate
        results = await c.controller.tick()
        expected = ('incident-revision-changed' if change == 'revision' else
                    'approval-expired' if change == 'expired' else 'approval-target-changed')
        outcome = read_slot(c.store, 'outcome', proposal['proposal_id'])
        assert outcome['phase'] == 'escalated' and outcome['reason'] == expected
        expected_calls = [] if timing == 'before-execute' else [True]
        assert c.f.kube.calls == expected_calls
        if timing == 'before-patch':
            # Dispatch's 600-second deadline rejects before the approval hook;
            # the controller's durable approval outcome still reports expiry.
            assert results[-1]['facts']['reason'] == ('dispatch-expired-before-send' if change == 'expired' else expected)
            assert results[-1]['facts']['dispatch_cancelled_before_send'] is True
        assert read_slot(c.store, 'approval', proposal['proposal_id']) == receipt
        assert c.store.read('artifact://manifests/' + stable_id(proposal) + '.json') == proposal
        await AutoRunbookController(**c.kwargs).tick()
        assert c.f.kube.calls == expected_calls
    asyncio.run(run())


def test_prepared_ledger_must_match_even_if_live_ledger_returns_to_original(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        await c.controller.tick()
        approve(c)
        original_resolve, original_authorize = c.f.executor.registry.resolve, c.f.executor.authorize
        alternate = [False]
        async def resolve(*args, **kwargs):
            good = await original_resolve(*args, **kwargs)
            return replace(good, ledger_ref='artifact://manifests/' + 'e' * 64 + '.json') if alternate[0] else good
        async def authorize(request):
            # The live ledger changes after prepare's hook, then returns before
            # the final hook. The actual prepared inputs still used a new ledger.
            alternate[0] = False
            await original_authorize(request)
            if c.f.journal.state is None:
                alternate[0] = True
        monkeypatch.setattr(c.f.executor.registry, 'resolve', resolve)
        monkeypatch.setattr(c.f.executor, 'authorize', authorize)
        result = (await c.controller.tick())[-1]
        assert result['phase'] == 'escalated' and result['facts']['reason'] == 'approval-target-changed'
        assert result['facts']['dispatch_cancelled_before_send'] is True
        assert c.f.kube.calls == [True]
    asyncio.run(run())


@pytest.mark.parametrize('missing', module.TARGET_BINDING)
def test_legacy_proposal_missing_any_target_binding_cannot_dispatch(tmp_path, monkeypatch, missing):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        del proposal[missing]
        legacy = ArtifactStore(tmp_path / 'unbound-proposal')
        legacy.write('manifests', proposal)
        record_approval(legacy, proposal_id=proposal['proposal_id'], approved_by='oncall-test',
            decision='approve', confirm_oncall_approval=True, now=c.f.clock())
        restarted = AutoRunbookController(**{**c.kwargs, 'store': legacy})
        result = (await restarted.tick())[0]
        assert result['phase'] == 'escalated' and result['reason'] == 'approval-target-changed'
        assert c.f.kube.calls == [] and c.f.journal.state is None
    asyncio.run(run())


@pytest.mark.parametrize('severity', ['low', 'medium', 'high', 'critical'])
def test_real_matching_requires_approval_then_dispatches_at_every_severity(tmp_path, monkeypatch, severity):
    async def run():
        c = fixture(tmp_path, monkeypatch, latency=981.)
        c.severity[0] = c.snapshot['payload']['severity'] = severity
        use_real_matching(c, monkeypatch)
        executed = []
        original_execute = c.f.executor.execute
        async def execute(request):
            executed.append(request)
            return await original_execute(request)
        monkeypatch.setattr(c.f.executor, 'execute', execute)
        proposal = (await c.controller.tick())[0]
        assert proposal['decision'] == 'oncall' and proposal['symptoms']['severity'] == severity
        assert proposal['score'] >= .80 and proposal['margin'] >= .15
        await c.controller.tick()
        assert executed == [] and c.f.kube.calls == []
        approve(c)
        assert (await c.controller.tick())[-1]['phase'] == 'verifying'
        assert len(executed) == 1 and c.f.kube.calls == [True, False]
        await c.controller.tick()
        assert len(executed) == 1 and c.f.kube.calls == [True, False]
    asyncio.run(run())


def test_payment_981ms_with_checkout_is_high_and_still_proposes_approval(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch, latency=981.)
        use_real_matching(c, monkeypatch)
        monkeypatch.setattr(module, 'group_severity', original_group_severity)
        proposal = (await c.controller.tick())[0]
        assert proposal['symptoms']['severity'] == 'high'
        assert proposal['symptoms']['root_p95_ms'] == 981.
        assert proposal['symptoms']['checkout_impacted'] is True
        assert proposal['symptoms']['rca_abstained'] is False
        assert proposal['decision'] == 'oncall' and proposal['score'] >= .80 and proposal['margin'] >= .15
        assert c.f.kube.calls == [] and c.f.journal.state is None
        approve(c)
        assert (await c.controller.tick())[-1]['phase'] == 'verifying'
        assert c.f.kube.calls == [True, False]
    asyncio.run(run())


@pytest.mark.parametrize('before,after', [('low', 'medium'), ('medium', 'high'), ('high', 'critical')])
def test_real_matching_blocks_severity_increase_after_approval_before_patch(tmp_path, monkeypatch, before, after):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        c.severity[0] = c.snapshot['payload']['severity'] = before
        use_real_matching(c, monkeypatch)
        assert (await c.controller.tick())[0]['decision'] == 'oncall'
        approve(c)
        c.f.journal.after_arm = lambda: c.severity.__setitem__(0, after)
        result = (await c.controller.tick())[-1]
        assert result['phase'] == 'escalated'
        assert result['facts']['dispatch_cancelled_before_send'] is True
        assert c.f.kube.calls == [True]
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['kill-switch', 'unknown', 'no-job', 'bad-evidence', 'backlog', 'unbound', 'abstain'])
def test_fail_closed_records_engineer_without_any_patch(tmp_path, monkeypatch, failure):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        if failure == 'kill-switch': c.controller.kill_switch.touch()
        if failure == 'unknown': c.snapshot['context']['availability'] = 'UNKNOWN'
        if failure == 'no-job': c.evidence.has_job = False
        if failure == 'bad-evidence': c.evidence.error = True
        if failure == 'backlog': c.evidence.backlog = True
        if failure == 'unbound': c.snapshot['context']['source_refs'] = []
        if failure == 'abstain': c.f.authority.value.rca['abstained'] = True
        results = await c.controller.tick()
        assert results[0]['decision'] == 'engineer'
        assert c.f.kube.calls == [] and c.f.journal.state is None
        assert 'untrusted provider' not in json.dumps(results)
        await c.controller.tick()
        assert len(list((c.store.root / 'manifests').glob('*.json'))) == 1
    asyncio.run(run())


@pytest.mark.parametrize('change', ['current-severity', 'incident-severity', 'revision', 'kill-switch',
                                  'evidence-error', 'expired', 'approval-changed'])
def test_authorize_rechecks_after_arm_and_blocks_before_real_patch(tmp_path, monkeypatch, change):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        def after_arm():
            if change == 'current-severity': c.severity[0] = 'high'
            if change == 'incident-severity': c.snapshot['payload']['severity'] = 'critical'
            if change == 'revision': c.snapshot['revision'] += 1
            if change == 'kill-switch': c.controller.kill_switch.touch()
            if change == 'evidence-error': c.evidence.error = True
            if change == 'expired': c.f.time[0] += timedelta(minutes=15)
            if change == 'approval-changed':
                pid = next(iter(c.controller._decisions.values()))['proposal_id']
                receipt = read_slot(c.store, 'approval', pid)
                receipt['decision'] = 'reject'
                (c.store.root / 'runbook-approvals' / 'approval' / (pid + '.json')).unlink()
                create_slot(c.store, 'approval', pid, receipt)
        c.f.journal.after_arm = after_arm
        assert (await c.controller.tick())[0]['decision'] == 'oncall'
        approve(c)
        results = await c.controller.tick()
        assert results[-1]['phase'] == 'escalated'
        assert results[-1]['facts']['dispatch_cancelled_before_send'] is True
        assert c.f.kube.calls == [True]
        await c.controller.tick()
        assert c.f.kube.calls == [True]
    asyncio.run(run())


def test_reconciliation_continues_after_kill_switch_until_terminal(tmp_path, monkeypatch):
    from datetime import timedelta
    async def run():
        c = fixture(tmp_path, monkeypatch)
        await c.controller.tick()
        approve(c)
        await c.controller.tick()
        c.controller.kill_switch.touch()
        assert (await c.controller.tick())[0]['phase'] == 'verifying'
        c.f.time[0] += timedelta(minutes=4)
        assert (await c.controller.tick())[0]['phase'] == 'recovered'
        assert await c.controller.tick() == []
        assert c.f.kube.calls == [True, False]
    asyncio.run(run())


def test_write_failure_prevents_dispatch_and_crash_after_decision_never_retries(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        original = c.store.write
        def broken(*args): raise OSError('disk full')
        monkeypatch.setattr(c.store, 'write', broken)
        with pytest.raises(OSError): await c.controller.tick()
        assert c.f.kube.calls == []
        monkeypatch.setattr(c.store, 'write', original)
        async def crash(request): raise ConnectionError('crash before intent')
        monkeypatch.setattr(c.f.executor, 'execute', crash)
        await c.controller.tick()
        approve(c)
        with pytest.raises(ConnectionError): await c.controller.tick()
        assert read_slot(c.store, 'used', next(iter(c.controller._decisions.values()))['proposal_id'])
        assert await AutoRunbookController(**c.kwargs).tick() == []
        assert c.f.kube.calls == []
    asyncio.run(run())


def test_concurrent_ticks_do_not_dispatch_twice(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        await c.controller.tick()
        approve(c)
        await asyncio.gather(*(c.controller.tick() for _ in range(5)))
        assert c.f.kube.calls == [True, False]
    asyncio.run(run())


def test_non_ready_incident_is_not_decided(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        c.snapshot['context']['lifecycle_stage'] = 'OPEN'
        assert await c.controller.tick() == [] and c.evidence.reads == 0
    asyncio.run(run())


def test_action_budget_defers_second_incident_and_paginates_past_non_ready_rows(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        first = deepcopy(c.snapshot)
        second = {**deepcopy(first), 'incident_id': 'INC-other'}
        ignored = [{**deepcopy(first), 'incident_id': f'ignored-{i}',
                    'context': {'lifecycle_stage': 'CLOSED'}} for i in range(100)]
        pages = []
        async def list_current(*, limit, after):
            pages.append(after)
            return ignored + [first] if after is None else [first, second]
        monkeypatch.setattr(c.kwargs['incidents'], 'list_current', list_current)
        async def get(identifier):
            return deepcopy(first if identifier == first['incident_id'] else second)
        monkeypatch.setattr(c.kwargs['incidents'], 'get', get)
        verdict = c.controller._engineer('fixture')
        verdict.update(decision='oncall', executor_runbook=RUNBOOK, runbook_id='fixture')
        verdict.update(target=asdict(TARGET), current_image=BAD, rollback_image=GOOD,
                       known_good_ledger_ref='artifact://manifests/' + '1' * 64 + '.json')
        async def decide(snapshot): return deepcopy(verdict)
        monkeypatch.setattr(c.controller, '_decide', decide)
        requests = []
        async def execute(request):
            requests.append(request)
            return {'action_id': request.incident_id, 'phase': 'recovered'}
        monkeypatch.setattr(c.f.executor, 'execute', execute)
        await c.controller.tick()
        assert len(requests) == 0 and len(c.controller._decisions) == 2
        for proposal in c.controller._decisions.values():
            record_approval(c.store, proposal_id=proposal['proposal_id'], approved_by='oncall-test',
                decision='approve', confirm_oncall_approval=True, now=c.f.clock())
        await c.controller.tick()
        assert len(requests) == 1 and requests[0].incident_id == first['incident_id']
        assert pages[1][1] == 'ignored-99'
        await c.controller.tick()
        assert len(requests) == 2 and requests[1].incident_id == second['incident_id']
    asyncio.run(run())


def test_restart_cancels_prepared_intent_and_rejects_corrupt_decision(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        c.f.journal.fail_arm = True
        await c.controller.tick()
        approve(c)
        with pytest.raises(ConnectionError): await c.controller.tick()
        assert c.f.journal.state['phase'] == 'prepared' and c.f.kube.calls == [True]
        restarted = AutoRunbookController(**c.kwargs)
        assert (await restarted.tick())[0]['phase'] == 'blocked'
        assert c.f.kube.calls == [True]
        artifact = next((c.store.root / 'manifests').glob('*.json'))
        artifact.write_text('{}')
        with pytest.raises(ValueError, match='checksum'):
            await AutoRunbookController(**c.kwargs).tick()
        assert c.f.kube.calls == [True]
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['reject', 'expired', 'wrong-proposal', 'wrong-hash', 'used',
                                  'revision', 'severity', 'decision', 'stale', 'malformed', 'future', 'blank-operator'])
def test_approval_failures_cannot_dispatch(tmp_path, monkeypatch, problem):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        # RCA is already 2 seconds old when proposed, so it can become stale
        # while the proposal is still within its independent 900-second TTL.
        if problem == 'stale': c.f.time[0] += timedelta(seconds=2)
        proposal = (await c.controller.tick())[0]
        pid = proposal['proposal_id']
        approve(c, 'reject' if problem == 'reject' else 'approve')
        if problem in ('wrong-proposal', 'wrong-hash', 'malformed', 'future', 'blank-operator'):
            original = read_slot(c.store, 'approval', pid)
            if problem == 'wrong-proposal': original['proposal_id'] = 'f' * 64
            if problem == 'wrong-hash': original['proposal_sha256'] = 'f' * 64
            if problem == 'malformed': original['approved_at'] = None
            if problem == 'future': original['approved_at'] = proposal['expires_at']
            if problem == 'blank-operator': original['approved_by'] = ' '
            # Simulate an untrusted receipt in the identity slot.
            path = c.store.root / 'runbook-approvals' / 'approval' / (pid + '.json')
            path.unlink()
            create_slot(c.store, 'approval', pid, original)
        if problem == 'used':
            create_slot(c.store, 'used', pid, {'proposal_id': pid, 'approval_sha256': 'f'*64})
        if problem == 'expired': c.f.time[0] += timedelta(minutes=15)
        if problem == 'revision': c.snapshot['revision'] += 1
        if problem == 'severity': c.snapshot['payload']['severity'] = 'high'
        if problem == 'decision': c.controller.catalog['catalog_version'] = 'changed'
        if problem == 'stale': c.f.time[0] += timedelta(seconds=899)
        if problem == 'decision':
            async def changed(snapshot): return c.controller._engineer('changed')
            monkeypatch.setattr(c.controller, '_decide', changed)
        results = await c.controller.tick()
        assert c.f.kube.calls == []
        if problem == 'expired': assert results[0]['reason'] == 'approval-expired'
        if problem == 'stale':
            assert c.f.clock() < utc_time(proposal['expires_at'])
            assert (c.f.clock() - utc_time(c.f.authority.value.rca['as_of'])).total_seconds() == 901
            assert results[0]['reason'] == 'approval-after-evidence-freshness'
            assert c.f.journal.state['facts']['reason'] == 'evidence-stale-or-future'
        await AutoRunbookController(**c.kwargs).tick()
        assert c.f.kube.calls == []
    asyncio.run(run())


def test_unapproved_proposal_expires_and_cannot_be_approved_later(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        c.f.time[0] += timedelta(minutes=15)
        outcome = (await c.controller.tick())[0]
        assert outcome['phase'] == 'escalated' and outcome['reason'] == 'approval-expired'
        assert read_slot(c.store, 'outcome', proposal['proposal_id']) == outcome
        with pytest.raises(ValueError): approve(c)
        assert c.f.kube.calls == []
    asyncio.run(run())


def test_approved_proposal_dispatches_once_and_restart_only_reconciles(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        await c.controller.tick()
        approve(c)
        assert (await c.controller.tick())[-1]['phase'] == 'verifying'
        request = c.f.journal.state['intent']['request']
        assert request['idempotency_key'] == stable_id([c.f.request.incident_id, 2, RUNBOOK])[:32]
        await AutoRunbookController(**c.kwargs).tick()
        assert c.f.kube.calls == [True, False]
        with pytest.raises(ValueError): approve(c)
    asyncio.run(run())


def test_restart_can_execute_pending_approved_proposal_and_logs_proposal(tmp_path, monkeypatch, caplog):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        with caplog.at_level('INFO', logger=module.logger.name):
            proposal = (await c.controller.tick())[0]
        event = json.loads(caplog.records[-1].message)
        assert event['event'] == 'runbook_proposal' and event['proposal_id'] == proposal['proposal_id']
        approve(c)
        restarted = AutoRunbookController(**c.kwargs)
        assert (await restarted.tick())[-1]['phase'] == 'verifying'
        assert c.f.kube.calls == [True, False]
    asyncio.run(run())


def test_legacy_auto_decision_can_never_dispatch(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch, verdict='auto')
        result = (await c.controller.tick())[0]
        assert result['decision'] == 'engineer' and c.f.kube.calls == []
    asyncio.run(run())


def test_legacy_auto_intent_is_reconciled_without_redispatch(tmp_path, monkeypatch):
    async def run():
        c = fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        approve(c)
        await c.controller.tick()
        legacy = {k: v for k, v in proposal.items()
                  if k not in ('proposal_version', 'proposal_id', 'created_at', 'expires_at')}
        legacy['decision'] = 'auto'
        legacy_store = ArtifactStore(tmp_path / 'legacy-decisions')
        legacy_store.write('manifests', legacy)
        restarted = AutoRunbookController(**{**c.kwargs, 'store': legacy_store})
        assert (await restarted.tick())[0]['phase'] == 'verifying'
        assert c.f.kube.calls == [True, False]
    asyncio.run(run())


@pytest.mark.parametrize('enabled', [False, True])
def test_cli_policy_is_enabled_only_by_explicit_flag(tmp_path, monkeypatch, enabled):
    from scripts import run_remediation_controller as script
    from test_remediation_kubernetes import TARGET
    async def run():
        c = fixture(tmp_path, monkeypatch)
        argv = ['--health-stream', 'a'*64, '--source-store', str(tmp_path), '--target', str(tmp_path/'target.json'),
                '--server', 'https://127.0.0.1:6443', '--token-file', str(tmp_path/'token'),
                '--ca-file', str(tmp_path/'ca'), '--kill-switch', str(tmp_path/'kill'), '--max-ticks', '1']
        args = script.parser().parse_args(argv + (['--policy-enabled'] if enabled else []))
        assert args.poll_seconds == 10.
        import sentinelops.remediation.matching as matching
        monkeypatch.setattr(matching, 'load_catalog', lambda path: {}, raising=False)
        monkeypatch.setattr(script, 'validate_configuration', lambda *a: TARGET)
        class Conn:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def fetchrow(self, *a): return {}
            async def fetchval(self, *a): return {'evidence_profile': {}}
        class Pool:
            closed = False
            def acquire(self): return Conn()
            async def close(self): self.closed = True
        pool = Pool()
        async def create_pool(*a, **kw): return pool
        monkeypatch.setattr(script.asyncpg, 'create_pool', create_pool)
        class Actions:
            def __init__(self, *a): pass
            async def check_ready(self): pass
        monkeypatch.setattr(script, 'ActionJournal', Actions)
        monkeypatch.setattr(script, 'validate_stream', lambda *a: {
            'artifact_root_hash': stable_id({'root': str(tmp_path.resolve())}), 'environment': 'local'})
        monkeypatch.setattr(script, 'IncidentJournal', lambda *a, **kw: c.evidence)
        monkeypatch.setattr(script, 'KnownGoodRegistry', lambda *a, **kw: None)
        class Kube:
            def __init__(self, *a, **kw): pass
            async def close(self): pass
        monkeypatch.setattr(script, 'LocalKubernetes', Kube)
        monkeypatch.setattr(script, 'NativeH4Authority', lambda *a: None)
        class Done(Exception): pass
        def executor(*a, **kw):
            assert a[5].enabled is enabled
            raise Done()
        monkeypatch.setattr(script, 'RollbackExecutor', executor)
        with pytest.raises(Done): await script.run(args)
        assert pool.closed
    asyncio.run(run())
