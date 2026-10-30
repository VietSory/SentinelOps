# Phase 3 — evidence integration and native RCA development

Initial code verified: `f1b997c9a0f3d07dd5e695b51451ef9e7799d253`; final upstream
compatibility and canonical ablation checks are recorded below. The runtime extends
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

## Initial native RCAEval development execution

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
| RCA Development Evaluation | Three actual permitted cases and canonical ablations executed; top-1 zero, descriptive top-3 1/3 with trace support, 100% abstention/zero qualified |
| AI/RCA Integration | Optional v2 capture/pinned-estimator/raw-lineage checks fixture-tested; live ML unavailable |
| End-to-End Incident Processing | Actual objective SLO → canonical RCA/IER → SQL/API PASS; exact-head CI repeats S1 and both S5 |

AWS deployment, Bedrock, production authentication/dashboard, automatic remediation
and final holdout evaluation remain outside this delivery. Source changes and
phase-specific implementation commits are listed in [the inventory](PHASE_SOURCE_CHANGES.md).

## Final canonical source-admission and ablation verification

A final fetch found five additional upstream commits ending at `3e43e35`, including
accepted ADR-013 and the trusted publisher 12-file inventory correction. They were
merged at **`b27a8c260f1408962dcdba8770df59da0eb8b5a7`**. Current manifest execution
reuses that canonical bounded adapter, fixed trailing 300-second interval, exact
native names/clocks, trace-duration fraction and exception-marker policy. The old
whole-capture generic-token helper/config remains pure historical compatibility
only. No second manifest reader bypasses the new source inventory.

The supplemental evaluator adds comparisons by removing actual log or trace support
before labels are read. It preserves frozen scoring arithmetic and cannot promote
a root or write a local IER. The original canonical business output remains exactly
`4d1ab2e228e559abc9e9a0d6bd66f9748573db05c20ebf68f4591bde09257101`.

Executed after committing the merged protocol/code:

```sh
.venv/bin/python -m scripts.evaluate_rcaeval_native --manifest data/raw/public/manifests/3445957277004895c3a61460808355cf078123c890aad6d582c08373f666450f.json --output artifacts/phases/rcaeval-canonical-merged.json
.venv/bin/python -m scripts.evaluate_rcaeval --manifest data/raw/public/manifests/3445957277004895c3a61460808355cf078123c890aad6d582c08373f666450f.json --output artifacts/phases/rcaeval-canonical-ablations.json
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python scripts/check_installed_package.py
```

Canonical repeated evaluation: **PASS**, identical repeated results, three selected /
three evaluated / zero quarantined / three abstained / zero qualified predictions;
conditional qualified accuracy unavailable. The same original 12 source files were
used; no extra case or sealed final data was opened.

| Current supported ablation | Selected/evaluated | Descriptive top-1 | Descriptive top-3 | Descriptive MRR | Abstention / qualified | Coverage by case |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Canonical native traces + logs | 3/3 | 0 | 1/3 | 1/6 | 3/3; 0 qualified | 0 / 0.40 / 0.40 |
| Remove observed log support | 3/3 | 0 | 1/3 | 1/6 | 3/3; 0 qualified | 0 / 0.25 / 0.25 |
| Remove observed trace support | 3/3 | 0 | 0 | 0 | 3/3; 0 qualified | 0 / 0.15 / 0.15 |

These ranking metrics include abstained hypotheses; **they are not qualified RCA
accuracy**. The first case has no published logs/traces; the latter two provide
observed modalities. Removing unavailable support does not invent an observation.
Source receipt/SLO/model/deployment gaps remain; the 0.40 maximum coverage cannot
meet the unchanged 0.75 floor. No independent-session confidence interval is claimed.
The earlier whole-capture results above remain separately attributed, rather than
being replaced by these different fixed-profile measurements.

Final regression: **599 passed, zero skipped**, one existing deprecation warning;
contracts and installed wheel PASS. Focused source/native/ablation/RCA checks:
**122 passed**. A new ablation fixture initially used Unix second 100, which could
not support the fixed 300-second window; correcting fixture time to second 1000
passed without changing production policy. The initial full run recorded that
failure (598 pass/one fail); the final complete rerun passed all 599.
The earlier raw incident capture also passes externally checksum-bound replay
under this merged implementation. Actual S1/S5 evidence for the published release
is independently rerun by exact-head CI. G2/G3/full G4 remain unaccepted.
