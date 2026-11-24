"""Email notifications for the demo (DEMO_UPGRADE_SPEC WP3).

Delivery is at-least-once per event key: a key is recorded only after a successful
publish, so a crash can repeat one email but never lose one. Notification failures
never block the controller or the workers.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from datetime import datetime, timezone

logger = logging.getLogger('sentinelops-notifier')
KINDS = ('early_warning', 'warning_cleared', 'proposal_pending', 'outcome')
TOPIC_ARN = re.compile(r'arn:aws:sns:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]{1,256}')
MAX_ATTEMPTS = 10


def _record_path(root: Path, key: str) -> Path:
    return root / (hashlib.sha256(key.encode()).hexdigest() + '.json')


def read_notification_record(root: Path, key: str) -> dict | None:
    try:
        return json.loads(_record_path(root, key).read_text())
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class Notification:
    kind: str
    key: str
    subject: str
    message: str
    extra: tuple = ()


class NullNotifier:
    def __init__(self):
        self._logged = False

    def send(self, notification):
        if not self._logged:
            logger.info('notifications-disabled: no SNS topic configured')
            self._logged = True
        return 'disabled'


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)
        return f'recorded-{len(self.sent)}'


class SnsNotifier:
    def __init__(self, topic_arn, client=None):
        if not isinstance(topic_arn, str) or not TOPIC_ARN.fullmatch(topic_arn):
            raise ValueError('an explicit SNS topic ARN is required')
        if client is None:
            import boto3
            client = boto3.client('sns', region_name=topic_arn.split(':')[3])
        self.topic_arn, self.client = topic_arn, client

    def send(self, notification):
        reply = self.client.publish(TopicArn=self.topic_arn, Subject=notification.subject[:100],
                                    Message=notification.message)
        return reply['MessageId']


class NotificationMetrics:
    def __init__(self, reader):
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import Resource
        self.provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False,
            resource=Resource({'service.name': 'sentinelops-notifier', 'service.namespace': 'sentinelops-control'}))
        meter = self.provider.get_meter('sentinelops.notifications', '1')
        self.sent = meter.create_counter('sentinelops_notifications_sent')
        self.failed = meter.create_counter('sentinelops_notifications_failed')

    @classmethod
    def otlp(cls, endpoint):
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True, timeout=1,
                                      headers={'x-sentinelops-component': 'notifier'})
        return cls(PeriodicExportingMetricReader(exporter, export_interval_millis=5000, export_timeout_millis=2000))


class Dispatcher:
    """Send each notification key once; record it only after the publish succeeded."""

    def __init__(self, root, notifier, *, metrics=None, max_attempts=MAX_ATTEMPTS):
        self.root, self.notifier, self.metrics = Path(root), notifier, metrics
        self.max_attempts, self.attempts = max_attempts, {}

    def _path(self, key):
        return _record_path(self.root, key)

    def sent(self, key):
        return self._path(key).exists()

    def records(self, kind):
        if not self.root.is_dir():
            return []
        values = [json.loads(path.read_text()) for path in sorted(self.root.glob('*.json'))]
        return [value for value in values if value.get('kind') == kind]

    def record(self, notification, message_id):
        self.root.mkdir(parents=True, exist_ok=True)
        value = {'kind': notification.kind, 'key': notification.key, 'message_id': message_id,
                 'sent_at': datetime.now(timezone.utc).isoformat(), **dict(notification.extra)}
        temporary = self._path(notification.key).with_suffix('.tmp')
        temporary.write_text(json.dumps(value, sort_keys=True))
        os.replace(temporary, self._path(notification.key))

    def deliver(self, notification):
        if notification.kind not in KINDS:
            raise ValueError('unsupported notification kind')
        if self.sent(notification.key):
            return 'duplicate'
        if self.attempts.get(notification.key, 0) >= self.max_attempts:
            return 'gave-up'
        try:
            message_id = self.notifier.send(notification)
        except Exception as exc:
            self.attempts[notification.key] = self.attempts.get(notification.key, 0) + 1
            if self.metrics:
                self.metrics.failed.add(1, {'kind': notification.kind})
            if self.attempts[notification.key] >= self.max_attempts:
                logger.warning('notification-failed kind=%s error=%s', notification.kind, type(exc).__name__)
            return 'failed'
        self.record(notification, message_id)
        if self.metrics:
            self.metrics.sent.add(1, {'kind': notification.kind})
        return 'sent'
