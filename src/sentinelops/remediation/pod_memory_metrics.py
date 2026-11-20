"""Optional observation-only metrics for the single identity-bound OOM demo.

No metric authorizes a decision, invents business HTTP traffic, or changes the
controller. UID/event keys stay internal; the exported label surface is fixed.
"""
from collections import OrderedDict
from dataclasses import asdict
import math
import threading
import time
from urllib.parse import urlsplit

from grpc import Compression
from opentelemetry.metrics import NoOpMeterProvider, Observation
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from sentinelops.contracts import utc_time
from sentinelops.remediation.pod_memory import AFTER, BEFORE, MemoryTarget, TERMINAL

FRESH_SECONDS = 20
SEEN_EVENTS = 128
COLLECTOR = 'otel-collector.sentinelops-b-actions.svc.cluster.local'
PHASES = ('observing', 'waiting', 'pending', 'dispatching', 'verifying',
          'recovered', 'blocked', 'escalated', 'rejected', 'expired', 'unavailable')
PREFIX = 'sentinelops_oom_demo_'
# Actual Collector Prometheus names, including unit/counter suffix conversion.
NAMES = {
    'limit': PREFIX + 'memory_limit_bytes', 'ready': PREFIX + 'pod_ready',
    'restarts': PREFIX + 'restarts', 'events': PREFIX + 'oom_observed_events_total',
    'phase': PREFIX + 'phase', 'available': PREFIX + 'observation_available',
    'observed': PREFIX + 'last_observed_unixtime_seconds',
    'terminal': PREFIX + 'last_terminal_phase',
    'terminal_at': PREFIX + 'last_terminal_unixtime_seconds',
}
EXPORTED_METRIC_NAMES = frozenset(NAMES.values())


def metrics_endpoint(value):
    """Only local tests or the existing in-cluster Prometheus Collector path."""
    if not isinstance(value, str):
        raise ValueError('OOM metrics require an explicit allowlisted OTLP endpoint')
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('OOM metrics endpoint is not allowlisted') from None
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', COLLECTOR)
        or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
        or parsed.path not in ('', '/') or any(c.isspace() or ord(c) < 32 for c in value)
        or port is not None and not 1 <= port <= 65535
        or parsed.hostname == COLLECTOR and port != 4317):
        raise ValueError('OOM metrics endpoint is not allowlisted')
    return value.rstrip('/')


class PodMemoryMetrics:
    def __init__(self, reader, *, target: MemoryTarget, clock=time.time):
        if not isinstance(target, MemoryTarget):
            raise ValueError('OOM metrics require the validated bounded target')
        self._target = asdict(target)
        self._attributes = {'namespace': target.namespace, 'workload': target.deployment,
                            'container': target.container}
        self._clock, self._lock = clock, threading.Lock()
        self._values, self._observed = None, None
        self._available, self._closed = False, False
        self._phase, self._phase_at = None, None
        self._terminal, self._terminal_at = None, None
        self._seen = OrderedDict()
        self.provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False,
            resource=Resource({'service.name': 'sentinelops-oom-demo-controller',
                               'service.namespace': 'sentinelops-control'}))
        meter = self.provider.get_meter('sentinelops.remediation.pod_memory', '1')
        for name, kind, unit in (
            ('memory_limit', 'limit', 'By'), ('pod_ready', 'ready', ''), ('restarts', 'restarts', ''),
            ('observation_available', 'available', ''), ('last_observed_unixtime', 'observed', 's'),
            ('phase', 'phase', ''), ('last_terminal_phase', 'terminal', ''),
            ('last_terminal_unixtime', 'terminal_at', 's'),
        ):
            meter.create_observable_gauge(PREFIX + name, callbacks=[self._callback(kind)], unit=unit)
        self._events = meter.create_counter(PREFIX + 'oom_observed_events')
        self._events.add(0, self._attributes)

    @classmethod
    def otlp(cls, endpoint, *, target):
        endpoint = metrics_endpoint(endpoint)
        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True, timeout=1,
            # A truthy non-secret header prevents SDK ambient-header fallback.
            headers={'x-sentinelops-component': 'oom-demo-controller'},
            compression=Compression.NoCompression, channel_options=(('grpc.enable_http_proxy', 0),),
            meter_provider=NoOpMeterProvider())
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=2000,
                                              export_timeout_millis=1000)
        return cls(reader, target=target)

    def _fresh(self, timestamp):
        age = self._clock() - timestamp if timestamp is not None else math.inf
        return math.isfinite(age) and 0 <= age <= FRESH_SECONDS

    def _callback(self, kind):
        def observe(_options):
            with self._lock:
                fresh = self._available and self._fresh(self._observed)
                if kind == 'available':
                    return [Observation(int(fresh), self._attributes)]
                if kind == 'observed':
                    return [] if self._observed is None else [Observation(self._observed, self._attributes)]
                if kind in ('phase', 'terminal'):
                    selected = self._phase if kind == 'phase' else self._terminal
                    if kind == 'phase' and not self._fresh(self._phase_at):
                        # The independent Kubernetes sampler may still be fresh
                        # while control is stalled. Explicit zeros clear a
                        # Collector's cached prior phase rather than retaining it.
                        selected = 'unavailable'
                    if selected is None:
                        return []
                    phases = PHASES if kind == 'phase' else sorted(TERMINAL)
                    return [Observation(int(phase == selected), {**self._attributes, 'phase': phase})
                            for phase in phases]
                if kind == 'terminal_at':
                    return [] if self._terminal_at is None else [Observation(self._terminal_at, self._attributes)]
                return [] if not fresh else [Observation(self._values[kind], self._attributes)]
        return observe

    def observe(self, value):
        """Accept only a real validated KubernetesMemory projection for this UID."""
        if (not isinstance(value, dict) or value.get('target') != self._target
            or value.get('memory') not in (BEFORE, AFTER) or type(value.get('ready')) is not bool
            or type(value.get('restart_count')) is not int or value['restart_count'] < 0
            or not isinstance(value.get('pod_uid'), str) or not 1 <= len(value['pod_uid']) <= 128
            or type(value.get('oom')) is not bool):
            raise ValueError('OOM metric observation is outside the bound projection')
        observed = utc_time(value['observed_at']).timestamp()
        event = None
        if value['oom']:
            finished = utc_time(value['oom_finished_at']).timestamp()
            if not math.isfinite(finished) or finished > observed:
                raise ValueError('OOM termination timestamp must precede its real observation')
            # restartCount can advance after the same terminated -> waiting
            # transition. The termination timestamp identifies the event.
            if 0 <= self._clock() - finished <= 180:
                event = (value['pod_uid'], value['oom_finished_at'])
        increment = False
        with self._lock:
            if self._observed is not None and observed < self._observed:
                raise ValueError('OOM metric observation moved backwards')
            self._observed, self._available = observed, True
            self._values = {'limit': int(value['memory'][:-2]) * 1024 * 1024,
                            'ready': int(value['ready']), 'restarts': value['restart_count']}
            if event is not None and self._fresh(observed):
                if event not in self._seen:
                    self._seen[event] = None
                    increment = True
                    if len(self._seen) > SEEN_EVENTS:
                        self._seen.popitem(last=False)
                else:
                    self._seen.move_to_end(event)
        # Collection callbacks also take _lock. Never call into the SDK while
        # holding it, or reader/exporter locks can invert the acquisition order.
        if increment:
            self._events.add(1, self._attributes)

    def unavailable(self):
        with self._lock:
            self._available = False

    def phase(self, value):
        if value not in PHASES:
            raise ValueError('OOM metric phase is not in the fixed taxonomy')
        now = self._clock()
        if not math.isfinite(now) or now < 0:
            raise ValueError('OOM metric phase requires a real finite clock')
        with self._lock:
            self._phase, self._phase_at = value, now
            if value in TERMINAL:
                self._terminal, self._terminal_at = value, now

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.unavailable()
        try:
            self.provider.force_flush(timeout_millis=1000)
        finally:
            self.provider.shutdown(timeout_millis=2000)
