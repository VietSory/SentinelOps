"""WP3 acceptance (frozen): email notifications through SNS.

Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP3 (R3.1-R3.6, A3.1-A3.5).
"""
import asyncio
import re

import pytest

from sentinelops.notifications import (Dispatcher, Notification, NotificationMetrics, NullNotifier,
                                       RecordingNotifier, SnsNotifier)
from sentinelops.notifications.events import proposal_notifications, warning_notifications
from sentinelops.remediation.approvals import read_slot

from test_remediation_controller import fixture as controller_fixture, approve

UI = 'https://demo.example.invalid/ui/'
PID = 'a' * 64


def view(status, *, outcome=None, action=None, runbook='payment-rollback-known-good/v1', final=None):
    proposal = {'proposal_version': 'runbook-proposal/v2', 'proposal_id': PID, 'incident_id': 'INC-demo',
                'incident_revision': 2, 'runbook_id': 'payment-bad-deploy-rollback', 'executor_runbook': runbook,
                'created_at': '2026-12-01T10:10:00Z',
                'current_image': 'registry.invalid/app@sha256:' + '1' * 64,
                'rollback_image': 'registry.invalid/app@sha256:' + '2' * 64}
    value = {'proposal': proposal, 'status': status, 'outcome': outcome, 'final': final}
    if action is not None:
        value['memory_action'] = action
    return value


def kinds(notifications):
    return sorted((n.kind, n.key) for n in notifications)


def test_proposal_and_outcome_events_have_stable_keys():
    assert kinds(proposal_notifications([view('pending')], UI)) == [('proposal_pending', 'proposal:' + PID)]
    assert kinds(proposal_notifications([view('consumed', final={'phase': 'recovered'})], UI)) == [
        ('outcome', f'outcome:{PID}:recovered'), ('proposal_pending', 'proposal:' + PID)]
    assert ('outcome', f'outcome:{PID}:obsolete') in kinds(proposal_notifications(
        [view('obsolete', outcome={'phase': 'obsolete', 'reason': 'incident-changed'})], UI))
    assert ('outcome', f'outcome:{PID}:rejected') in kinds(proposal_notifications(
        [view('engineer', outcome={'phase': 'escalated', 'reason': 'approval-rejected'})], UI))
    assert ('outcome', f'outcome:{PID}:escalated') in kinds(proposal_notifications(
        [view('engineer', outcome={'phase': 'escalated', 'reason': 'evidence-stale-or-future'})], UI))
    oom = view('recovered', runbook='pod-memory-limit-oom/v1', action={'phase': 'recovered'})
    assert ('outcome', f'outcome:{PID}:recovered') in kinds(proposal_notifications([oom], UI))
    assert kinds(proposal_notifications([view('consumed')], UI)) == [('proposal_pending', 'proposal:' + PID)]


def test_each_event_sends_once_across_restarts(tmp_path):
    notifier = RecordingNotifier()
    for _ in range(3):
        dispatcher = Dispatcher(tmp_path / 'notified', notifier)
        for notification in proposal_notifications([view('consumed', final={'phase': 'recovered'})], UI):
            dispatcher.deliver(notification)
    assert sorted(n.kind for n in notifier.sent) == ['outcome', 'proposal_pending']


def test_publish_failure_retries_then_succeeds(tmp_path):
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    reader = InMemoryMetricReader()
    class Flaky(RecordingNotifier):
        def __init__(self):
            super().__init__(); self.failures = 1
        def send(self, notification):
            if self.failures:
                self.failures -= 1
                raise RuntimeError('sns unavailable')
            return super().send(notification)
    notifier = Flaky()
    dispatcher = Dispatcher(tmp_path / 'notified', notifier, metrics=NotificationMetrics(reader))
    [notification] = proposal_notifications([view('pending')], UI)
    assert dispatcher.deliver(notification) == 'failed'
    assert dispatcher.deliver(notification) == 'sent'
    assert dispatcher.deliver(notification) == 'duplicate'
    assert len(notifier.sent) == 1
    totals = {}
    for resource in reader.get_metrics_data().resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    totals[(metric.name, point.attributes['kind'])] = point.value
    assert totals == {('sentinelops_notifications_sent', 'proposal_pending'): 1,
                      ('sentinelops_notifications_failed', 'proposal_pending'): 1}


def test_gives_up_after_ten_failures(tmp_path):
    class Down(RecordingNotifier):
        def send(self, notification): raise RuntimeError('down')
    dispatcher = Dispatcher(tmp_path / 'notified', Down())
    [notification] = proposal_notifications([view('pending')], UI)
    results = [dispatcher.deliver(notification) for _ in range(12)]
    assert results[:10] == ['failed'] * 10 and results[10:] == ['gave-up'] * 2


SECRET = re.compile(r'sha256:[0-9a-f]{64}|postgres(?:ql)?://|Bearer |AKIA[0-9A-Z]{16}')


def test_message_contents_are_bounded_and_safe(tmp_path):
    values = proposal_notifications([view('consumed', final={'phase': 'recovered'})], UI)
    dispatcher = Dispatcher(tmp_path / 'notified', RecordingNotifier())
    values += warning_notifications({'payment-service': '2026-12-01T10:03:00Z'}, dispatcher, UI)
    assert {n.kind for n in values} == {'proposal_pending', 'outcome', 'early_warning'}
    for notification in values:
        assert notification.subject.startswith('[SentinelOps] ') and len(notification.subject) <= 100
        assert UI in notification.message
        assert not SECRET.search(notification.subject + notification.message)
    warning = next(n for n in values if n.kind == 'early_warning')
    assert 'payment-service' in warning.subject and 'chưa phải sự cố' in warning.message
    assert '+07:00' in warning.message and 'UTC' in warning.message
    pending = next(n for n in values if n.kind == 'proposal_pending')
    assert 'INC-demo' in pending.subject and 'INC-demo' in pending.message


def test_warning_cleared_only_after_a_sent_warning(tmp_path):
    notifier = RecordingNotifier()
    dispatcher = Dispatcher(tmp_path / 'notified', notifier)
    assert warning_notifications({'payment-service': None}, dispatcher, UI) == []
    for n in warning_notifications({'payment-service': '2026-12-01T10:03:00Z'}, dispatcher, UI):
        dispatcher.deliver(n)
    cleared = warning_notifications({'payment-service': None}, Dispatcher(tmp_path / 'notified', notifier), UI)
    assert kinds(cleared) == [('warning_cleared', 'warning-cleared:payment-service:2026-12-01T10:03:00Z')]
    for n in cleared:
        dispatcher.deliver(n)
    assert warning_notifications({'payment-service': None}, dispatcher, UI) == []


def test_disabled_without_topic(caplog):
    notifier = NullNotifier()
    with caplog.at_level('INFO'):
        notifier.send(Notification('proposal_pending', 'proposal:x', '[SentinelOps] x', 'y'))
        notifier.send(Notification('proposal_pending', 'proposal:z', '[SentinelOps] z', 'y'))
    assert caplog.text.count('notifications-disabled') == 1


def test_sns_publish_call():
    import boto3
    from botocore.stub import Stubber
    client = boto3.client('sns', region_name='ap-southeast-1', aws_access_key_id='fixture',
                          aws_secret_access_key='fixture')
    arn = 'arn:aws:sns:ap-southeast-1:000000000000:sentinelops-dev-demo-alerts'
    notification = Notification('proposal_pending', 'proposal:x', '[SentinelOps] Cần duyệt', 'nội dung')
    with Stubber(client) as stub:
        stub.add_response('publish', {'MessageId': 'm-1'},
                          {'TopicArn': arn, 'Subject': notification.subject, 'Message': notification.message})
        assert SnsNotifier(arn, client).send(notification) == 'm-1'
        stub.assert_no_pending_responses()
    with pytest.raises(ValueError):
        SnsNotifier('not-an-arn', client)


def test_controller_records_the_final_action_phase(tmp_path, monkeypatch):
    async def run():
        c = controller_fixture(tmp_path, monkeypatch)
        proposal = (await c.controller.tick())[0]
        approve(c)
        await c.controller.tick()
        assert read_slot(c.store, 'final', proposal['proposal_id']) is None
        from datetime import timedelta
        c.f.time[0] += timedelta(minutes=5)
        await c.controller.tick()
        final = read_slot(c.store, 'final', proposal['proposal_id'])
        assert final['phase'] == 'recovered' and final['proposal_id'] == proposal['proposal_id']
    asyncio.run(run())
