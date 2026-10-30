# Guarded offline model software

GAMMA v1 remains the only primary training source; RCAEval cannot fit models,
preprocessors or thresholds. No new accepted training dataset/model is produced
while healthy-reference, independent precursor and feature/source gates remain
blocked. The measured resource report is not an admission certificate.

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
