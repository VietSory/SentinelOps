# Phase 3 — evidence integration and native RCA development

Code verified: `f1b997c9a0f3d07dd5e695b51451ef9e7799d253`. The final runtime extends
the canonical ADR-012 journal, dedicated nonpooled writer, existing source verifier,
RCA policy and immutable IER lifecycle. The initial duplicate prototype consumer
was removed after fetching newer `origin/main`; its measured history remains
replayable as offline compatibility only. No unrelated remote work was overwritten.

## Implemented integration and authority

`ModelIncidentEvidence` optionally wraps the existing bounded operational collector.
The profile pins an independently validated release. Supplemental capture v2 binds
exact accepted consumer state, source batch, feature refs, model versions and actual
inference clocks. The journal rechecks that state against raw health lineage and
the accepted SQL step before accepting evidence. RCA/IER projection and portable
replay recompute predictions with the pinned estimator; rehashed invented scores
are rejected. V1 captures and the default no-model pipeline stay compatible.

Actual IF output contributes only the existing anomaly-strength weight. Risk
remains a distinct observed model fact; it does not become another RCA weight,
causality proof or promised warning time. Missing/ineligible/stale models produce
unavailable/null inference, never a zero anomaly/risk score. Existing score,
coverage, margin, ambiguity and unobserved-dependency abstention rules are unchanged.
IER evidence refs, lifecycle history and model provenance remain accessible through
the existing incident context API; no arbitrary prediction upload grants authority.

The model-enabled path is **fixture-tested**, including actual estimator calls,
exact feature/service/window/version matching, duplicate rejection, corruption and
estimator replay. No compatible validated model is available for a live ML test.
The objective SLO path runs independently and does not claim model success.

## Real native RCAEval development execution

Only the three previously approved development cases were acquired through the
pinned hash/role-guarded adapter. The final holdout remained sealed. Source manifest:
`3445957277004895c3a61460808355cf078123c890aad6d582c08373f666450f`.
The development protocol and source adapter were committed before scoring, then
reproduced at the final implementation commit with identical functional JSON.

| Case | Metric rows / channels | Native logs | Native spans | Error-token matches |
| --- | --- | ---: | ---: | ---: |
| RE1 OB adservice CPU | 4,201 / 49 | 0 | 0 | 0 |
| RE2 OB checkoutservice CPU | 1,441 / 72 | 171,322 | 391,997 | 0 |
| RE3 OB adservice F3 | 1,441 / 69 | 70,558 | 165,302 | 2,138 |

Native error-log presence is the supported supplemental component, using the
frozen token set and original 0.15 log weight. No foreign CPU metric is relabelled
an IF score. Parent-service edges are retained, but unverified native duration,
span-kind, receipt, SLO and deployment semantics receive no invented support.
Ground-truth case/fault/root/injection metadata enters only after diagnostic scoring.

| Actual supported ablation | Cases | Top-1 | Top-3 | MRR | Abstention | Max coverage by case |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Native error-log support | 3 | 0 | 0 | 0 | 3/3 (100%) | 0 / 0.15 / 0.15 |
| Remove that observed log support | 3 | 0 | 0 | 0 | 3/3 (100%) | 0 / 0 / 0 |

The last case's positive hypothesis is `frontend`, not its labelled injected root.
That weak downstream log observation is deliberately retained. These are failed
development localization results with explicit abstention, not an accepted accuracy
gate or evidence of independent sessions. No confidence interval is asserted.
Unavailable ML/topology/timing modalities are not compared as though they existed.
Cascading/independent-root behavior is evaluated separately by local S1/S5 runtime.

Executed commands:

```sh
.venv/bin/python scripts/download_public_data.py --dataset rcaeval
.venv/bin/python -m scripts.evaluate_rcaeval --manifest data/raw/public/manifests/3445957277004895c3a61460808355cf078123c890aad6d582c08373f666450f.json --output artifacts/phases/rcaeval-final.json
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python scripts/check_installed_package.py
```

Results: **557 regression tests passed with no skips**, 23 schemas/17 samples/two
policy configurations validated, installed-wheel checks passed. The native source
acquisition verified 12 files including the publisher index and permitted telemetry;
no final case telemetry was opened. Actual raw log/span counts above are not a
metadata-only diagnostic-accuracy claim.

## E2E, API and remaining acceptance

See [Phase 1](PHASE1_CONTINUOUS_PIPELINE.md) for the frozen local native execution,
actual outage/restart, SQL revision, protected HTTP and portable raw-lineage results.
Existing upstream S1 and both S5 development captures remain separately preserved;
their original source commit and reported seven naturally closed incidents are not
credited as a new run. The final CI repeats S1 and both S5 cases on its exact SHA,
with protected HTTP and positive/negative transported-journal verification.

G2/G3 remain blocked by the data prerequisites in the Phase 2 report. Full G4 remains
unaccepted: this development evaluator did not meet localization targets, validated
ML/runtime inference is unavailable, independent trial support is insufficient and
sealed final evaluation is not authorized. No RCA threshold was lowered, no
missing telemetry was called healthy, and no LLM/action/cloud result was fabricated.

Machine-readable measurements: [PHASE3_RCA_AI_INTEGRATION.json](evidence/PHASE3_RCA_AI_INTEGRATION.json).

## Delivery status by component

| Component | Implementation and verification scope |
| --- | --- |
| Continuous Incident Worker | Canonical journal retained/extended; new actual S1 outage/crash/concurrency PASS |
| Incident Correlation | Existing conservative policy reused; real dependency grouping, upstream S5 and fixture ambiguity tests |
| RCA Engine | Existing weights/abstention preserved; actual operational evidence processed; accuracy gate unaccepted |
| Incident Evidence Record | Existing schema/revisions reused; real four-stage natural lifecycle verified |
| PostgreSQL Integration | Dedicated writer/checkpoints/idempotency retained; real outage/restart and protected HTTP PASS |
| Dataset Readiness | Exact historical accounting recomputed; G2 BLOCKED, not a new raw cohort audit |
| Statistical Baseline | Real median/MAD software fixture-tested; admitted-source benchmark unavailable |
| Isolation Forest | Actual fit/artifact/load/inference software fixture-tested; approved training BLOCKED |
| XGBoost | Actual fit/artifact/load/risk software fixture-tested; verified precursor training BLOCKED |
| Model Inference API | Registry/schema/provenance/error paths tested; default 503, no validated activation |
| RCA Development Evaluation | Three actual permitted cases and supported log ablations executed; zero top-k/MRR, 100% abstention |
| AI/RCA Integration | Optional v2 capture/pinned-estimator/raw-lineage checks fixture-tested; live ML unavailable |
| End-to-End Incident Processing | Actual objective SLO → canonical RCA/IER → SQL/API PASS; exact-head CI repeats S1 and both S5 |

AWS deployment, Bedrock, production authentication/dashboard, automatic remediation
and final holdout evaluation remain outside this delivery. Source changes and
phase-specific implementation commits are listed in [the inventory](PHASE_SOURCE_CHANGES.md).
