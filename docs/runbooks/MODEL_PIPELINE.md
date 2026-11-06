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

## Source-gated development comparison

Run `scripts/evaluate_local_models.py` from the clean committed implementation
checkout after the nonfinal corpus is source-admitted/frozen and the candidate
has been fitted. The immutable capture checkout remains separate.

```sh
python -m scripts.evaluate_local_models \
  --store /trusted/corpus \
  --dataset-ref artifact://manifests/DATASET_HASH.json \
  --models /trusted/candidate \
  --training-manifest-sha256 TRAINING_MANIFEST_BYTES_SHA256 \
  --output /new/evaluation-store
```

The training-manifest checksum is supplied by the operator, not accepted from an
API upload. The evaluator recomputes original admission and the dataset freeze,
then checks service/profile/features/policy/row hashes, model checksums/library
versions and thresholds before prediction. Candidate directories resolve by the
existing service hash under the trusted root; sender-local absolute paths in the
training manifest are not followed. No model is fitted, activated or deserialized
before the original-source gate.

[Protocol v1](../../configs/local_model_evaluation.json) must already exist with
the same semantic content in each model's fitting commit. It fixes the 1% healthy
calibration-window budget, two-window entry/three-window exit persistence, 300s
matching/horizon, risk threshold 0.8 and positive p95-slope control. Changing it
after fitting is rejected. This shared window budget is not a certificate that
event-level false-incident rates match; the report exposes measured rates for
that review.

Development vectors alone produce predictions; outcomes are joined afterward.
Missing/censored risk labels remain unavailable. Group denominators include
independently supported gradual runs even when no usable model frames exist.
Each service reports MAD/IF counterfactual event counts, XGBoost/slope curves,
precursor recall and selected/undefined counts. For Q-07,
`precursor_recall_lead_ge_60s` keeps every independently eligible precursor run
in its denominator and requires at least 60 seconds of counterfactual lead.
Late detections and misses are reported separately; the existing any-lead
`precursor_recall` remains unchanged. These remain replay metrics, not live alerts.
Average precision is the primary
paired risk comparison, with trapezoidal PR-AUC separately named: these are
different integrals, and linear interpolation can be optimistic
([scikit-learn definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html)).
Neither convention silently grants Q-07/G3 acceptance.

Uncertainty resamples complete acquisition groups with replacement, using the
same sampled groups for each paired comparator (1,000 replicates, seed 42, 95%
percentile intervals). It reports undefined replicas and requires at least five
development groups. Overlapping windows and three services do not multiply the
independent-group denominator; distinct contexts still do not prove IID sampling.

The output is a new content-addressed store containing one prediction table and
one model/data/protocol/code-pinned report. Keep the original corpus and candidate
alongside it to resolve source refs; it is not a standalone raw-data archive.
These are reusable model-comparison artifacts, not service smoke/PASS reports.
The report's actual `evaluated_at` is separate from its **counterfactual** feature
decision clocks. Replay assumes instantaneous inference at recorded feature
cutoffs; its detector delays and precursor leads are not historical model alert
times, online MTTD or NFR performance measurements. Censored event follow-up is
reported. Actual timing, matched event-level budgets, source parity, model cards,
final evaluation and all remaining gates need their own evidence.
`G3_accepted=false`, `activation_allowed=false` and `holdout_opened=false`
remain explicit regardless of numerical results. Final data is never read.

Focused software regression:
`python -m pytest -q tests/test_local_model_evaluation.py tests/test_local_data.py`.

## Portable H1 candidate preparation

After admitted fitting and development evaluation, create a new handoff directory:

```sh
python -m scripts.export_model_candidate \
  --store /trusted/corpus --dataset-ref artifact://manifests/DATASET_HASH.json \
  --models /trusted/candidate --training-manifest-sha256 TRAINING_MANIFEST_BYTES_SHA256 \
  --evaluation-store /trusted/evaluation --evaluation-ref artifact://manifests/EVALUATION_HASH.json \
  --output /new/handoff
```

The source freeze is reverified before model/evaluation assets are accessed.
Training/evaluation/model versions, row provenance, source/profile/policy and
byte checksums must agree. Existing bundle verification checks library versions
and model artifacts. All roots are trusted operator inputs; this CLI is not an
API upload handler. Output is exclusive and original inputs remain unchanged.

- `runtime/models/<service-hash>/` contains the three unchanged v2 bundles.
- `audit/` contains the original training-manifest bytes, frozen source report,
  pinned evaluation/predictions and content-addressed machine model cards.
- `handoff.json` lists relative runtime paths, hashes and card refs. Historical
  absolute paths inside the original training manifest are provenance; use the
  relative handoff entries when materializing on B's host.

Copy the whole tree and verify its hashes after transport. Keep `audit/` with
the operator; mount the `runtime/` tree for serving only after the existing
approval gates are met. The descriptor is `unapproved-model-candidate/v1`;
the registry rejects it as a serving release. Separate operator validation
produces the existing `validated-model-release/v1` ledger and its approved
evaluation refs. Export creates no approval or activation ledger.

When A supplies a local handoff archive, receive its matching `SHA256SUMS` over
the agreed trusted transfer channel. In the directory containing both files:

```sh
sha256sum -c SHA256SUMS
mkdir handoff
# Replace PATH_TO_ARCHIVE.tar.gz with the exact verified archive filename.
tar --no-same-owner -xzf PATH_TO_ARCHIVE.tar.gz -C handoff
```

The extracted root contains `handoff.json`, `runtime/` and `audit/`; B uses these
relative paths rather than A's original absolute training paths. The checksum
checks transport integrity; source replay, model quality and activation remain
separate gates. Preparing this package locally does not publish model/audit data.

Original corpus/Git objects remain required for full source replay and are
declared external dependencies. This candidate package contains no raw/final
data and makes no source-parity or model-quality acceptance claim. Actual H1
readiness still requires G2/G3/parity evidence and B's consumer verification.
Model cards/evaluation artifacts are retained for review and reproduction;
no service smoke/PASS report is generated.
