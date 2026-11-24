"""Per-minute early-warning risk from the deployed model release (DEMO_UPGRADE_SPEC WP2).

Read-only with respect to the accepted health stream: features are read from the
health worker's store, derived projections/signals go to a separate scratch store,
and nothing is written to the control database. A warning only informs people; it
never opens an incident, creates a proposal or authorizes an action.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import threading

from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.workers.model_signals import collect_model_signals

logger = logging.getLogger('sentinelops-risk-worker')
SERVICES = tuple(sorted(BUSINESS_ROUTES))
STATES = ('normal', 'watch', 'degrading', 'unavailable')
BUCKET = timedelta(seconds=60)
CLEAR_BUCKETS = 2
LOST_BUCKETS = 5


class ReadThroughStore:
    """Read accepted artifacts from the source store; write derived artifacts elsewhere."""

    def __init__(self, source, scratch):
        if source.root == scratch.root:
            raise ValueError('risk scratch store must be separate from the health store')
        self.source, self.scratch = source, scratch
        self.root = scratch.root

    def read(self, reference):
        try:
            return self.source.read(reference)
        except FileNotFoundError:
            return self.scratch.read(reference)

    def write(self, kind, value):
        return self.scratch.write(kind, value)


def _unavailable(bucket_end):
    return {'state': 'unavailable', 'risk_score': None, 'anomaly_score': None,
            'bucket_end': bucket_end, 'model_version': None}


class RiskScorer:
    def __init__(self, health_store, scratch_store, registry):
        self.store = ReadThroughStore(health_store, scratch_store)
        self.health_store, self.registry = health_store, registry

    def score(self, batch_ref, *, now):
        batch = self.health_store.read(batch_ref)
        normalization = self.health_store.read(batch['normalization_manifest_ref'])
        results = {service: _unavailable(batch['bucket_end']) for service in SERVICES}
        signals = collect_model_signals(self.store, normalization, bucket_end=batch['bucket_end'],
                                        registry=self.registry, now=now, batch_ref=batch_ref)
        for _, signal in signals:
            inference = signal.get('inference')
            if signal['service'] not in results or not inference or inference.get('risk') is None:
                continue
            value = inference['risk']
            results[signal['service']] = {'state': value['state'], 'risk_score': value['risk_score'],
                'anomaly_score': inference['detection']['anomaly_score'], 'bucket_end': batch['bucket_end'],
                'model_version': inference['model_version']}
        return results


def release_thresholds(registry):
    thresholds = {}
    for service in SERVICES:
        route = getattr(registry, '_models', {}).get(service)
        values = route[0].get('risk_thresholds') if route else None
        if values:
            thresholds[service] = {'watch': float(values['watch']), 'degrading': float(values['degrading'])}
    return thresholds


class EpisodeTracker:
    """Warning episodes: start on `degrading` right after `watch`/`degrading` (two consecutive buckets)."""

    def __init__(self):
        self._services = {}

    def update(self, service, bucket_end, state):
        if state not in STATES:
            raise ValueError('unsupported risk state')
        current = self._services.setdefault(service, {'previous': None, 'previous_at': None, 'started_at': None,
                                                        'normal': 0, 'unavailable': 0})
        consecutive = current['previous_at'] is not None and bucket_end - current['previous_at'] == BUCKET
        event = None
        if current['started_at'] is None:
            if state == 'degrading' and consecutive and current['previous'] in ('watch', 'degrading'):
                current['started_at'] = timestamp(bucket_end)
                event = 'started'
        else:
            current['normal'] = current['normal'] + 1 if state == 'normal' else 0
            current['unavailable'] = current['unavailable'] + 1 if state == 'unavailable' else 0
            if current['normal'] >= CLEAR_BUCKETS or current['unavailable'] >= LOST_BUCKETS:
                current['started_at'] = None
                event = 'ended'
        if current['started_at'] is None:
            current['normal'] = current['unavailable'] = 0
        current['previous'], current['previous_at'] = state, bucket_end
        return {'event': event, 'active': current['started_at'] is not None, 'started_at': current['started_at']}


class RiskMetrics:
    def __init__(self, reader, *, thresholds):
        self._lock, self._values, self._thresholds = threading.Lock(), {}, dict(thresholds)
        self.provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False,
            resource=Resource({'service.name': 'sentinelops-risk-worker', 'service.namespace': 'sentinelops-control'}))
        meter = self.provider.get_meter('sentinelops.workers.risk', '1')
        for name, callback in (('score', self._score), ('state', self._state), ('episode_active', self._active),
                               ('episode_started_unixtime', self._started),
                               ('last_scored_unixtime', self._scored), ('threshold', self._threshold)):
            meter.create_observable_gauge('sentinelops_risk_' + name, callbacks=[callback])

    @classmethod
    def otlp(cls, endpoint, *, thresholds):
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True, timeout=1,
                                      headers={'x-sentinelops-component': 'risk-worker'})
        return cls(PeriodicExportingMetricReader(exporter, export_interval_millis=5000,
                                                 export_timeout_millis=2000), thresholds=thresholds)

    def publish(self, service, result, episode, *, scored_at):
        with self._lock:
            self._values[service] = {'state': result['state'], 'score': result.get('risk_score'),
                                     'active': bool(episode['active']), 'started_at': episode.get('started_at'),
                                     'scored_at': float(scored_at)}

    def _each(self, select):
        with self._lock:
            items = list(self._values.items())
        return [observation for service, value in items for observation in select(service, value)]

    def _score(self, _options):
        return self._each(lambda s, v: [] if v['score'] is None else [Observation(v['score'], {'service': s})])

    def _state(self, _options):
        return self._each(lambda s, v: [Observation(int(v['state'] == state), {'service': s, 'state': state})
                                        for state in STATES])

    def _active(self, _options):
        return self._each(lambda s, v: [Observation(int(v['active']), {'service': s})])

    def _started(self, _options):
        return self._each(lambda s, v: [] if not v['active'] or not v['started_at'] else
                          [Observation(utc_time(v['started_at']).timestamp(), {'service': s})])

    def _scored(self, _options):
        return self._each(lambda s, v: [] if v['score'] is None else [Observation(v['scored_at'], {'service': s})])

    def _threshold(self, _options):
        return [Observation(value, {'service': service, 'level': level})
                for service, levels in sorted(self._thresholds.items()) for level, value in sorted(levels.items())]


class RiskWorker:
    def __init__(self, pool, stream_id, scorer, tracker, metrics, *, clock=lambda: datetime.now(timezone.utc)):
        self.pool, self.stream_id, self.scorer = pool, stream_id, scorer
        self.tracker, self.metrics, self.clock = tracker, metrics, clock
        self.last = None

    async def poll_once(self):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT bucket_end, manifest_ref FROM sentinel_control.health_batches
                WHERE stream_id=$1 ORDER BY bucket_end DESC LIMIT 1""", self.stream_id)
        if row is None or (self.last is not None and row['bucket_end'] <= self.last):
            return None
        bucket_end, now = row['bucket_end'], self.clock()
        try:
            results = await asyncio.to_thread(self.scorer.score, row['manifest_ref'], now=now)
        except Exception as exc:
            # Scoring problems leave the warning unavailable; they never stop the worker.
            logger.warning('risk scoring unavailable: %s', type(exc).__name__)
            results = {service: _unavailable(timestamp(bucket_end)) for service in SERVICES}
        for service in SERVICES:
            result = results.get(service) or _unavailable(timestamp(bucket_end))
            episode = self.tracker.update(service, bucket_end, result['state'])
            if episode['event']:
                logger.info('risk episode %s service=%s started_at=%s', episode['event'], service, episode['started_at'])
            self.metrics.publish(service, result, episode, scored_at=now.timestamp())
        self.last = bucket_end
        return bucket_end

    async def run(self, stop, *, poll_seconds=5.):
        while not stop.is_set():
            try:
                await self.poll_once()
            except Exception as exc:
                logger.warning('risk worker poll failed: %s', type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
