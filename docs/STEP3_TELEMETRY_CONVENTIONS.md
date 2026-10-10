# Step 3 Telemetry Conventions

This document defines the local observability conventions introduced in Step 3. It does not replace the frozen product contracts.

## Signals

- Metrics: request count, request duration, and HTTP 5xx count.
- Logs: JSON on stdout with timestamp, service, event, correlation_id, trace_id, span_id, plus event fields.
- Traces: OpenTelemetry spans exported over OTLP to the local Collector and then to Jaeger.

## Correlation

`x-correlation-id` is propagated across Checkout -> Inventory and Checkout -> Payment.
OpenTelemetry trace context is propagated alongside it using standard W3C Trace Context headers.
Outbound headers are injected inside the active CLIENT span. The downstream
SERVER span must be a child of that span, with the same trace ID.
Responses include `x-correlation-id` and `x-trace-id` for request lookup.

## Canonical local metric names

- `sentinelops_http_requests`
- `sentinelops_http_request_duration_ms`
- `sentinelops_http_errors`

Metric dimensions are `service`, `method`, `route` and `status_code`.
`route` is a template, not a concrete SKU/path; unmatched paths use
`__unmatched__`. Trace/correlation IDs never become metric dimensions.
The Prometheus exporter may append unit/type suffixes to these instrument names.

HTTP 5xx sets an error status on SERVER spans and increments the error counter.
Downstream rejection, transport failure and timeout are recorded on CLIENT spans.

These are intentionally small baseline signals. Step 4 will derive ML feature windows from a wider, contract-aligned telemetry dataset.

## Boundaries

Step 3 does not perform anomaly detection, degradation prediction, RCA, LLM explanation, or remediation. It only establishes observable, reproducible telemetry for the existing testbed.
