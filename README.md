# SentinelOps

**Evidence-Driven AIOps Platform for Cloud-Native Reliability**

SentinelOps is the implementation repository for the approved AI4Life 2026 AIOps design. The architecture is contract-first: telemetry, model outputs, RCA results, Incident Evidence Records, LLM explanations, and remediation actions must conform to versioned schemas.

## Implementation status

The repository implements a protected incident API, a real
three-service/PostgreSQL testbed, distributed OpenTelemetry, local Prometheus
history, immutable metric captures, deterministic feature windows, predeclared
local experiments, objective SLO episodes, conservative correlation, analytical
RCA with abstention and a computed-IER producer with an isolated incident repository. Dated
verification reports live under [docs/reports](docs/reports/).

The API detector returns 503 until a real validated model is available; Foundation
fixtures require explicit local demo mode. Public source acquisition and local validation
have not closed healthy-reference, precursor-label and live-feature-parity gates.
A [continuous local SLO worker](docs/STEP7_HEALTH_WORKER.md) now has durable checkpoints and
[actual crash/outage/recovery evidence](docs/reports/2026-10-28-continuous-health-worker.md).
Its optional [operational metrics](docs/reports/2026-10-28-health-worker-metrics.md)
have measured OTLP/Prometheus outage recovery and guards against cached values after restart.
The [continuous incident journal](docs/STEP8_INCIDENT_WORKER.md) now connects accepted health to
source-derived RCA, persisted IER lifecycle and the protected API.
[Guarded model software](docs/MODEL_PIPELINE.md) and [native RCA development evaluation](docs/RCAEVAL_NATIVE_DEVELOPMENT.md)
keep missing data/model results explicit. Validated model serving, RCA performance, Bedrock,
operator UI, controlled remediation and AWS deployment follow their separate gates; local telemetry and software checks do not certify them.

The [frozen training-data contract](configs/training_data_contract.json) selects
GAMMA v1 training and RCAEval RCA validation/heldout evaluation, with at most these
two public sources and no row concatenation. SMD remains historical research.
See [ADR-011](docs/adr/ADR-011-frozen-gamma-rcaeval-contract.md) and the
[execution plan](docs/PUBLIC_DATA_PLAN.md).
[RCAEval partitions and source access](docs/RCAEVAL_PARTITIONS.md) freeze case groups
and keep the final holdout sealed. Local captures support integration
and transfer/calibration. Public bundles require measured live feature/source
compatibility before activation; XGBoost still requires independent pre-SLO
gradual labels.

Incident writes persist exact IERs with idempotency and revision checks. See the current
[phase status](docs/PROJECT_STATUS.md), [experiment protocol](docs/STEP5_EXPERIMENTS.md)
and [incident API guide](docs/STEP7_INCIDENT_API.md).

Runtime contracts remain strict and versioned. Supplemental envelopes carry
quality, provenance and future lifecycle metadata without changing legacy wire
payloads. The current architecture baseline is [PRODUCT_SPEC v1.1](docs/PRODUCT_SPEC.md).

## Architecture boundaries

- Isolation Forest: anomaly detection.
- XGBoost: degradation-risk scoring.
- RCA Engine: root-cause ranking from anomaly strength + topology/traces + timeline + log evidence.
- Incident Evidence Record: authoritative source of truth.
- Amazon Bedrock: explanation only; it must not invent evidence, decide root cause, or authorize remediation.
- Remediation: only approved runbooks, least privilege, pre-check, execution, telemetry verification, rollback/escalation, audit.

> Evidence is computed; LLM explanation is generated. Never the reverse.

## Implementation planning

The implementation planning pack, based on the source review at `20edcae`, is:

- [Delivery plan](docs/DELIVERY_PLAN.md): phases, dependencies, ownership, estimates and deliverables.
- [Technical specification](docs/TECHNICAL_SPEC.md): component design, data semantics, APIs, model lifecycle and controlled remediation.
- [Constraints and acceptance gates](docs/CONSTRAINTS_AND_GATES.md): frozen decisions, proposed targets, verification evidence and open decisions.

These documents supplement the product spec and accepted ADRs. New design choices
are marked as proposals; planned capabilities and acceptance targets are not claims
of completed implementation or measured performance.

Delivery uses topic branches and pull requests, as requested by the owner on
2026-10-30. Keep small, verified `feat:`, `fix:` and `docs:` commits for each phase;
preserve existing history and monitor CI for the exact proposed commit. Earlier
direct-to-main deliveries retain their recorded verification scope.

## Quick start

Use Python 3.11 (CI/runtime baseline) or 3.12.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
uvicorn sentinelops.main:app --app-dir src --reload
```

Then:

```bash
curl http://127.0.0.1:8000/health
```

OpenAPI docs are available at `http://127.0.0.1:8000/docs`.

## Local testbed and observability

The repository also includes the Step 2 microservices and Step 3 OpenTelemetry
baseline. With Docker running and the virtual environment activated:

```bash
docker compose up --build -d --wait --wait-timeout 120
python scripts/step2_smoke_test.py
python scripts/step3_smoke_test.py
```

Jaeger is available at `http://127.0.0.1:16686`; Collector metrics are exposed at
`http://127.0.0.1:8889/metrics`. See [Step 3 observability](docs/STEP3_OBSERVABILITY.md)
for the verification gates and troubleshooting. The AIOps API above remains a
separate control process. The services generate measurements; host-side analytical
[RCA replay/incident production](docs/STEP7_RCA.md) consumes captures separately.
The [continuous incident worker](docs/STEP8_INCIDENT_WORKER.md) is separately runnable.
Validated ML activation and remediation remain gated.

## Telemetry history and feature windows

Prometheus at `http://127.0.0.1:9090` retains local metric history for seven days.
Published testbed ports bind to loopback. Phase 4 adds immutable source captures,
quality-aware observations and deterministic five-minute feature windows:

```bash
python scripts/collect_telemetry.py --output artifacts/capture --minutes 6
python -m scripts.step4_smoke_test --output artifacts/step4/local
```

The live Step 4 check takes about ten minutes and temporarily controls sandbox
faults; run it without another fault experiment. It checks real histogram/rate
signals, replay and trace/log evidence, then restores the original fault settings.
See [Step 4 telemetry](docs/STEP4_TELEMETRY.md). Captures are test evidence, not a
trained model or an AWS integration claim.

## Contract validation

```bash
python scripts/validate_contracts.py
```

## Repository layout

```text
contracts/        Frozen external data/API schemas
configs/          Product configuration that is allowed to change without redesign
docs/             Product spec and ADRs
samples/          Contract-valid example payloads
scripts/          Validation/replay/developer utilities
src/sentinelops/  Control-plane API and telemetry/feature modules
services/         Monitored Checkout, Payment and Inventory workload
observability/    Collector and local Prometheus configuration
infra/            Local database and future deployment infrastructure
tests/            Contract and API tests
```

## Change policy

Changes to thresholds, feature windows, hyperparameters, RCA weights, model versions, and deployment sizing are tunable implementation changes.

Changes to mandatory contract fields, model responsibility boundaries, the Incident Evidence Record authority, LLM privileges, or remediation safety flow are breaking architecture changes and require an ADR before implementation.
