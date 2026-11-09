# Model training, review and serving

Isolation Forest and XGBoost are trained only on Alibaba
`cluster-trace-microservices-v2022` production traces
([ADR-016](../adr/ADR-016-public-production-training-source.md)). Local systems
are evaluation only. Local-data fitting (ADR-014) and its per-service candidate,
handoff, release and parity tools were retired on 2026-11-09 with owner approval;
they stay readable in git history, and their recorded evidence (candidate
`38bcc766`, D-19) is unchanged. RCAEval remains RCA-only.

`scripts.audit_model_readiness` describes historical GAMMA v1 reproduction; it
remains ineligible for fitting.

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
`validated-model-release/v1`. Only the ADR-016 Alibaba release is servable: one
shared `sentinelops-model/v3` bundle for every business route, whose approval binds
the same model, separately accepted G2/G3, profile-v2 source parity, the fit report
and frozen config hashes, and evaluation refs. The registry checks them before
loading weights. Fixture, GAMMA and retired local-data bundles are rejected even
with forged PASS flags.
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

## Training on Alibaba production traces

Detectors are trained on Alibaba `cluster-trace-microservices-v2022` MSRTMCR
(production; research and study use per its README). Local corpora and the
detector-event epochs are evaluation only.

```sh
# 1. Immutable acquisition: one 3-minute archive per file, SHA-256 recorded at download.
#    Files 0..119 cover trace hours 0-6; the download loop retries and never overwrites.
# 2. Aggregate HTTP minutes per microservice (streams each archive, no extraction):
python -m scripts.aggregate_alibaba --raw <root>/raw --output <root>/minutes
# 3. Fit and evaluate under the frozen configs/alibaba_relative_features.json (clean checkout):
python -m scripts.train_alibaba --minutes <root>/minutes --output <root>/fit-<date>
# 4. Package a checksum-pinned bundle (sentinelops-model/v3, origin admitted-alibaba-v2022):
python -m scripts.package_alibaba_bundle --fit <root>/fit-<date> --output <root>/bundle-<date>
```

What each step does:

- **Features (`http-relative-5m/v1`):** compare a service's last five minutes with
  its own trailing median (5–60 minutes, strictly before the window): latency
  level/slope/peak and call-rate level/slope.
- **Breach labels:** latency above twice that baseline for two consecutive
  minutes. XGBoost predicts an onset within 300 s.
- **Splits:** by microservice identity (60/20/20), so evaluation services are
  never fitted.
- **5xx:** the source has no error field. Errors are covered by the SLO rule and
  MAD, not by these models.
- **Transform:** `service_windows` is the single-window reference and
  `service_windows_fast` the vectorised training path; a test keeps them equal.
- **No gates:** the bundle is never an approval or activation. G3, parity and
  release apply unchanged.

## Evaluation

| Layer | Command | Role |
| --- | --- | --- |
| 1 — unseen Alibaba services | `scripts.alibaba_controls --fit … --minutes … --output …` | Frozen config, run once; Q-05/Q-07 controls with the v1 protocol rules |
| 2 — local v2 epoch (D-21) | `scripts.evaluate_alibaba_local` | Official local result; plan frozen before capture, ≥30 healthy minutes before each fault |
| ADR-015 epoch replay | `scripts.evaluate_alibaba_legacy_p95` | Development only: profile v1 has no `_sum`, so p95 replaces mean latency |

The ADR-015 detector epoch is still audited and frozen with
`scripts.audit_detector_events`; its event, persistence and bootstrap functions in
`ml.detector_evaluation` are shared by the replay.

## Source parity and serving release

1. `scripts.check_alibaba_source_parity --kind KIND --bundle BUNDLE --manifest-sha256 SHA
   --environment NAME --verified-by NAME --output DIR` runs the services on an owned
   kind cluster and checks criteria `alibaba-parity-criteria/v1` (P1–P6), fixed
   before measurement. Any failure makes the record `FAIL`.
2. The owner decides G3 from the review packet
   ([example](../reports/G3_CANDIDATE_ALIBABA_BADF9DA0.md)).
3. `scripts.build_alibaba_release --bundle … --manifest-sha256 … --controls …
   --parity-record … --parity-verification … --approved-by … --decision-note …
   --confirm-owner-decision --output NEW_DIR` writes a new trusted store after
   checking that every piece of evidence binds the exact bundle. It activates
   nothing; select the release with `SENTINELOPS_MODEL_STORE` /
   `SENTINELOPS_MODEL_RELEASE_REF`.

Live inference needs a v2 health stream (`--source-profile
prometheus-increase-60s-5s/v2`). Before a service has five baseline minutes ahead
of a five-minute window, its model signal is unavailable, never zero.
