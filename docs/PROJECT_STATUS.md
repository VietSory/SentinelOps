# SentinelOps — verified implementation status

Updated 2026-10-26. This is the current evidence index; the initial-state tables in the delivery plan remain dated planning snapshots. PRs are stacked on feature/fix/docs branches, not merged into `main`.

| Area | Verified state | Evidence / remaining gate |
| --- | --- | --- |
| Architecture | Product spec v1.1; old v1.0 archived; ADR-001…005 retained, ADR-006…009 accepted | [ADR index](adr/README.md), [technical spec](TECHNICAL_SPEC.md) |
| Foundation, workload, OTel | Unit/schema/wheel checks and real Docker Step 2/3 pass after baseline fixes | [Baseline repair](reports/2026-10-26-baseline-repaired.md), PRs #2–3 |
| Telemetry/features | Real history, strict quality/cutoff, immutable raw captures, deterministic replay, native DB observation | [Phase 4 local report](reports/2026-10-26-phase4-local.md), PRs #4–5; sustained 95% coverage is not established by a short smoke |
| CI | Complete Foundation/observability jobs green for runner commit `62452c1` | [CI run 40261800520](https://github.com/VietSory/SentinelOps/actions/runs/40261800520); later branches require their own checks |
| Experiments/data | Strict manifests/labels/splits and bounded S0/S1/S3 runner; one real clean-source S0 completed | [Healthy pilot](reports/2026-10-26-healthy-pilot.md), PRs #6–7 |
| G2 | **IN_PROGRESS**: pilot has only 5 healthy train windows/service, one healthy run and no validation/test/gradual-positive runs | Strict freeze rejects training; explicit pilot manifest records every blocker |
| Models/G3 | Not implemented or trained | Statistical/IF/XGBoost comparison and real serving require G2 |
| Incident storage | Immutable revisions/CAS/idempotency/audit/outbox verified on real isolated PostgreSQL and after restart | [Storage acceptance](reports/2026-10-26-incident-storage.md), PR #8; storage prerequisite only |
| RCA/G4 | Not implemented/evaluated; HTTP incident persistence migration still pending | Repository tests are not RCA accuracy evidence |
| Explanation/UI, remediation, AWS, release | Not implemented/accepted | G5–G8 remain open; AWS budget/quota/credentials not confirmed |

The suite at storage checkpoint `ff7d830` has 120 passing tests, 12 schemas/8 samples and an installed-wheel smoke pass. See each PR/report for the code checkpoint and actual validation scope. Current detector HTTP output is still the documented Foundation mock; it is not model evidence.

Next: collect independent healthy and gradual-fault runs with predeclared splits, validate sustained source quality and finalize G2; connect incident persistence through a protected, versioned API without changing the frozen IER payload. Model training/promotion and action execution must keep their own gates.
