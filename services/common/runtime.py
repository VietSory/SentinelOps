from __future__ import annotations

import json
import logging
import os
import time
from contextvars import ContextVar
from uuid import uuid4

from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from services.common.observability import configure, extract_context

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="unknown")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
logger = logging.getLogger("sentinelops-testbed")


def log_event(service: str, event: str, **fields: object) -> None:
    span = trace.get_current_span()
    span_context = span.get_span_context()
    trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else None
    span_id = format(span_context.span_id, "016x") if span_context.is_valid else None
    payload = {
        "timestamp_unix_ms": int(time.time() * 1000),
        "service": service,
        "event": event,
        "correlation_id": correlation_id_var.get(),
        "trace_id": trace_id,
        "span_id": span_id,
        **fields,
    }
    logger.info(json.dumps(payload, default=str, separators=(",", ":")))


def install_request_context(app: FastAPI, service_name: str) -> None:
    tracer, meter = configure(service_name)
    request_counter = meter.create_counter(
        "sentinelops_http_requests",
        unit="{request}",
        description="HTTP requests handled by the testbed service",
    )
    request_duration = meter.create_histogram(
        "sentinelops_http_request_duration_ms",
        unit="ms",
        description="HTTP server request duration in milliseconds",
    )
    error_counter = meter.create_counter(
        "sentinelops_http_errors",
        unit="{error}",
        description="HTTP responses with status code >= 500",
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
        correlation_token = correlation_id_var.set(correlation_id)
        parent_context = extract_context(request.headers)
        started = time.perf_counter()
        status_code = 500
        route_template = request.url.path
        try:
            with tracer.start_as_current_span(
                f"{service_name} {request.method}",
                context=parent_context,
                kind=SpanKind.SERVER,
                attributes={
                    "service.name": service_name,
                    "http.request.method": request.method,
                    "url.path": request.url.path,
                    "sentinelops.correlation_id": correlation_id,
                },
            ) as span:
                try:
                    log_event(service_name, "request_started", method=request.method, path=request.url.path)
                    response = await call_next(request)
                    status_code = response.status_code
                    response.headers["x-correlation-id"] = correlation_id
                    route = request.scope.get("route")
                    if route is not None and getattr(route, "path", None):
                        route_template = route.path
                    span.set_attribute("http.response.status_code", status_code)
                    span.set_attribute("http.route", route_template)
                    return response
                finally:
                    duration_ms = round((time.perf_counter() - started) * 1000, 2)
                    attrs = {
                        "service": service_name,
                        "method": request.method,
                        "route": route_template,
                        "status_code": status_code,
                    }
                    request_counter.add(1, attrs)
                    request_duration.record(duration_ms, attrs)
                    if status_code >= 500:
                        error_counter.add(1, attrs)
                    log_event(
                        service_name,
                        "request_completed",
                        method=request.method,
                        path=request.url.path,
                        route=route_template,
                        status_code=status_code,
                        duration_ms=duration_ms,
                    )
        finally:
            correlation_id_var.reset(correlation_token)
