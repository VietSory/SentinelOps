# Phase 1 — continuous local incident pipeline

Verified implementation: `f1b997c9a0f3d07dd5e695b51451ef9e7799d253`.
Runtime capture: 2026-10-29 UTC; additional protected HTTP/replay checks:
2026-10-30. **Local S1 integration PASS; this is not full G4 acceptance.**

## Audit and implementation checklist

The initial clean checkout was `e33bddd6c8`; its executed baseline had **477 passing
tests**, rather than assuming the historical 466-test checkpoint. The mandatory
README/status/specification/delivery/gate/public-data/Step 5/Step 7/partition docs,
accepted ADRs, policies, contracts, source, scripts, tests and CI were inspected.
Existing strict telemetry, health lineage, episode correlation, RCA scoring,
immutable IER revisions, PostgreSQL repository and protected API were reusable.

A subsequent fetch found newer `origin/main` at `f1deb7e`, including accepted
ADR-012 and a canonical continuous journal. That work was merged and preserved.
The initial parallel prototype's runtime and SQL were removed; only its pure
offline projection/replay remains for its recorded capture. There is one production
consumer implementation and one accepted migration, not competing journal designs.

- [x] Reuse the canonical accepted-health reader, source verifier and dependency reducer.
- [x] Preserve dedicated nonpooled lock/writer ownership, immutable intents/receipts,
  checkpoints, CAS/idempotency, retry and natural recovery under ADR-012.
- [x] Integrate optional validated-model evidence with the existing RCA and IER authority.
- [x] Keep missing telemetry UNKNOWN, models unavailable and insufficient RCA abstained.
- [x] Execute real workload, database/source outage and competing-consumer crash recovery.
- [x] Verify protected HTTP history and positive/negative raw-lineage replay.
- [x] Run regression, contract and installed-wheel checks; retain blocked model gates.

The default objective path uses actual SLO evidence without a model. Native bounded
traces/logs and supported infrastructure observations retain receipt/source semantics;
the run does not invent deployment, resource or database support. Model integration
is covered separately in Phases 2/3. Frozen schemas, thresholds and source policy
are unchanged. No cloud infrastructure or action executor was introduced.

## Reproducible environment and commands

Python 3.11.15, isolated `.venv`, development and optional ML dependencies installed.
This Nix workstation needed the following runtime settings; the unsupported system
Python and missing native-library attempts were environment failures, not passing tests:

```sh
export LD_LIBRARY_PATH=/nix/store/0iv8glcslgfcgn371lbjr5jjw5a6cqir-gcc-15.3.0-lib/lib:/nix/store/dbz6pb9g67kpgpl95k8d85kzpxm1c32p-zlib-1.3.2/lib
export OPENBLAS_NUM_THREADS=1
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_installed_package.py
.venv/bin/python scripts/step2_smoke_test.py
.venv/bin/python scripts/step3_smoke_test.py
```

Results: **23 schemas, 17 samples, two policy configurations validated; 557 tests
passed, zero skipped, one upstream Starlette/AnyIO deprecation warning**. Wheel
verification passed outside the checkout. Step 2/3 real Docker smoke checks passed,
including latency/error propagation, recovery, traces, logs and service counters.
The full suite was executed on the frozen implementation; later report-only commits
do not change those tested source bytes. Socket/native checks require access to
the owned local Docker/socket environment.

Compose projects were isolated as `sentinelops-phases` (commerce port 55432) and
`sentinelops-final-control` (control port 55434), using local port-only overrides.
Unrelated projects, ports 5432/5433, existing datasets and volumes were preserved.
Use the canonical `docker-compose.yml`, `docker-compose.control.yml` and accepted
SQL migration; do not apply the removed prototype schema to the canonical database.
Configure the loopback `CONTROL_DATABASE_URL` with the nonowner `control_writer`
role; the password remains environment configuration rather than report output.

Executed canonical actual-clock acceptance command:

```sh
COMPOSE_PROJECT_NAME=sentinelops-phases .venv/bin/python -m scripts.check_health_worker_live \
  --control-container sentinelops-final-control-control-postgres-1 \
  --control-project sentinelops-final-control --native-project sentinelops-phases \
  --prometheus-container sentinelops-phases-prometheus-1 --incident-consumer \
  --metrics-otlp-endpoint http://127.0.0.1:4317 \
  --output artifacts/phases/canonical-native
```

Protocol SHA-256: `3fd268bd99ee8fe80e316059c09e2f661285110d923b97e0cd71778a4ec0c1be`.
The harness recorded implementation hashes and rejected changes during execution.
It ran real healthy traffic, controlled Payment latency, recovery, health-worker
SIGKILL/restart, a database outage and a Prometheus outage with continued traffic.
The incident observer killed the consumer after a durable IER, then resumed two
competing processes. No manual incident construction or replay triggered detection.

## Actual runtime evidence

| Measurement | Result |
| --- | --- |
| Accepted source history | 18 committed buckets / 54 three-service signals |
| Availability | 39 ELIGIBLE / 15 UNKNOWN signals; no missing-as-healthy conversion |
| Open/recovery | Checkout and Payment each opened and naturally recovered once |
| Correlation | One dependency-path group, Checkout + Payment; structural candidate Payment |
| RCA | Explicit abstention: insufficient coverage and score; three captured traces, 2,451 source log completions |
| Durable IER | One incident; four revisions: OPEN → RCA_READY → ESCALATED → CLOSED |
| Crash/concurrency | Consumer SIGKILL after durable IER; two competitors resumed without duplicate versions |
| Database failure | Stopped 16:20:30 UTC, started 16:22:06 UTC; processing recovered |
| Source failure | Prometheus stopped 16:27:15 UTC, started 16:28:00 UTC; all services recorded source-outage UNKNOWN |
| Traffic during source outage | 539 actual HTTP 200 responses; absence of telemetry remained UNKNOWN |
| Processing | All 18 batches timed; maximum 286.275 ms, two missed-deadline buckets retained |
| Operational metrics | Actual health-worker OTLP → Collector → Prometheus PASS, including errors/restart guard/shutdown readiness |
| Model/action status | No model outputs, no remediation authorization or action |

Incident: `INC-b0e09b43a9992c3aacba27ea7818a13b4eae246264ce08ded51fb4924e4d3502`.
Health snapshot SHA-256: `bbc4f36cb111e414e6dc1f1c6ae7e2fa956b484536d030b9c0098074a3bfb2f9`.
Incident snapshot SHA-256: `9d0141fd03dc00da7908be19a4a51d63e8ef00f7a3ae9bd455793d748d97c796`.

```sh
.venv/bin/python -m scripts.replay_incident_worker --run artifacts/phases/canonical-native \
  --expected-health-snapshot-sha256 bbc4f36cb111e414e6dc1f1c6ae7e2fa956b484536d030b9c0098074a3bfb2f9 \
  --expected-incident-snapshot-sha256 9d0141fd03dc00da7908be19a4a51d63e8ef00f7a3ae9bd455793d748d97c796 \
  --output artifacts/phases/canonical-native/offline-incident-replay-bound.json
.venv/bin/python -m scripts.check_incident_replay_integrity --run artifacts/phases/canonical-native \
  --output artifacts/phases/canonical-native/offline-incident-negative.json
.venv/bin/python -m scripts.check_native_incident_api --run artifacts/phases/canonical-native \
  --output artifacts/phases/canonical-native/native-api-reconnected
```

Positive replay **PASS**: raw telemetry → features/health → groups → operational
RCA → all four IER versions/audit/outbox reconstructed. All **ten corruption probes
rejected**, including rehashed fake IER/RCA, bad receipt, missing/duplicate source,
raw bytes, actor and outbox mutations; originals unchanged. Protected real Uvicorn
HTTP/PostgreSQL check **PASS**: current/history/context, authentication, pagination,
ETag, immutable detection time, four versions/four outbox rows, no read-side writes,
demo disabled and unavailable-model readiness 503.

The first later HTTP attempt failed with connection refused because the owned
database container had stopped between sessions. Starting that exact container
without changing its volume and rerunning to a new output directory passed. This
also confirmed persisted history survives container shutdown; the failed attempt
was not counted as a pass or silently overwritten.

## Scenario scope and limitations

The initial prototype also captured actual independent Payment/Inventory errors,
shared Checkout ambiguity, source/database outages and competing-consumer restart:
21 buckets/63 signals, four groups/12 versions. Its original commit was `5a9aaa3`;
its capture is retained under `artifacts/phases/native-v1` and pure offline replay
passes after the canonical merge. It is historical integration evidence, not proof
of the retired runtime's safety or a new canonical S5 run. Existing upstream
[canonical S1/S5 evidence](2026-10-29-continuous-incident-local.md) remains attributed
to its original commit. Exact-head CI repeats canonical S1 and both S5 cases.

Unit/integration regressions cover healthy operation, individual/propagating and
independent failures, shared symptoms, abstention, duplicate/out-of-order history,
source corruption, journal retry and lock/writer ownership. The optional incident
metrics path is software-tested; this capture proves health-worker metrics transport,
not incident-metrics OTLP transport. These are development executions, not independent
diagnostic trials or a full reliability/production acceptance. G2/G3 and full G4
remain unaccepted. No sealed final holdout was accessed.

Machine-readable measurements: [PHASE1_CONTINUOUS_PIPELINE.json](evidence/PHASE1_CONTINUOUS_PIPELINE.json).
Each published SHA still requires its own completed CI; the release commit's exact
run is recorded in the final delivery response. Older green runs do not certify it.
