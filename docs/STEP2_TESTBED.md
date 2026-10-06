# Step 2 — Experimental Microservices Testbed

## Goal

Build a small but real dependency graph that SentinelOps can observe and later diagnose:

```text
Checkout
├── Inventory ──> PostgreSQL (local stand-in for Amazon RDS)
└── Payment   ──> PostgreSQL (local stand-in for Amazon RDS)
```

This step is **not** the AIOps engine. It creates the monitored system and controlled failure surfaces.

## Why PostgreSQL locally?

Amazon RDS is a managed database deployment model, not a different application protocol. The local testbed uses PostgreSQL so service/database interactions are real while development remains reproducible. The AWS milestone replaces this container with RDS without changing the service-level intent.

## Functional flow

`POST /v1/checkout` performs:

1. Checkout reads stock from Inventory.
2. Inventory reads the shared database.
3. Checkout calls Payment.
4. Payment writes a payment record to the shared database.
5. Checkout returns a completed response.

The testbed intentionally avoids distributed transaction complexity. Its purpose is to create observable service dependencies and cascading failure behavior, not to implement a production commerce domain.

## Correlation contract

Every inbound request receives an `x-correlation-id` if one is not supplied. Checkout propagates the same ID to Inventory and Payment. Each service emits structured JSON logs containing the same ID.

This is a temporary correlation mechanism. Step 3 replaces/augments it with OpenTelemetry trace context and real spans.

## Fault surface

Payment and Inventory expose sandbox-only endpoints:

- `GET /internal/faults`
- `POST /internal/faults`

Payload examples:

```json
{"mode":"healthy","delay_ms":0}
```

```json
{"mode":"latency","delay_ms":1500}
```

```json
{"mode":"error","delay_ms":0}
```

These endpoints exist only to produce deterministic scenarios during local development. They must not be exposed as unauthenticated production controls.

## Definition of Done

Step 2 is complete only when all are true:

- PostgreSQL, Checkout, Payment, Inventory start through Docker Compose.
- `/health` succeeds for all services.
- `/ready` verifies downstream/database readiness.
- A normal checkout succeeds end-to-end.
- Payment records are persisted in PostgreSQL.
- The same correlation ID appears across the Checkout → Payment/Inventory request chain.
- Injected Payment latency increases Checkout latency.
- Injected Payment error produces a downstream Checkout failure.
- Resetting the fault restores successful checkout.
- `python scripts/step2_smoke_test.py` ends with `STEP 2 SMOKE TEST: PASS`.

## Explicitly deferred to Step 3

- ADOT / OpenTelemetry Collector
- CloudWatch metrics/log shipping
- AWS X-Ray traces
- RED metrics and histogram aggregation
- telemetry normalization into the frozen SentinelOps telemetry contract
