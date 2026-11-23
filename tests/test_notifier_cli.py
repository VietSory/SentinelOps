"""run_notifier: history is seeded without email; later events are sent once; outages skip warnings."""
import httpx

from scripts import run_notifier
from sentinelops.notifications import Dispatcher, RecordingNotifier
from sentinelops.remediation import approvals
from sentinelops.remediation.proposal_api import ProposalStore
from sentinelops.telemetry.store import ArtifactStore


def proposal(identifier):
    return {'proposal_version': approvals.PROPOSAL_VERSION_V2, 'approval_policy': approvals.APPROVAL_POLICY,
            'proposal_id': identifier * 64, 'incident_id': 'INC-' + identifier, 'incident_revision': 1,
            'runbook_id': 'payment-bad-deploy-rollback', 'executor_runbook': 'payment-rollback-known-good/v1',
            'created_at': '2026-12-01T10:00:00Z',
            'current_image': 'registry.invalid/app@sha256:' + '1' * 64,
            'rollback_image': 'registry.invalid/app@sha256:' + '2' * 64}


def prometheus(active):
    def handler(request):
        query = request.url.params['query']
        rows = [{'metric': {'service': s}, 'value': [0, '1' if started else '0']} for s, started in active.items()]
        if query == 'sentinelops_risk_episode_started_unixtime':
            rows = [{'metric': {'service': s}, 'value': [0, str(started)]} for s, started in active.items() if started]
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'vector', 'result': rows}})
    return httpx.Client(base_url='http://prom.invalid', transport=httpx.MockTransport(handler))


def test_first_cycle_seeds_history_then_sends_only_new_events(tmp_path):
    store = ArtifactStore(tmp_path / 'controller')
    store.write('manifests', proposal('a'))
    proposals, notifier = ProposalStore(tmp_path / 'controller'), RecordingNotifier()
    dispatcher = Dispatcher(tmp_path / 'notified', notifier)
    run_notifier.cycle(proposals, dispatcher, prometheus({'payment-service': 1764583260}), 'https://ui/', seed=True)
    assert notifier.sent == []
    store.write('manifests', proposal('b'))
    run_notifier.cycle(proposals, dispatcher, prometheus({'payment-service': 1764583260}), 'https://ui/')
    assert [n.key for n in notifier.sent] == ['proposal:' + 'b' * 64]
    run_notifier.cycle(proposals, dispatcher, prometheus({'payment-service': None}), 'https://ui/')
    assert [n.kind for n in notifier.sent] == ['proposal_pending', 'warning_cleared']


def test_prometheus_outage_never_sends_a_false_all_clear(tmp_path):
    (tmp_path / 'controller').mkdir()
    proposals, notifier = ProposalStore(tmp_path / 'controller'), RecordingNotifier()
    dispatcher = Dispatcher(tmp_path / 'notified', notifier)
    run_notifier.cycle(proposals, dispatcher, prometheus({'payment-service': 1764583260}), 'https://ui/')
    assert [n.kind for n in notifier.sent] == ['early_warning']
    down = httpx.Client(base_url='http://prom.invalid', transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    run_notifier.cycle(proposals, dispatcher, down, 'https://ui/')
    assert [n.kind for n in notifier.sent] == ['early_warning']
