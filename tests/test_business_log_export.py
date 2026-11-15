"""Software transport checks against a real loopback OTLP/gRPC receiver; no AWS."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import subprocess
import sys
from threading import Event

import grpc
import pytest
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs.export import LogRecordExportResult
from opentelemetry.sdk.trace import TracerProvider

from services.common import log_export, runtime


@pytest.fixture
def receiver():
    class Receiver(logs_service_pb2_grpc.LogsServiceServicer):
        def __init__(self):
            self.requests = []
            self.unavailable = False
            self.attempted = Event()

        def Export(self, request, context):
            self.attempted.set()
            if self.unavailable:
                context.abort(grpc.StatusCode.UNAVAILABLE, 'isolated receiver outage')
            self.requests.append(request)
            return logs_service_pb2.ExportLogsServiceResponse()

    endpoint = Receiver()
    pool = ThreadPoolExecutor(max_workers=2)
    server = grpc.server(pool)
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(endpoint, server)
    try:
        port = server.add_insecure_port('127.0.0.1:0')
        assert port > 0
        server.start()
        yield endpoint, f'http://127.0.0.1:{port}'
    finally:
        server.stop(0).wait(5)
        pool.shutdown(wait=True)


@pytest.fixture
def resource():
    return Resource.create({'service.name': 'payment-service',
        'service.version': 'software-fixture', 'deployment.environment': 'dev',
        'k8s.pod.uid': '12345678-1234-1234-1234-123456789abc',
        'k8s.pod.name': 'payment-fixture', 'k8s.namespace.name': 'sentinelops'})


def test_business_event_arrives_as_otlp_with_exact_stdout_body_context_and_resource(
        receiver, resource, monkeypatch, caplog):
    received, endpoint = receiver
    monkeypatch.setattr(log_export, '_sink', None)
    monkeypatch.setenv('SENTINELOPS_OTLP_LOGS_ENABLED', 'true')
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', endpoint)
    for suffix, value in [('POD_UID', resource.attributes['k8s.pod.uid']),
                          ('POD_NAME', 'payment-fixture'), ('NAMESPACE', 'sentinelops')]:
        monkeypatch.setenv('SENTINELOPS_K8S_' + suffix, value)
    log_export.configure_logs(resource)
    sink = log_export._sink
    log_export.configure_logs(resource)
    assert log_export._sink is sink  # Repeated configure never duplicates delivery.
    tracer = TracerProvider()
    correlation = runtime.correlation_id_var.set('correlation-fixture')
    instance = runtime.request_instance_id_var.set('request-fixture')
    try:
        with caplog.at_level(logging.INFO), tracer.get_tracer('software-check').start_as_current_span('payment') as span:
            runtime.log_event('payment-service', 'payment_approved', order_id='order-fixture')
            context = span.get_span_context()
        assert sink.provider.force_flush(timeout_millis=5000)
        assert len(received.requests) == 1
        (resource_logs,) = received.requests[0].resource_logs
        attrs = {a.key: a.value.string_value for a in resource_logs.resource.attributes}
        assert all(attrs[key] == value for key, value in resource.attributes.items())
        (scope_logs,) = resource_logs.scope_logs
        (record,) = scope_logs.log_records
        stdout = [r.message for r in caplog.records if r.name == 'sentinelops-testbed']
        assert stdout == [record.body.string_value]
        payload = json.loads(record.body.string_value)
        assert payload['event'] == 'payment_approved' and payload['order_id'] == 'order-fixture'
        assert payload['correlation_id'] == 'correlation-fixture'
        assert payload['request_instance_id'] == 'request-fixture'
        assert payload['k8s.pod.uid'] == attrs['k8s.pod.uid']
        assert record.trace_id.hex() == payload['trace_id'] == f'{context.trace_id:032x}'
        assert record.span_id.hex() == payload['span_id'] == f'{context.span_id:016x}'
        assert record.time_unix_nano == payload['timestamp_unix_ms'] * 1_000_000
        assert record.severity_text == 'INFO'
    finally:
        runtime.correlation_id_var.reset(correlation)
        runtime.request_instance_id_var.reset(instance)
        sink.provider.shutdown()
        tracer.shutdown()


def test_receiver_outage_keeps_stdout_and_later_events_export_without_recursive_diagnostics(
        receiver, resource, monkeypatch, caplog):
    received, endpoint = receiver
    sink = log_export.BusinessLogSink(resource, endpoint)
    monkeypatch.setattr(log_export, '_sink', sink)
    try:
        received.unavailable = True
        with caplog.at_level(logging.INFO):
            runtime.log_event('payment-service', 'during_outage')
            # Drain is not an acknowledgement of delivery; exporter may drop.
            sink.provider.force_flush(timeout_millis=5000)
            assert received.attempted.is_set() and received.requests == []
            received.unavailable = False
            runtime.log_event('payment-service', 'after_recovery')
            sink.provider.force_flush(timeout_millis=5000)
        bodies = [json.loads(record.body.string_value) for request in received.requests
                  for resource_logs in request.resource_logs for scope in resource_logs.scope_logs
                  for record in scope.log_records]
        assert [body['event'] for body in bodies] == ['after_recovery']
        assert [json.loads(r.message)['event'] for r in caplog.records
                if r.name == 'sentinelops-testbed'] == ['during_outage', 'after_recovery']
    finally:
        sink.provider.shutdown()


def test_request_middleware_configures_and_exports_real_business_events(receiver):
    received, endpoint = receiver
    # Separate process exercises the real one-time SDK setup without replacing
    # global trace/meter providers used by other application tests.
    script = '''
import asyncio
import httpx
from fastapi import FastAPI
from opentelemetry import metrics, trace
from services.common.runtime import install_request_context
from services.common import log_export
app = FastAPI()
install_request_context(app, 'checkout-service')
@app.get('/business-check')
async def business():
    return {'ok': True}
async def check():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/business-check')
        assert response.status_code == 200
asyncio.run(check())
assert log_export._sink.provider.force_flush(timeout_millis=5000)
log_export._sink.provider.shutdown()
metrics.get_meter_provider().shutdown()
trace.get_tracer_provider().shutdown()
'''
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('OTEL_', 'SENTINELOPS_K8S_'))}
    env.update(SENTINELOPS_OTLP_LOGS_ENABLED='true', OTEL_EXPORTER_OTLP_ENDPOINT=endpoint)
    result = subprocess.run([sys.executable, '-c', script], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    events = [json.loads(record.body.string_value) for request in received.requests
              for resource_logs in request.resource_logs for scope in resource_logs.scope_logs
              for record in scope.log_records]
    assert [event['event'] for event in events] == ['request_started', 'request_completed']
    assert all(event['service'] == 'checkout-service' and event['trace_id'] for event in events)
    assert events[1]['route'] == '/business-check' and events[1]['status_code'] == 200


@pytest.mark.parametrize('value', [None, 'false'])
def test_default_does_not_construct_exporter(value, resource, monkeypatch):
    monkeypatch.setattr(log_export, '_sink', None)
    monkeypatch.delenv('SENTINELOPS_OTLP_LOGS_ENABLED', raising=False)
    if value:
        monkeypatch.setenv('SENTINELOPS_OTLP_LOGS_ENABLED', value)
    def forbidden(*args, **kwargs):
        raise AssertionError('default must not open a log exporter')
    monkeypatch.setattr(log_export, 'BusinessLogSink', forbidden)
    log_export.configure_logs(resource)
    runtime.log_event('payment-service', 'stdout_only')
    assert log_export._sink is None


@pytest.mark.parametrize('flag,endpoint', [
    ('yes', 'http://collector:4317'), ('TRUE', 'http://collector:4317'),
    ('true', ''), ('true', 'http://user:secret@collector:4317'),
    ('true', 'http://collector:4317?token=secret'), ('true', 'http://collector:4317/logs'),
    ('true', 'http://collector'), ('true', 'http://collector:99999'),
])
def test_bad_opt_in_configuration_fails_before_constructing_sink(flag, endpoint, resource, monkeypatch):
    monkeypatch.setattr(log_export, '_sink', None)
    monkeypatch.setenv('SENTINELOPS_OTLP_LOGS_ENABLED', flag)
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', endpoint)
    with pytest.raises(ValueError):
        log_export.configure_logs(resource)
    assert log_export._sink is None


def test_enqueue_failure_does_not_fail_business_operation_or_leak_exception(monkeypatch, caplog):
    class BrokenSink:
        def emit(self, *args):
            raise RuntimeError('credential=do-not-retain')
    monkeypatch.setattr(log_export, '_sink', BrokenSink())
    with caplog.at_level(logging.INFO):
        runtime.log_event('payment-service', 'already_committed')
    assert 'already_committed' in caplog.text and 'stdout event retained' in caplog.text
    assert 'do-not-retain' not in caplog.text


def test_slow_export_runs_off_the_business_call_thread(resource, monkeypatch):
    entered, release = Event(), Event()
    class SlowExporter:
        def export(self, batch):
            entered.set()
            release.wait(10)
            return LogRecordExportResult.SUCCESS
        def shutdown(self):
            pass
    monkeypatch.setattr(log_export, 'OTLPLogExporter', lambda **kwargs: SlowExporter())
    sink = log_export.BusinessLogSink(resource, 'http://collector.invalid:4317')
    monkeypatch.setattr(log_export, '_sink', sink)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        call = pool.submit(runtime.log_event, 'payment-service', 'slow_receiver')
        assert entered.wait(5)
        call.result(timeout=1)  # Must complete while exporter is still blocked.
        assert not release.is_set()
    finally:
        release.set()
        sink.provider.shutdown()
        pool.shutdown(wait=True)
