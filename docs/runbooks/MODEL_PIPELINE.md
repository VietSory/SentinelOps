# Guarded offline model software

Owner-approved ADR-014 and `training-data/v2` now authorize prospectively registered
local HTTP observations as primary training data. Original source/health/precursor
evidence is still required before admission. RCAEval cannot fit models, preprocessors
or thresholds. No new dataset/model is accepted while those gates remain blocked.
The GAMMA interfaces/counts below describe immutable historical v1 reproduction;
the measured resource report is not a current admission certificate.

`scripts.audit_model_readiness` recomputes exact accounting from the committed
grouped report, identifies its historical scope, and records zero certified live
business-service health/precursor support. It never opens raw or final data.

`scripts.train_models` rejects unready reports **before estimator construction,
table access or output creation**. The proposed internal
`gamma-verified-training/v1` table reader has structural group/chronology/count
checks, but no accepted source-backed health/precursor certificate verifier exists.
Previously, caller-authored PASS flags and arbitrary existing evidence references
could enter this reader. Admission now explicitly rejects self-attested reports,
including reports claiming every gate passed. Content hashes prove byte integrity,
not workload health, outcome truth or scientific approval.

Enabling source-backed training requires implementing and reviewing a verifier
that binds traffic completeness, objective SLO health/outcomes, native identities,
independent groups and precursor chronology to approved original observations.
Changing booleans cannot unlock training. The dormant row reader is not a certified
admission pipeline. Actual numerical fit/serialization/inference primitives remain
fixture-tested separately; no valid source-admission integration is claimed.

Numerical primitives implement actual train-only RobustScaler/IsolationForest,
median/MAD control and XGBoost binary risk. Per-service matrices preserve native
identities. Calibration ECDFs and higher-quantile thresholds use the designated
healthy calibration distribution with the same false-alert budget for IF/MAD.
Contamination stays `auto`; seed/config is saved. XGBoost uses train-only class
weighting, with fixed recorded watch/degrading thresholds and no test early stopping.
Required missing features reject inference; optional CPU/memory/DB observations are
never invented. Existing causal features retain their source-specific schemas.

Fixture tests fit estimators on explicitly synthetic matrices, serialize genuine
temporary artifacts and compare loaded inference. These artifacts cannot activate;
they are not trained SentinelOps models, data acceptance or performance evidence.

Bundles contain exact ordered features, train/calibration identities, source and
feature versions, preprocessing/model bytes, hashes, ECDF identity, thresholds,
seed/config and library versions. Loading verifies every checksum and library
version before loading trusted local joblib. Do not load API uploads/untrusted
pickle. Training writes `activation_allowed=false`, never the serving release ledger.

`ModelRegistry.load` consumes a separate operator-controlled content-addressed
`validated-model-release/v1` with bundle directory/hash and validation ref per route.
The validation must bind the same model and separately accepted G2/G3, live source
parity and evaluation refs. Fixture origin is rejected even with forged PASS flags.
No endpoint writes approvals. Environment configuration uses both
`SENTINELOPS_MODEL_STORE` and `SENTINELOPS_MODEL_RELEASE_REF`; absent configuration
leaves the registry empty and `/v1/detect` returns 503. Invalid configured artifacts
fail initialization. Each request pins its loaded bundle; process restart selects a
new immutable release. Distributed hot activation/CAS and production registry audit
are not claimed by this local loader.

The existing `/v1/detect` wire shape is preserved. Real inference requires the
matching `X-SentinelOps-Source-Profile`, exact finite feature schema and a nonfuture,
fresh window. The model version is also returned in a header. Supplemental worker
inference retains risk, provenance and availability; unavailable risk stays null.
`/v1/risk` serves the existing strict degradation-risk contract with the same
source/window checks and returns 503 when the compatible risk model is unavailable.
The explicit legacy demo remains separately marked. No fallback supplies fake scores.

Event evaluation matches actual alert emissions one-to-one to independent evaluator
events, keeps duplicate/missed alerts, reports measured delay/lead time and denominators,
and refuses to invent uncertainty or timing for missing/instantaneous precursors.
No numerical fixture score is reported as benchmark accuracy. Independent session
evidence, sealed final evaluation and a bootstrap protocol remain prerequisites for
generalization/uncertainty claims.

## Recorded model-signal clocks

The continuous collector emits `validated-window-model/v2` with a separate
`inference_started_at` and `evaluated_at`. It pins feature eligibility and model
execution to the recorded start, then records prediction availability at actual
completion. Replay recomputes with the original start and validates completion
ordering, source identity, signal status and actual estimator output. Completion
time does not become the feature freshness decision. The existing RCA stale
support guard still applies at the later RCA decision; replaying a valid inference
does not automatically make it eligible RCA evidence.

Readers retain v1 completion-based replay because v1 did not capture a start. No
historical start is invented and immutable v1 artifacts are not rewritten. Both
canonical and legacy replay use the same validator. Unsupported versions and
forged status/clock combinations fail closed. The strict HTTP prediction payloads,
source contract, model admission and activation gates are unchanged.

[Accepted ADR-014](../adr/ADR-014-proposed-local-training-evidence.md) records explicit
owner approval on 2026-10-31. The [new capture protocol](LOCAL_HTTP_CORPUS.md) describes
prospective registration; source authorization does not pass admission or activate models.

## Owner-approved prospective v2 implementation

`scripts.audit_local_corpus` now implements the original-observation certificate
producer for the new local source; the historical GAMMA limitations above remain
unchanged. See [source verification, oracle and collection](LOCAL_HTTP_CORPUS.md).
The source verifier must pass every per-service healthy/precursor/group/split
condition and bind an immutable dataset freeze before `train_local_http` constructs
estimators. The current in-progress corpus is BLOCKED, so no source-backed fit or
research model metric is claimed.

Admitted fitting reuses the existing numerical primitives and registry, with an
explicit `sentinelops-model/v2` bundle. It stores the v2 policy, original dataset
reference/report, fitting Git commit, row hashes, feature order/profile, 300-second
risk horizon and all existing model/checksum/calibration metadata. Censored risk
rows stay excluded; healthy calibration is not mixed into fitting. Fitting writes
unactivated artifacts and cannot write the separate operator release ledger.
Loading/promotion reject fixtures, historical GAMMA and locally relabelled v1
bundles. Separate G2/G3, live-parity and matching dataset/policy validation are
required before serving. The default registry remains empty and returns 503.

The minute-aligned v2 risk target is anchored at the closed feature-window end.
Both actual start and completion must precede its next minute; after that, risk is
null with explicit supplemental availability, even if IF still has a valid fresh
window. The collector/replayer apply the same completion qualification. This
preserves the approved forecast horizon without backdating an emission. Existing
strict HTTP contracts and v1 recorded model-signal replay remain unchanged.
