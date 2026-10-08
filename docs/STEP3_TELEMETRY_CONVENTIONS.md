# Step 3 Telemetry Conventions

This document defines the local observability conventions introduced in Step 3. It does not replace the frozen product contracts.

## Signals

- Metrics: request count, request duration, and HTTP 5xx count.
- Logs: JSON on stdout with timestamp, service, event, correlation_id, trace_id, span_id, plus event fields.
- Traces: OpenTelemetry spans exported over OTLP to the local Collector and then to Jaeger.

## Correlation

`x-correlation-id` is propagated across Checkout -> Inventory and Checkout -> Payment.
OpenTelemetry trace context is propagated alongside it using standard W3C Trace Context headers.

## Canonical local metric names

- `sentinelops_http_requests`
- `sentinelops_http_request_duration_ms`
- `sentinelops_http_errors`

These are intentionally small baseline signals. Step 4 will derive ML feature windows from a wider, contract-aligned telemetry dataset.

## Boundaries

Step 3 does not perform anomaly detection, degradation prediction, RCA, LLM explanation, or remediation. It only establishes observable, reproducible telemetry for the existing testbed.
