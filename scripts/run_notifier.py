"""Email the on-call about early warnings, pending proposals and outcomes (DEMO_UPGRADE_SPEC WP3).

Reads the controller store (read-only) and the risk worker metrics in Prometheus.
Keeps its own record of sent keys. On its very first start it records the events
that already exist as sent, so history is never mailed. Failures only log; this
process never blocks the controller.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import signal
import threading
from datetime import datetime, timezone

import httpx

from sentinelops.notifications import Dispatcher, NotificationMetrics, NullNotifier, SnsNotifier
from sentinelops.notifications.events import proposal_notifications, warning_notifications
from sentinelops.remediation.pod_memory_metrics import metrics_endpoint
from sentinelops.remediation.proposal_api import ProposalStore

logger = logging.getLogger('sentinelops-notifier')
SERVICES = ('checkout-service', 'payment-service', 'inventory-service')
PROMETHEUS = 'http://prometheus.sentinelops-b-actions.svc.cluster.local:9090'


def active_episodes(client):
    """service -> ISO start of the open episode, or None; services without data are omitted."""
    def vector(query):
        body = client.get('/api/v1/query', params={'query': query}).json()
        if body.get('status') != 'success':
            raise ValueError('prometheus query failed')
        return {item['metric'].get('service'): float(item['value'][1]) for item in body['data']['result']}
    active = vector('sentinelops_risk_episode_active')
    started = vector('sentinelops_risk_episode_started_unixtime')
    result = {}
    for service in SERVICES:
        if service not in active:
            continue
        moment = started.get(service)
        result[service] = (datetime.fromtimestamp(moment, timezone.utc).isoformat().replace('+00:00', 'Z')
                           if active[service] == 1 and moment else None)
    return result


def cycle(proposals, dispatcher, client, ui_url, *, seed=False):
    views = [proposals.view(p) for p in proposals.proposals()]
    notifications = proposal_notifications(views, ui_url)
    try:
        notifications += warning_notifications(active_episodes(client), dispatcher, ui_url)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning('risk metrics unavailable: %s', type(exc).__name__)
    for notification in notifications:
        if seed:
            if not dispatcher.sent(notification.key):
                dispatcher.record(notification, 'seeded-existing-event')
        else:
            dispatcher.deliver(notification)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True, help='Controller decision store (read-only)')
    parser.add_argument('--notifier-store', type=Path, required=True, help='Writable record of sent notifications')
    parser.add_argument('--topic-arn', help='SNS topic; without it notifications are disabled')
    parser.add_argument('--ui-url', required=True)
    parser.add_argument('--prometheus', default=PROMETHEUS)
    parser.add_argument('--metrics-otlp-endpoint')
    parser.add_argument('--poll-seconds', type=float, default=10.)
    args = parser.parse_args()
    if not 2 <= args.poll_seconds <= 120:
        parser.error('poll interval must be between 2 and 120 seconds')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    notifier = SnsNotifier(args.topic_arn) if args.topic_arn else NullNotifier()
    if args.topic_arn:
        try:
            import boto3
            arn = boto3.client('sts').get_caller_identity()['Arn']
            logger.info('notifier identity %s', arn)
        except Exception as exc:
            logger.warning('notifications-unavailable: no AWS identity (%s)', type(exc).__name__)
    metrics = NotificationMetrics.otlp(metrics_endpoint(args.metrics_otlp_endpoint)) if args.metrics_otlp_endpoint else None
    seed = not args.notifier_store.exists() or not any(args.notifier_store.iterdir())
    dispatcher = Dispatcher(args.notifier_store, notifier, metrics=metrics)
    proposals = ProposalStore(args.store)
    stop = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stop.set())
    with httpx.Client(base_url=args.prometheus, timeout=8, trust_env=False) as client:
        while not stop.is_set():
            try:
                cycle(proposals, dispatcher, client, args.ui_url, seed=seed)
                seed = False
            except Exception as exc:
                logger.warning('notifier cycle failed: %s', type(exc).__name__)
            stop.wait(args.poll_seconds)


if __name__ == '__main__':
    main()
