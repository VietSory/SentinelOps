from __future__ import annotations

import json
import logging
import os
import time
from contextvars import ContextVar
from uuid import uuid4

from fastapi import FastAPI, Request

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="unknown")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
logger = logging.getLogger("sentinelops-testbed")


def log_event(service: str, event: str, **fields: object) -> None:
    payload = {
        "timestamp_unix_ms": int(time.time() * 1000),
        "service": service,
        "event": event,
        "correlation_id": correlation_id_var.get(),
        **fields,
    }
    logger.info(json.dumps(payload, default=str, separators=(",", ":")))


def install_request_context(app: FastAPI, service_name: str) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
        token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        status_code = 500
        try:
            log_event(service_name, "request_started", method=request.method, path=request.url.path)
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-correlation-id"] = correlation_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                service_name,
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            correlation_id_var.reset(token)
