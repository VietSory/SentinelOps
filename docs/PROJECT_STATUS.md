# SentinelOps — verified implementation status

Updated 2026-10-26. This is the current evidence index; the initial-state tables in the delivery plan remain dated planning snapshots. PRs are stacked on feature/fix/docs branches, not merged into `main`.

Frozen-policy checkpoint: **189 tests passed** in the ML environment; **17 schemas, 12 samples and the actual policy configuration** validate. The installed wheel loads the new contract and passes its API smoke check outside the checkout. These checks do not establish GAMMA training readiness or measured risk performance.

| Area | Verified state | Evidence / remaining gate |
| --- | --- | --- |
| Architecture | Product spec v1.1; old v1.0 archived; ADR-001…005 retained, ADR-006…011 accepted; 011 supersedes 010's source choice | [ADR index](adr/README.md), [technical spec](TECHNICAL_SPEC.md) |
| Frozen training sources | Owner-approved GAMMA v1 training / pinned RCAEval RCA validation and holdout; at most two public sources, no row concatenation; SMD historical only | [Machine-validated contract](../configs/training_data_contract.json), [ADR-011](adr/ADR-011-frozen-gamma-rcaeval-contract.md), [execution plan](PUBLIC_DATA_PLAN.md) |
| Foundation, workload, OTel | Unit/schema/wheel checks and real Docker Step 2/3 pass after baseline fixes | [Baseline repair](reports/2026-10-26-baseline-repaired.md), PRs #2–3 |
| Telemetry/features | Real history, strict quality/cutoff, immutable raw captures, deterministic replay, native DB observation | [Phase 4 local report](reports/2026-10-26-phase4-local.md), PRs #4–5; sustained 95% coverage is not established by a short smoke |
| CI | Complete Foundation/observability jobs green for runner commit `62452c1` | [CI run 40261800520](https://github.com/VietSory/SentinelOps/actions/runs/40261800520); later branches require their own checks |
| Experiments/data | Strict manifests/labels/splits and bounded S0/S1/S3 runner; real clean-source S0 and gradual S3 completed | [Healthy pilot](reports/2026-10-26-healthy-pilot.md), [gradual pilot](reports/2026-10-26-gradual-pilot.md); combined pilot 57 rows, including 6 precursor positives each for Payment/Checkout |
| G2 | **IN_PROGRESS**: only 5 healthy train windows for Payment/Checkout, 19 for Inventory, one gradual-positive run, and no validation/test runs | Strict freeze rejects training; explicit pilot manifests record every blocker |
| Models/G3 | SMD IF/MAD single-machine results retained as historical research, excluded from v1 training; GAMMA acquisition/readiness and all live gates remain open | [Historical benchmark](reports/2026-10-26-public-smd-if.md); no live activation or verified public precursor labels |
| Incident storage | Immutable revisions/CAS/idempotency/audit/outbox verified on real isolated PostgreSQL and after restart | [Storage acceptance](reports/2026-10-26-incident-storage.md), PR #8; storage prerequisite only |
| Incident HTTP API | Protected persistent IER writes/read/history, revision/CAS/idempotency, bounded supplemental contracts; default inference fails closed | [API acceptance](reports/2026-10-26-incident-api.md); local auth only, no production identity or recovery/action writer |
| RCA/G4 | Correlation/RCA not implemented/evaluated | Storage/API tests are not RCA accuracy evidence |
| Explanation/UI, remediation, AWS, release | Not implemented/accepted | G5–G8 remain open; AWS budget/quota/credentials not confirmed |

The public-data audit checkpoint has 173 passing tests in the ML environment; 16 schemas/12 samples validate. Real Uvicorn/HTTP/PostgreSQL acceptance includes database outage/reconnection; [API checkpoint CI](https://github.com/VietSory/SentinelOps/actions/runs/40265791810) is green. Raw replay and full trace/log lineage of both real local pilots also [pass admission checks](reports/2026-10-26-dataset-lineage.md). Longer local attempts did not produce an accepted dataset and no long-running generator is retained as the primary training source. Later commits need their own CI. Default inference returns 503 without a model; local fixtures need explicit demo mode and are not model evidence.

Next: implement bounded GAMMA v1 acquisition/inventory and raw/processed capability audit under ADR-011, then freeze grouped source-specific features/splits before IF/MAD fitting. XGBoost keeps the original pre-SLO gradual target and remains not-ready until evidence supports its labels; no substitute corpus or target. Partition RCAEval RCA development/holdout separately and verify transfer before any live activation. Continue independent signal/correlation/RCA implementation using the protected revision API; action execution keeps its own gates.
