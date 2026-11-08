# Model training, review and serving

The [accepted local HTTP source](../adr/ADR-014-proposed-local-training-evidence.md)
is the primary source for prospective fitting under `training-data/v2`.
Use the [corpus runbook](LOCAL_HTTP_CORPUS.md) to register acquisitions and audit
original observations with `scripts.audit_local_corpus`. The source verifier
checks per-service healthy and precursor evidence, independent groups and
chronological splits. `scripts.train_models` verifies the admitted dataset and
immutable freeze before constructing estimators. Audit the selected original
store to determine its readiness.

`scripts.audit_model_readiness` describes historical GAMMA v1 reproduction.
Its grouped resource report has zero certified live business-service health or
precursor support and remains ineligible for fitting. RCAEval is reserved for
RCA evaluation. Caller-written PASS flags and arbitrary evidence references
cannot authorize fitting or serving.

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
parity and evaluation refs. The registry checks those approvals and pinned metadata
for every route before loading model weights. Fixture origin is rejected even with
forged PASS flags.
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

## Admitted local HTTP bundles

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
Both risk curves use the same windows with a verified label and an available
prediction at the recorded counterfactual cutoff. The report counts labelled
windows that missed the prediction deadline separately. Average precision is
the primary paired risk comparison, with trapezoidal PR-AUC reported separately.
They use different integrals, and linear interpolation can be optimistic
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

The extracted root contains `handoff.json`, `runtime/` and `audit/`. From the
implementation checkout, verify the received candidate using the ID supplied
by the trusted sender:

```sh
python -m scripts.verify_model_candidate --candidate handoff --expected-id CANDIDATE_ID
```

The default check verifies the file inventory, route identities, hashes and
review metadata without loading the model weights. For a trusted candidate, add
`--replay` in the target environment with the bundle's pinned library versions
to reproduce all stored development scores and flags. The result includes the
three event denominators for review. Target telemetry parity, G3 approval and
activation remain separate decisions. Keep the audit tree with the operator.

Original corpus/Git objects remain required for full source replay and are
declared external dependencies. This candidate package contains no raw/final
data and makes no source-parity or model-quality acceptance claim. Actual H1
readiness still requires G2/G3/parity evidence and B's consumer verification.
Model cards/evaluation artifacts are retained for review and reproduction;
no service smoke/PASS report is generated.

## Detector-event development protocol v2 (ADR-015)

`local-model-development/v2` reuses the v1 candidate gate unchanged: frozen
corpus, rows, checksums, thresholds and the v1 protocol committed before fitting.
It then predicts with the same bundles on a frozen ADR-015 detector-event epoch:

```sh
python scripts/evaluate_detector_events.py --store /trusted/corpus \
  --dataset-ref artifact://manifests/DATASET_HASH.json --models /trusted/candidate \
  --training-manifest-sha256 TRAINING_MANIFEST_BYTES_SHA256 \
  --detector-store /trusted/detector-epoch --detector-report-ref artifact://manifests/DETECTOR_HASH.json \
  --output /new/evaluation
```

Each service reports:

- `abrupt_detector_events`: IF and MAD events matched to independent abrupt SLO
  onsets with the v1 deadline, persistence and budget, plus a paired whole-group
  bootstrap of IF minus MAD event F1;
- `gradual_detector_events_v1`: the unchanged v1 result;
- `gradual_detector_events_precursor_separated`: episodes entered on a verified
  v1 risk-positive window are counted as `precursor_interval_episodes`. They are
  never true positives, and those windows leave healthy service time.

The gradual groups were already inspected, so their re-accounting is not
independent evidence.

Use the evaluator's printed `report_ref` and the same output store to render
the owner's review packet:

```sh
python -m scripts.g3_review_packet --candidate /verified/handoff \
  --v2-store /new/evaluation --v2-report-ref artifact://manifests/REPORT_HASH.json \
  --output packet.md
```

The packet records the exact v2 report reference for the owner decision. It
adds a source diagnostic: the training healthy p95 centre/IQR compared with the
development healthy spread. IF is invariant to per-feature affine scaling, so a
narrow training range, not the scaler, explains drift flags. It decides nothing. Risk/XGBoost evidence stays on the v1 gradual protocol.
The report keeps `G3_accepted=false` and `activation_allowed=false`. G3 remains
an owner/reviewer decision over all of these results.

## Operator serving release

`validated-model-release/v1` is built only by an operator, after two decisions
taken outside software:

1. the owner accepts G3 for the exact candidate and the v2 report reference
   recorded in the review packet, based on the v1 and v2 development evidence;
2. lane B records PASS source parity on the target environment.

```sh
python -m scripts.build_model_release --candidate /verified/handoff --expected-id CANDIDATE_ID \
  --v2-store /new/evaluation --v2-report-ref artifact://manifests/REPORT_HASH.json \
  --parity-record /b/parity.json \
  --approved-by "<owner>" --decision-note "<verbatim G3 decision>" --confirm-owner-decision \
  --output /trusted/serving-release
```

The builder works in four steps:

1. Reverify the candidate with `verify_model_candidate`; add `--replay` to also
   reproduce development scores.
2. Load the exact reviewed v2 report by checksum. Check its candidate pins,
   protocol and abrupt evaluation evidence. Check that the parity record names
   the same three model versions and source profile.
3. Copy the pinned bundles into a **new** store, so the received candidate stays
   reverifiable, and write one approval per route that cites all evidence.
4. Load the result with `ModelRegistry.load` before publishing the directory
   atomically.

It activates nothing. B points `SENTINELOPS_MODEL_STORE` and
`SENTINELOPS_MODEL_RELEASE_REF` at the printed release, for both API and workers,
using a fresh `--stream-epoch`.

The source parity record (`model-source-parity/v1`) is produced by B. Every field
is required, and no extra fields are allowed:

```json
{"parity_version": "model-source-parity/v1", "candidate_id": "<64 hex>",
 "environment": "<target environment>", "source_profile": "prometheus-increase-60s-5s/v1",
 "models": [{"service": "checkout-service", "model_version": "<64 hex>"}, "... all three routes"],
 "status": "PASS", "verified_by": "<operator>", "verified_at": "<UTC ISO-8601>",
 "evidence": ["<refs to replay output, live feature/source comparison>"]}
```

A failed or partial parity check is reported as such. It is never written as PASS.

A first exports the frozen training source shapes, which contain no observations:
`python -m scripts.export_source_shapes --corpus /frozen/corpus --output shapes.json`.
A sends the printed SHA-256 over a separate trusted channel. Then
`python -m scripts.check_model_source_parity --kind KIND --candidate /verified/handoff
--expected-id ID --source-shapes shapes.json --source-shapes-sha256 SHA --environment NAME
--verified-by NAME --output /new/dir` produces that record from an owned Kubernetes sandbox with five healthy minutes.
Its criteria C1–C6 are fixed in the script docstring before measurement: replay,
training source shape, bucket eligibility and lag, count agreement within 5%,
same-bucket p95, and eligible live features with finite scores. Any failed
criterion yields `status: FAIL` with the failures listed. It refuses to run beside
an active corpus capture.
