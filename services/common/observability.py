from __future__ import annotations

import os
import re
from typing import Mapping, MutableMapping
from uuid import UUID

from opentelemetry import metrics, propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind

_CONFIGURED = False


def kubernetes_identity(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Optional Downward API metadata, not authenticated deployment authority.

    Compose remains unchanged. Kubernetes consumers must compare these attributes
    with actual API observations; a caller-controlled environment proves nothing.
    """
    env = os.environ if environ is None else environ
    fields = {'k8s.pod.uid': 'SENTINELOPS_K8S_POD_UID',
              'k8s.pod.name': 'SENTINELOPS_K8S_POD_NAME',
              'k8s.namespace.name': 'SENTINELOPS_K8S_NAMESPACE'}
    if not any(key in env for key in fields.values()):
        return {}
    if any(not env.get(key) for key in fields.values()):
        raise ValueError('incomplete Kubernetes identity environment')
    result = {key: env[value] for key, value in fields.items()}
    try:
        if str(UUID(result['k8s.pod.uid'])) != result['k8s.pod.uid']:
            raise ValueError()
        for key in ('k8s.pod.name', 'k8s.namespace.name'):
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', result[key]):
                raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid Kubernetes identity environment') from None
    return result


def duration_histogram_views() -> list[View]:
    # Preserve original boundaries and add resolution around the testbed SLO/fault.
    boundaries = (0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000,
                  1250, 1500, 2000, 2500, 4000, 5000, 7500, 10000)
    return [View(instrument_name="sentinelops_http_request_duration_ms",
                 aggregation=ExplicitBucketHistogramAggregation(boundaries=boundaries))]


def configure(service_name: str) -> tuple[trace.Tracer, metrics.Meter]:
    global _CONFIGURED
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    identity = kubernetes_identity()
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.getenv("SERVICE_VERSION", "0.2.1"),
            "service.namespace": "sentinelops",
            "deployment.environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
            **identity,
            **({'service.instance.id': identity['k8s.pod.uid']} if identity else {}),
        }
    )

    if not _CONFIGURED:
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))
            )
        )
        trace.set_tracer_provider(tracer_provider)

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=endpoint.startswith("http://")),
            # Keep delivery lag small compared with the 5 s Prometheus scrape.
            export_interval_millis=1000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader], views=duration_histogram_views())
        metrics.set_meter_provider(meter_provider)
        _CONFIGURED = True

    tracer = trace.get_tracer("sentinelops.runtime", "0.2.1")
    meter = metrics.get_meter("sentinelops.runtime", "0.2.1")
    return tracer, meter


def inject_trace_headers(headers: MutableMapping[str, str]) -> None:
    propagate.inject(headers)


def extract_context(headers: Mapping[str, str]):
    return propagate.extract(dict(headers))


def new_client_span(tracer: trace.Tracer, dependency: str, method: str):
    return tracer.start_as_current_span(
        f"{dependency} {method}",
        kind=SpanKind.CLIENT,
        attributes={
            "peer.service": dependency,
            "http.request.method": method,
        },
    )


def new_database_span(tracer: trace.Tracer, operation: str):
    return tracer.start_as_current_span(
        f"postgresql {operation}", kind=SpanKind.CLIENT,
        attributes={"peer.service": "rds", "db.system.name": "postgresql", "db.operation.name": operation},
    )
