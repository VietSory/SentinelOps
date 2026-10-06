# SentinelOps

**Evidence-Driven AIOps Platform for Cloud-Native Reliability**

SentinelOps is the implementation repository for the approved AI4Life 2026 AIOps design. The architecture is contract-first: telemetry, model outputs, RCA results, Incident Evidence Records, LLM explanations, and remediation actions must conform to versioned schemas.

## Foundation milestone

This repository starts with the **Foundation & Contract Freeze** milestone. At this stage we intentionally do not deploy EKS, train production models, or enable autonomous remediation yet.

The first Definition of Done is:

- product spec committed as the source of truth;
- machine-readable contracts validated in CI;
- API skeleton starts successfully;
- `/health` returns OK;
- `/v1/detect` accepts a valid telemetry/feature payload and returns a contract-valid mock result;
- `/v1/incidents` can build a valid sample Incident Evidence Record;
- replay input format exists;
- contract tests run automatically.

## Architecture boundaries

- Isolation Forest: anomaly detection.
- XGBoost: degradation-risk scoring.
- RCA Engine: root-cause ranking from anomaly strength + topology/traces + timeline + log evidence.
- Incident Evidence Record: authoritative source of truth.
- Amazon Bedrock: explanation only; it must not invent evidence, decide root cause, or authorize remediation.
- Remediation: only approved runbooks, least privilege, pre-check, execution, telemetry verification, rollback/escalation, audit.

> Evidence is computed; LLM explanation is generated. Never the reverse.

## Quick start

```bash
python3 -m venv .venv
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
src/sentinelops/  Control-plane API skeleton
tests/            Contract and API tests
```

## Change policy

Changes to thresholds, feature windows, hyperparameters, RCA weights, model versions, and deployment sizing are tunable implementation changes.

Changes to mandatory contract fields, model responsibility boundaries, the Incident Evidence Record authority, LLM privileges, or remediation safety flow are breaking architecture changes and require an ADR before implementation.
