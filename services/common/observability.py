from __future__ import annotations

import os
from typing import Mapping, MutableMapping

from opentelemetry import metrics, propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind

_CONFIGURED = False


def configure(service_name: str) -> tuple[trace.Tracer, metrics.Meter]:
    global _CONFIGURED
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.getenv("SERVICE_VERSION", "0.2.0"),
            "service.namespace": "sentinelops",
            "deployment.environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
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
            export_interval_millis=2000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
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
