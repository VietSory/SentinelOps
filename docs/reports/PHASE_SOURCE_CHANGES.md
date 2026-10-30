# Three-phase source change inventory

Implementation frozen and locally verified at `f1b997c9a0f3d07dd5e695b51451ef9e7799d253`.
This inventory lists final additions/modifications against the newer canonical
upstream checkpoint `f1deb7ee2263aed79f4e49a43b0411ba2b7ad37a`, merged without
resetting or rewriting shared history. Existing canonical runtime, ADR-012 and
SQL migrations remain authoritative; retaining them is not a claim they were
newly created by this delivery.

## Logical implementation commits

| Commit | Scope |
| --- | --- |
| `5a9aaa3` | Phase 1 initial continuous prototype and tests; later runtime/schema retired after fetching canonical upstream; recorded history kept offline |
| `4833cce` | Phase 2 guarded source admission, real estimator/baseline software, bundles, registry, evaluation and inference API |
| `f162727` | Phase 3 native development RCAEval evaluator and pure captured-history replay |
| `282356b` | Phase 3 validated model evidence hooks and bounded incident-worker metrics |
| `f1b997c` | Canonical upstream merge: dedicated journal/writer preserved, optional pinned model verification integrated, duplicate production runtime removed |
| `9e32c3e` | Phase 1 actual canonical native, raw-lineage/recovery and protected HTTP verification report |
| `52e5fb8` | Phase 2 exact data readiness/blocker and numerical software verification report |

Phase 3 reporting/status is committed separately after these checkpoints. Each
implementation commit was preceded by relevant executed checks; the merged source
received the complete 557-test, contract, wheel and canonical local-runtime checks.
Published CI verifies its own exact delivery SHA; older CI is not reused as proof.

## Significant changes

- `ml/`: guarded corpus/readiness, train-only preprocessing and estimators, distinct
  MAD baseline, versioned artifact checks, trusted release loading and event evaluation.
- `api.py`/settings: optional immutable registry, actual detector/risk inference and
  explicit unavailable behavior; existing detection/IER wire contracts preserved.
- `model_signals.py`/canonical journal, RCA and replay: accepted source-state-bound
  model evidence, pinned estimator recomputation, stable provenance and null missing
  signals; frozen scoring weights/abstention remain unchanged.
- Native RCAEval adapter/config/CLI: role-guarded development data, supported native
  log evidence, ground truth joined only after scoring, honest weak/abstained metrics.
- Worker metrics: isolated bounded incident instrument set; actual transport proof
  in this delivery covers the existing health metrics, not all worker instruments.
- Legacy projection/replay: pure offline compatibility with the initial real capture;
  no alternative production worker or database migration.
- Tests/CI: preserve canonical S1 and both S5 jobs, source integrity/HTTP checks;
  add optional-ML numerical, admission, native-evaluation and integration coverage.

## Actual final files

A = added; M = modified, relative to the canonical upstream checkpoint above.
Raw datasets, local model fixtures, credentials and large runtime captures are
excluded from Git; sanitized measured summaries and snapshot checksums are committed.

| Change | File |
| --- | --- |
| M | [.github/workflows/ci.yml](../../.github/workflows/ci.yml) |
| M | [README.md](../../README.md) |
| A | [configs/rcaeval_development_evidence.json](../../configs/rcaeval_development_evidence.json) |
| A | [docs/MODEL_PIPELINE.md](../../docs/MODEL_PIPELINE.md) |
| M | [docs/PROJECT_STATUS.md](../../docs/PROJECT_STATUS.md) |
| A | [docs/RCAEVAL_NATIVE_DEVELOPMENT.md](../../docs/RCAEVAL_NATIVE_DEVELOPMENT.md) |
| A | [docs/STEP7_INCIDENT_WORKER.md](../../docs/STEP7_INCIDENT_WORKER.md) |
| A | [docs/reports/PHASE1_CONTINUOUS_PIPELINE.md](../../docs/reports/PHASE1_CONTINUOUS_PIPELINE.md) |
| A | [docs/reports/PHASE2_DATASET_AI_MODELS.md](../../docs/reports/PHASE2_DATASET_AI_MODELS.md) |
| A | [docs/reports/PHASE3_RCA_AI_INTEGRATION.md](../../docs/reports/PHASE3_RCA_AI_INTEGRATION.md) |
| A | [docs/reports/PHASE_SOURCE_CHANGES.md](../../docs/reports/PHASE_SOURCE_CHANGES.md) |
| A | [docs/reports/evidence/PHASE1_CONTINUOUS_PIPELINE.json](../../docs/reports/evidence/PHASE1_CONTINUOUS_PIPELINE.json) |
| A | [docs/reports/evidence/PHASE2_DATASET_AI_MODELS.json](../../docs/reports/evidence/PHASE2_DATASET_AI_MODELS.json) |
| A | [docs/reports/evidence/PHASE3_RCA_AI_INTEGRATION.json](../../docs/reports/evidence/PHASE3_RCA_AI_INTEGRATION.json) |
| A | [scripts/audit_model_readiness.py](../../scripts/audit_model_readiness.py) |
| M | [scripts/check_installed_package.py](../../scripts/check_installed_package.py) |
| A | [scripts/evaluate_rcaeval.py](../../scripts/evaluate_rcaeval.py) |
| M | [scripts/replay_incident_worker.py](../../scripts/replay_incident_worker.py) |
| A | [scripts/replay_phase_incident_prototype.py](../../scripts/replay_phase_incident_prototype.py) |
| M | [scripts/run_incident_worker.py](../../scripts/run_incident_worker.py) |
| A | [scripts/train_models.py](../../scripts/train_models.py) |
| M | [src/sentinelops/api.py](../../src/sentinelops/api.py) |
| M | [src/sentinelops/api_settings.py](../../src/sentinelops/api_settings.py) |
| M | [src/sentinelops/incidents/rca.py](../../src/sentinelops/incidents/rca.py) |
| M | [src/sentinelops/incidents/repository.py](../../src/sentinelops/incidents/repository.py) |
| A | [src/sentinelops/ml/__init__.py](../../src/sentinelops/ml/__init__.py) |
| A | [src/sentinelops/ml/corpus.py](../../src/sentinelops/ml/corpus.py) |
| A | [src/sentinelops/ml/evaluation.py](../../src/sentinelops/ml/evaluation.py) |
| A | [src/sentinelops/ml/models.py](../../src/sentinelops/ml/models.py) |
| A | [src/sentinelops/ml/readiness.py](../../src/sentinelops/ml/readiness.py) |
| A | [src/sentinelops/ml/registry.py](../../src/sentinelops/ml/registry.py) |
| A | [src/sentinelops/public_data/rcaeval_evaluation.py](../../src/sentinelops/public_data/rcaeval_evaluation.py) |
| M | [src/sentinelops/workers/incident.py](../../src/sentinelops/workers/incident.py) |
| M | [src/sentinelops/workers/incident_journal.py](../../src/sentinelops/workers/incident_journal.py) |
| M | [src/sentinelops/workers/incident_rca.py](../../src/sentinelops/workers/incident_rca.py) |
| M | [src/sentinelops/workers/incident_replay.py](../../src/sentinelops/workers/incident_replay.py) |
| A | [src/sentinelops/workers/legacy_incident_projection.py](../../src/sentinelops/workers/legacy_incident_projection.py) |
| A | [src/sentinelops/workers/legacy_incident_replay.py](../../src/sentinelops/workers/legacy_incident_replay.py) |
| M | [src/sentinelops/workers/metrics.py](../../src/sentinelops/workers/metrics.py) |
| A | [src/sentinelops/workers/model_signals.py](../../src/sentinelops/workers/model_signals.py) |
| M | [tests/test_incident_evidence.py](../../tests/test_incident_evidence.py) |
| A | [tests/test_incident_metrics.py](../../tests/test_incident_metrics.py) |
| A | [tests/test_ml_pipeline.py](../../tests/test_ml_pipeline.py) |
| A | [tests/test_model_signals.py](../../tests/test_model_signals.py) |
| A | [tests/test_phase_incident_projection.py](../../tests/test_phase_incident_projection.py) |
| A | [tests/test_rcaeval_evaluation.py](../../tests/test_rcaeval_evaluation.py) |
