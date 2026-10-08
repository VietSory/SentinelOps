# Step 3 — OpenTelemetry Observability Baseline

## Goal

Instrument the three business services so a single request produces correlated:

- metrics
- structured logs
- distributed traces

Local development uses an upstream OpenTelemetry Collector and Jaeger only as a development sink. The AWS target remains ADOT with Amazon CloudWatch Metrics/Logs and AWS X-Ray as the canonical observability destinations.

## What is frozen

1. OpenTelemetry is the application instrumentation standard.
2. `correlation_id` remains an application-level correlation key.
3. `trace_id` / `span_id` come from OpenTelemetry.
4. Checkout propagates W3C trace context to Payment and Inventory.
5. Metrics use low-cardinality service/route/method/status dimensions.
6. Logs remain JSON on stdout; AWS deployment can ship stdout to CloudWatch Logs.
7. No ML, RCA, Bedrock or remediation is added in this step.

## Local topology

```text
Checkout / Payment / Inventory
            |
            | OTLP gRPC :4317
            v
   OpenTelemetry Collector
        |             |
        |             +--> Prometheus-format :8889
        |
        +--> Jaeger :4317 --> Jaeger UI :16686
```

## Signals

### Metrics

- `sentinelops_http_requests`
- `sentinelops_http_request_duration_ms`
- `sentinelops_http_errors`

These are exported to the Collector and exposed in Prometheus format on port 8889.

### Logs

Each JSON log contains:

- `timestamp_unix_ms`
- `service`
- `event`
- `correlation_id`
- `trace_id`
- `span_id`
- event-specific fields

### Traces

A checkout request should result in a trace containing at minimum:

```text
checkout-service (SERVER)
├── inventory-service (CLIENT)
│   └── inventory-service (SERVER)
└── payment-service (CLIENT)
    └── payment-service (SERVER)
```

## Step 2 correction

The host-side HTTPX smoke test now uses `trust_env=False` so local requests are not routed through an unrelated proxy configuration.

## Definition of Done

- Collector health endpoint responds.
- Jaeger UI responds.
- Checkout still works.
- Custom metrics appear on Collector port 8889.
- Jaeger lists all three business services after a checkout.
- Logs contain both correlation and OpenTelemetry trace identifiers.
- Existing Step 2 fault scenarios remain reproducible.
