# Phase 2 — dataset and model software verification

Implementation: `f1b997c9a0f3d07dd5e695b51451ef9e7799d253`, Python 3.11.15,
NumPy 2.2.6, scikit-learn 1.7.2, XGBoost CPU 3.1.1, asyncpg 0.30.0.
Status: **software implemented and fixture-tested; G2/G3 BLOCKED; zero approved
trained models and zero activations**. No GAMMA estimator was fitted or promoted.

## Source audit and exact support

The current readiness command recomputes accounting from the committed, measured
GAMMA grouped-resource report. It is **not a new audit of the 30,364 raw files**.
That historical cohort accounts for 194 experiments, 101 native-evidence admitted
and 93 quarantined, under 19 conservative groups. Grouping does not certify
independent experimental replications. No new primary source was substituted.

| Split | Candidate windows | Feature-eligible | Eligible reference proxies | Admitted cases |
| --- | ---: | ---: | ---: | ---: |
| Train | 7,909 | 6,058 | 590 | 54 |
| Calibration | 11,434 | 8,776 | 553 | 20 |
| Development | 5,716 | 4,456 | 227 | 10 |
| Test metadata already in committed report | 4,551 | 3,481 | 161 | 17 |
| Total | **29,610** | **22,771** | **1,531** | **101** |

There are 6,839 feature-excluded windows. The historical exclusion report records
6,839 insufficient-time-coverage and 19 sample-gap flags; flags overlap. No new
heldout raw data was opened to report the existing aggregate test counts.

Train reference proxies span 14 cases / four groups / 28 cutoffs, only 8–26
rows/native entity. Calibration proxies span 20 cases / two groups / 27 cutoffs,
only 8–23 rows/entity. Neither proxy is certified healthy, traffic-complete or
SLO-valid. Certified approved-source healthy counts are **0 train / 0 calibration
for each live business service**, versus the unchanged 300/100 requirement.
Verified independent pre-SLO precursor runs: **0**. Historical local pilots remain
integration/transfer evidence, not a replacement primary training corpus.

Source resource features are six causal CPU-core/memory-byte/network-rate features,
not the live five HTTP RED features. There is no invented CPU percentage, request
denominator, HTTP error rate, DB observation or implicit service-name mapping.
Existing counter-reset, gap, alignment, cutoff, schema and replay tests are reused.
GAMMA intervention labels are not predictive targets. RCAEval is development/RCA
evaluation only; SMD remains excluded historical research. The sealed holdout stays
closed and no final labels choose a threshold.

## Implemented software

- `ml/corpus.py`: source/role/policy admission, separate feature/label objects,
  whole-group isolation, chronology, healthy 300/100 counts, independent healthy
  and gradual support, and pre-breach label checks. Current resource proxies fail
  before table access, estimator construction or output creation.
- `ml/models.py`: actual train-only RobustScaler/IsolationForest, median/MAD,
  designated-calibration ECDF/raw thresholds, actual XGBoost fitting with train-only
  class weights, version/config/provenance manifests, serialization and checksums.
- `ml/registry.py`: trusted release loading, separately accepted G2/G3/parity
  evidence, exact source/features, quality/freshness checks, real estimator calls,
  explicit unavailable risk and fixture-promotion rejection.
- Existing `/v1/detect` shape is preserved; `/v1/risk` exposes the existing strict
  degradation-risk contract. No configured approved bundle means 503, never demo
  scores. Explicit legacy demo remains separately marked.
- `ml/evaluation.py`: one-to-one event matching, duplicate/missed alert accounting,
  emission-based delay/lead times, healthy exposure, per-run/scenario/service
  identifiers, and explicit unavailable uncertainty. No overlapping-window
  denominator is presented as an independent experiment.

Numerical tests fit real estimators on explicitly synthetic matrices, save genuine
temporary artifacts and compare loaded inference, including XGBoost API output.
These artifacts are **software fixtures**, not SentinelOps training, benchmark or
acceptance results. The baseline keeps its own median/MAD identity.

## Executed commands and results

Commands use the supported `.venv` and the Nix runtime library path documented in
the Phase 1 report. Local socket checks ran with sandbox network permission.

```sh
.venv/bin/python -m scripts.audit_model_readiness --output artifacts/phases/model-readiness-final.json
.venv/bin/python -m scripts.train_models --store artifacts/phases/blocked-training-input --dataset-ref artifact://manifests/c93ce4d56be31f6c643a6317e2764f211a6ecd0b55900bcb41bce8081e143822.json --output models/gamma-rejected
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python scripts/check_installed_package.py
```

Readiness: `BLOCKED`. Training exit code **2**, actual output:

```json
{"status":"BLOCKED","reason":"GAMMA healthy-reference/G2 gate is blocked; no estimator fitted","models_fitted":0,"activation_allowed":false}
```

`models/gamma-rejected` does not exist. Full regression: **557 passed, no skipped
tests**, one upstream deprecation warning. Contracts: **23 schemas + 17 samples +
two policy configurations**. Installed-wheel verification: **PASS** outside the
checkout. CI also exercises the optional-ML tests in the ML-enabled public-data job.

## Model/evaluation status and minimum legitimate next evidence

| Item | Status | Legitimate model metrics |
| --- | --- | --- |
| MAD baseline | Numerical/software implementation verified | Unavailable on an admitted GAMMA evaluation corpus |
| Isolation Forest | Real fit/load/inference primitives verified with fixtures | No approved trained artifact; precision/recall/F1/FPR/delay unavailable |
| XGBoost | Real fit/load/risk primitives verified with fixtures | No approved precursor corpus; false-warning/lead-time performance unavailable |
| Serving | Default fail-closed and compatibility paths verified | No activated model and no claimed production latency/accuracy |

Required next evidence: health/traffic/SLO certificates for the approved source,
300 train and 100 calibration healthy windows per modeled live service under the
existing independence/split policy, at least the required independent healthy and
gradual train/calibration runs with both classes, observed pre-SLO precursor/onset
labels, and a verified equivalent live feature/source adapter plus transfer and
G3 evaluation. Complete per-service/per-scenario comparisons and uncertainty on
those legitimate denominators before promotion. Frozen source/target/threshold
policy is unchanged. No final holdout, AWS or remediation gate is accepted.

Machine-readable accounting: [PHASE2_DATASET_AI_MODELS.json](evidence/PHASE2_DATASET_AI_MODELS.json).

## Final upstream compatibility check

New upstream main `3e43e35` was merged without rewriting history at
`b27a8c260f1408962dcdba8770df59da0eb8b5a7`. The source-admission correction and
accepted ADR-013 remain intact. Full regression after the merge: **599 passed,
zero skipped**, one upstream deprecation warning; contracts and installed-wheel
checks PASS. The canonical captured incident history also replays with both
external snapshot checksums under the merged code. Earlier actual measurements
above retain their original source SHA; later exact-head CI is a separate run.
