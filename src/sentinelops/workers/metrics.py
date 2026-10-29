"""Bounded worker telemetry; observed source availability is not business health."""
from __future__ import annotations

import math
import re
import threading
import time

from grpc import Compression
from opentelemetry.metrics import NoOpMeterProvider, Observation
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.telemetry.core import BUSINESS_ROUTES
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import local_endpoint

ENVIRONMENTS = frozenset({"local", "test", "staging", "production"})
ERROR_COMPONENTS = frozenset({"source", "database", "artifact", "configuration", "runtime"})


class WorkerMetrics:
    """A private provider and fixed-cardinality state for one stable replica slot."""

    service_name = "sentinelops-health-worker"
    meter_name = "sentinelops.workers.health"
    component_name = "health-worker"

    def __init__(self, reader, *, environment: str, instance: str, clock=time.time):
        self.validate_labels(environment, instance)
        self._clock, self._lock = clock, threading.Lock()
        self._environment = environment
        self._attributes = {"worker_instance": instance, "environment": environment}
        self._ready, self._heartbeat, self._bucket_end = False, None, None
        self._source_available: dict[str, int] = {}
        self._closed = False
        self.provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False,
            resource=Resource({"service.name": self.service_name,
                               "service.namespace": "sentinelops-control", "deployment.environment": environment}),
            views=[View(instrument_name="sentinelops_worker_processing_duration",
                        aggregation=ExplicitBucketHistogramAggregation(boundaries=(.01, .05, .1, .25, .5, 1., 2., 5., 10., 20.)))])
        meter = self.provider.get_meter(self.meter_name, "1")
        for name, kind, unit in (
            ("sentinelops_worker_ready", "ready", ""),
            ("sentinelops_worker_heartbeat_unixtime", "heartbeat", "s"),
            ("sentinelops_worker_bucket_age", "age", "s"),
            ("sentinelops_worker_source_available", "source", ""),
            ("sentinelops_worker_has_local_decision", "has_decision", ""),
            ("sentinelops_worker_bucket_end_unixtime", "end", "s"),
        ):
            meter.create_observable_gauge(name, callbacks=[self._callback(kind)], unit=unit)
        self._committed = meter.create_counter("sentinelops_worker_committed_windows")
        self._busy = meter.create_counter("sentinelops_worker_busy_polls")
        self._errors = meter.create_counter("sentinelops_worker_errors")
        self._unknown = meter.create_counter("sentinelops_worker_unknown_decisions")
        self._duration = meter.create_histogram("sentinelops_worker_processing_duration", unit="s")
        # Explicit known-zero process counters reset a reused Collector slot.
        # A histogram must not receive a fabricated zero-duration observation.
        self._committed.add(0, self._attributes)
        self._busy.add(0, self._attributes)
        for component in sorted(ERROR_COMPONENTS):
            self._errors.add(0, {**self._attributes, "component": component})
        for service in sorted(BUSINESS_ROUTES):
            self._unknown.add(0, {**self._attributes, "business_service": service})

    @staticmethod
    def validate_labels(environment: str, instance: str) -> None:
        if environment not in ENVIRONMENTS or not re.fullmatch(r"health-(?:[0-9]|1[0-5])", instance):
            raise ValueError("worker metrics require an approved environment and stable health-0..health-15 slot")

    @classmethod
    def otlp(cls, endpoint: str, *, environment: str, instance: str):
        cls.validate_labels(environment, instance)
        endpoint = local_endpoint(endpoint)
        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True, timeout=1.,
            # Empty headers fall back to OTEL_*_HEADERS in the pinned SDK.
            # A truthy noncredential allowlist prevents ambient token forwarding.
            headers={"x-sentinelops-component": cls.component_name}, compression=Compression.NoCompression,
            channel_options=(("grpc.enable_http_proxy", 0),),
            meter_provider=NoOpMeterProvider())
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=2000, export_timeout_millis=1000)
        return cls(reader, environment=environment, instance=instance)

    def _callback(self, kind):
        def observe(_options):
            with self._lock:
                if kind == "ready":
                    return [Observation(int(self._ready), self._attributes)]
                if kind == "heartbeat":
                    return [] if self._heartbeat is None else [Observation(self._heartbeat, self._attributes)]
                if kind == "has_decision":
                    return [Observation(int(self._bucket_end is not None), self._attributes)]
                if kind == "end":
                    return [] if self._bucket_end is None else [Observation(self._bucket_end, self._attributes)]
                if kind == "source":
                    return [Observation(value, {**self._attributes, "business_service": service})
                            for service, value in sorted(self._source_available.items())]
                age = None if self._bucket_end is None else self._clock() - self._bucket_end
                return [] if age is None or not math.isfinite(age) or age < 0 else [Observation(age, self._attributes)]
        return observe

    def heartbeat(self, *, ready: bool) -> None:
        """Called by the main loop; exporter collections cannot refresh liveness."""
        now = self._clock()
        if not math.isfinite(now) or now < 0:
            raise ValueError("worker heartbeat requires a finite actual clock")
        with self._lock:
            self._heartbeat, self._ready = now, ready

    def error(self, component: str) -> None:
        if component not in ERROR_COMPONENTS:
            raise ValueError("worker metric error component is not in the fixed taxonomy")
        with self._lock:
            self._ready = False
        self._errors.add(1, {**self._attributes, "component": component})

    def observe_result(self, result: dict, store: ArtifactStore) -> None:
        if result["status"] == "BUSY":
            self._busy.add(1, self._attributes)
            return
        if result["status"] != "COMMITTED":
            return
        duration = result["processing_ms"]
        if isinstance(duration, bool) or not math.isfinite(duration) or duration < 0:
            raise ValueError("worker processing metric requires a nonnegative finite duration")
        end = utc_time(result["bucket_end"]).timestamp()
        batch = store.read(result["manifest_ref"])
        if (batch["stream_id"] != result["stream_id"] or batch["bucket_end"] != result["bucket_end"]
            or batch["identity"]["environment"] != self._environment):
            raise ValueError("worker metric batch belongs to a different accepted decision")
        signals = [store.read(ref) for ref in batch["signal_refs"]]
        available = {}
        for signal in signals:
            validate_payload("health-signal.schema.json", signal)
            if (signal["bucket_end"] != result["bucket_end"] or signal["service"] in available
                or signal["environment"] != self._environment):
                raise ValueError("worker metric signals mix or duplicate decisions")
            available[signal["service"]] = int(signal["availability"] == "ELIGIBLE")
        if set(available) != set(BUSINESS_ROUTES) or len(signals) != len(BUSINESS_ROUTES):
            raise ValueError("worker metric decision omits a business service")
        with self._lock:
            self._bucket_end, self._source_available = end, available
        self._committed.add(1, self._attributes)
        self._duration.record(duration / 1000, self._attributes)
        for service, value in available.items():
            if not value:
                self._unknown.add(1, {**self._attributes, "business_service": service})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            self._ready = False
        try:
            self.provider.force_flush(timeout_millis=1000)
        finally:
            self.provider.shutdown(timeout_millis=2000)


class IncidentWorkerMetrics(WorkerMetrics):
    """Same guarded liveness/source gauges, separate service and replica slots."""

    service_name = "sentinelops-incident-worker"
    meter_name = "sentinelops.workers.incidents"
    component_name = "incident-worker"

    def __init__(self, reader, **kwargs):
        super().__init__(reader, **kwargs)
        meter = self.provider.get_meter(self.meter_name, "1")
        self._incidents = meter.create_counter("sentinelops_incident_groups")
        self._abstentions = meter.create_counter("sentinelops_rca_abstentions")
        self._incidents.add(0, self._attributes)
        self._abstentions.add(0, self._attributes)

    @staticmethod
    def validate_labels(environment, instance):
        if environment not in ENVIRONMENTS or not re.fullmatch(r"incidents-(?:[0-9]|1[0-5])", instance):
            raise ValueError("incident metrics need an approved environment and incidents-0..incidents-15 slot")

    def observe_result(self, result, store):
        if result["status"] == "BUSY":
            self._busy.add(1, self._attributes)
            return
        if result["status"] != "COMMITTED":
            return
        commit = store.read(result["commit_ref"])
        state = commit["state"]
        if (commit["consumer_id"] != result["consumer_id"] or state["last_bucket_end"] != result["bucket_end"]
            or state["source"]["health_identity"]["environment"] != self._environment):
            raise ValueError("incident metric input differs from the accepted consumer commit")
        duration = result["processing_ms"]
        if type(duration) not in (float, int) or not math.isfinite(duration) or duration < 0:
            raise ValueError("incident processing duration must be measured finite time")
        available = {service: int(signal["availability"] == "ELIGIBLE") for service, signal in state["service_states"].items()}
        if set(available) != set(BUSINESS_ROUTES):
            raise ValueError("incident metrics lack complete source availability")
        with self._lock:
            self._bucket_end = utc_time(result["bucket_end"]).timestamp()
            self._source_available = available
        self._committed.add(1, self._attributes)
        self._duration.record(duration / 1000, self._attributes)
        self._incidents.add(len(commit["results"]), self._attributes)
        self._abstentions.add(sum(row["rca"]["abstained"] for row in commit["results"]), self._attributes)
        for service, value in available.items():
            if not value:
                self._unknown.add(1, {**self._attributes, "business_service": service})
