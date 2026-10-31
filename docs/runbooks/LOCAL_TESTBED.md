# Step 2 — Experimental Microservices Testbed

## Lane B: isolated application runtime

Use `python -m scripts.lane_b config` to verify the rendered B configuration, then
`python -m scripts.lane_b up`. This combines the existing testbed/control Compose
files with `docker-compose.b.yml`, owned project `sentinelops-b`, fresh B volumes
and the reserved loopback ports from the team contract. It does not use A's corpus,
database or worker checkpoints. Run `status` to inspect B; `down` preserves volumes.
The launcher refuses project resources belonging to another checkout.

Host processes run in separate terminals:

```sh
python -m scripts.lane_b serve
python -m scripts.lane_b health
# Copy the actual READY stream_id from the health worker, not a guessed value:
python -m scripts.lane_b incidents --health-stream <stream_id>
```

API listens on `127.0.0.1:18000`; B commerce/control DB ports are 15432/15433,
Prometheus 19090, Jaeger 26686 and OTLP 14317/14318. All published ports, not just
PostgreSQL, are replaced. Workers stay host processes so their loopback ownership
guards remain intact. Stop those foreground processes with Ctrl+C separately;
`down` does not kill arbitrary host processes. Don't run B load/stress alongside
A's active measurement on a shared host without isolated capacity/scheduling.

The first launch creates `artifacts/lane-b/runtime.json` and a private 0600
`artifacts/lane-b/api-token`; read that file locally when a client needs its token.
The launcher never prints the token. You can supply `SENTINELOPS_API_TOKEN` through
the environment instead. Inherited Compose/model settings are not reused; models
remain unavailable until separately accepted H1 wiring. Control credentials must
select B's explicit nonowner loopback database on 15433. API liveness remains
available during database/model outages; it is not proof those dependencies work.

After H1 acceptance, materialize the complete trusted model store inside
`artifacts/lane-b/models/`. Select it explicitly with paired `--model-store` and
`--model-release-ref` on `serve` and `incidents`; use the same immutable release
for both processes. The existing registry verifies checksums, approvals and
compatibility at startup. The launcher writes no approval and never inherits A's
model configuration. Invalid releases fail startup. Keep the previous immutable
release/reference for rollback; no hot activation is implemented. The API can
select it at restart, but an existing incident consumer is already bound to its
original evidence/release profile. Changing that profile needs A's explicit
consumer migration/rollover protocol; simply restarting it with another release
is rejected by the existing journal. Do not reset checkpoints or create duplicate
streams to evade this guard. These flags provide wiring, not that migration.
Without an accepted release and applicable migration, leave both flags absent.

For B1 source integration, start both workers then run
`python -m scripts.check_lane_b_live`. It executes 180s healthy, 360s Payment
latency and 300s recovery against B only, preserves request observations and restores
the original healthy fault configuration in `finally`. It checks native journal/DB
revisions and protected API reads, never writes an IER manually. This is operational
integration evidence, not admitted training data or RCA accuracy. Output under
`artifacts/lane-b/live-*` is separate from A's corpus.

### Pin observed B container images and configuration

From a clean committed checkout with all eight B containers running, execute
`python -m scripts.lane_b snapshot`. It returns an immutable content-addressed
`deployment_ref` under `artifacts/lane-b/releases`. The observer records actual
local image/container IDs, the rendered Compose configuration hash and hashes of
every owned read-only bind file (SQL/Collector/Prometheus configuration). Its own
Git/code identity is recorded; this does not claim the images were built by that
commit. Container readiness is not business/SLO health certification.

```sh
python -m scripts.lane_b snapshot
python -m scripts.lane_b up --deployment-ref artifact://manifests/<hash>.json
```

Locked `up` validates all configuration/bind bytes and local image identities
before mutation, materializes an immutable override with exact image IDs, disables
build/pull and retains volumes. Missing images or changed inputs stop the operation;
there is no fallback to mutable tags. Normal `up` without a lock remains the dev
build path. A lock is checkout/local-daemon bound; another machine must materialize
the actual image bytes and verify its own configuration instead of assuming an
image ID is a portable registry pull reference.

The scope is Docker images and bind inputs only. Host API/worker processes, database
contents and model releases are not pinned by this lock. It is not a backup, model
approval, admitted dataset or approved known-good remediation ledger. The strict
lock contract always carries `model_activation_allowed=false` and
`remediation_approval=false`; it cannot unlock inference or automatic rollback.

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

These endpoints require `ENABLE_FAULT_INJECTION=true` and `DEPLOYMENT_ENVIRONMENT=local` or `test`. They return 404 by default and in non-sandbox environments even if the enable flag is set. Compose explicitly enables them in the sandbox and publishes services only on loopback. They must not be exposed as unauthenticated production controls.

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
