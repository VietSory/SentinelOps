# Step 3 — OpenTelemetry Observability Baseline

## Goal

Instrument the three business services so a single request produces correlated:

- metrics
- structured logs
- distributed traces

Local development uses the pinned upstream OpenTelemetry Collector and Jaeger only as development sinks. The Collector image supports the configured Prometheus exporter and health-check extension. The AWS target remains ADOT with Amazon CloudWatch Metrics/Logs and AWS X-Ray as the canonical observability destinations.

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
Prometheus may append unit/type suffixes, for example `_total` for a counter and
`_bucket`, `_sum`, and `_count` for the duration histogram. These are exported
names; the instrumentation names above remain stable.

Dimensions are `service`, `method`, `route` and `status_code`. Parameterized routes
use their template, such as `/v1/inventory/{sku}`. Unknown URLs share the route
`__unmatched__` so arbitrary paths do not create new metric series. Correlation
and trace IDs are kept in logs/spans, never in metric labels.

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
├── inventory-service (CLIENT, emitted by Checkout)
│   └── inventory-service (SERVER)
└── payment-service (CLIENT, emitted by Checkout)
    └── payment-service (SERVER)
```

Checkout injects W3C headers **inside** each active CLIENT span, so the downstream
SERVER span has that CLIENT span as its parent. All spans share the same trace ID.
Responses include `x-correlation-id` and `x-trace-id`. Server spans with HTTP 5xx
and client spans with downstream errors/timeouts carry an error status.

`/health` is a liveness probe. `/ready` checks database availability for Payment
and Inventory; Checkout checks both downstream `/ready` endpoints.

## Run and verify

From the repository root, with the project's virtual environment activated:

```bash
pip install -e '.[dev]'
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 120
python scripts/step2_smoke_test.py
python scripts/step3_smoke_test.py
pytest -q
```

Rebuilding is necessary after changing service code: the containers copy source
at build time. This command preserves the database volume.

- Jaeger UI: http://127.0.0.1:16686
- Collector health: http://127.0.0.1:13133
- Metrics: http://127.0.0.1:8889/metrics
- Checkout: http://127.0.0.1:8001

The Step 3 smoke test runs healthy, Payment latency, Payment error and recovery
requests. Each uses a fresh correlation ID and sampled W3C trace ID. The test
reads each exact trace through Jaeger's UI JSON query API
(`/api/traces/{trace_id}`), checks SERVER -> CLIENT -> SERVER parentage, compares
JSON logs against those server span IDs, and checks that business-route request,
duration and error counts have increased since its initial metrics snapshot.
The service registry is checked through `/api/services`. Collector export and
Jaeger indexing are polled with bounded timeouts and diagnostic error messages.

The test temporarily controls Payment and Inventory faults and restores both
original configurations in `finally`, including when a scenario fails. Run it
against the local sandbox without a concurrent fault experiment. Step 2 also
restores its original Payment fault configuration.

Logs stay on the services' stdout/stderr; there is no Collector log-export
pipeline in this milestone. The smoke test reads them with `docker compose logs`,
so it needs access to the Docker daemon as well as the local HTTP ports.

## Automated checks

- In-memory SDK tests exercise the real service handlers, trace propagation,
  metrics, structured logs, concurrent requests, timeouts and unhandled errors.
  Database operations use a test double in these tests.
- Smoke-validator tests reject incomplete/unrelated traces, wrong parentage,
  unrelated logs and stale metrics.
- CI's `observability` job builds the real Docker testbed and runs both smoke
  tests against PostgreSQL, Collector and Jaeger. Failure logs are collected
  before the CI stack is removed.

## Troubleshooting

```bash
docker compose ps --all
docker compose logs --no-color --tail 100 otel-collector jaeger
docker compose logs --no-color --tail 100 checkout-service payment-service inventory-service
```

- An unknown `prometheus` exporter or `health_check` extension indicates a
  Collector distribution/configuration mismatch; use the pinned Compose image.
- A trace-parent mismatch requires checking header injection inside the client
  span, not only whether all three service names are present.
- Missing metrics can indicate exporter/Collector startup errors; allow for the
  SDK's 2-second export interval and the Collector's batching delay.
- The smoke test expects Jaeger's UI query API. A 404 after polling should prompt
  checking the running image/configuration and rebuilding the current Compose
  stack. It does not depend on a derived dependency graph or v3 search streaming.

## Step 2 correction

The host-side HTTPX smoke test now uses `trust_env=False` so local requests are not routed through an unrelated proxy configuration.

## Definition of Done

- Collector health endpoint responds.
- Jaeger UI responds.
- Checkout still works.
- Custom metrics appear on Collector port 8889.
- Jaeger lists all three business services after a checkout.
- Every tested trace has correct SERVER -> CLIENT -> SERVER parentage.
- Logs contain matching correlation IDs, trace IDs and server span IDs.
- HTTP 5xx is visible in traces and error counters.
- Metric counters and histogram counts increase for the tested business routes.
- Existing Step 2 fault scenarios remain reproducible.
- Both smoke tests pass and fault configurations are restored.
