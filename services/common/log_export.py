"""Opt-in delivery of business events, independent of Python/root logger handlers.

Stdout remains the local source. Only explicit log_event calls enter this bounded,
best-effort OTLP queue; exporter diagnostics cannot recursively export themselves.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

_sink: BusinessLogSink | None = None
_diagnostics = logging.getLogger(__name__)


class BusinessLogSink:
    def __init__(self, resource: Resource, endpoint: str):
        self.provider = LoggerProvider(resource=resource)
        self.provider.add_log_record_processor(BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=endpoint, insecure=endpoint.startswith('http://'), timeout=2),
            max_queue_size=512, max_export_batch_size=64, schedule_delay_millis=1000,
            export_timeout_millis=2000,
        ))
        self.logger = self.provider.get_logger('sentinelops.business', '0.2.1')

    def emit(self, payload: dict, body: str) -> None:
        self.logger.emit(
            timestamp=payload['timestamp_unix_ms'] * 1_000_000,
            severity_number=SeverityNumber.INFO, severity_text='INFO',
            body=body, event_name=payload['event'],
            attributes={'service': payload['service'], 'event': payload['event']},
        )


def configure_logs(resource: Resource) -> None:
    global _sink
    enabled = os.getenv('SENTINELOPS_OTLP_LOGS_ENABLED', 'false')
    if enabled not in ('true', 'false'):
        raise ValueError('SENTINELOPS_OTLP_LOGS_ENABLED must be true or false')
    if enabled == 'false' or _sink is not None:
        return
    endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', '')
    try:
        parts = urlsplit(endpoint)
        valid = (parts.scheme in ('http', 'https') and parts.hostname and parts.port
                 and parts.username is None and parts.password is None
                 and parts.path in ('', '/') and not parts.query and not parts.fragment
                 and not any(c.isspace() for c in endpoint))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('OTLP logs require an explicit credential-free HTTP(S) endpoint with a port')
    _sink = BusinessLogSink(resource, endpoint.rstrip('/'))


def export_event(payload: dict, body: str) -> None:
    if _sink is None:
        return
    try:
        _sink.emit(payload, body)
    except Exception:
        # Logging failure must not turn a successful business operation into 500.
        # Do not include exception text, which may contain transport credentials.
        _diagnostics.warning('business OTLP log enqueue failed; stdout event retained')
