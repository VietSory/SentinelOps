# Group-first GAMMA resource dataset

This source-specific research dataset follows [native admission](GAMMA_NATIVE_ADMISSION.md) under the unchanged GAMMA/RCAEval contract. It does not replace the live five HTTP RED features or the pre-SLO gradual XGBoost target.

## Frozen assignments before windowing

[gamma_resource_splits.json](../configs/gamma_resource_splits.json) binds the complete source file tree, group snapshot, native event/case values and inspection/profile identities. Whole groups are assigned by this predeclared protocol before building any cohort feature/label windows:

1. Rank the seven candidate final-holdout groups by SHA256 of seed 42 and group ID; first two become test.
2. From remaining uninspected candidate groups choose two with greatest admitted-case support for calibration, seed-hash tie-break. This balances support using existing case metadata, not feature counts, window labels or model scores.
3. The group containing the initial capability case is development; supported remaining groups train; wholly unsupported groups quarantine.

Metadata allocation is 54 admitted train cases, 20 calibration, 10 development and 17 test. All 194 original cases remain assigned/accounted for, including 93 quarantined cases; no unsupported case supplies fake zero vectors. Assignment is frozen before source window generation and cannot be swapped based on resulting label counts or scores. This is an **offline grouped source benchmark**, not chronological online forecasting or a claim of independent replications.

## Separate numeric, truth and split stores

The builder verifies raw manifests/shards and the complete file tree. It verifies native case/event values against the frozen evidence hash, preventing a rehashed admission file from inventing no-interference references. It validates complete disjoint group assignments before creating output windows.

Numeric frames reuse `resource_window` and the same shared cutoff function as the original resource pipeline: four raw counter/gauge series, 300-second warmup, 300-second lookback, 60-second buckets/step, existing coverage/reset/gap rules and six ordered features. Native declared warmup above the configured exclusion fails rather than admitting unsettled history. Phase/args/group/split labels never enter numeric predictors.

- `resource-windows.jsonl`: strict existing research feature schema, including invalid/null vectors with quality reasons.
- `interference-labels.jsonl`: strict existing retrospective intervention schema, linked to exact feature-row SHA256. Peer-host context remains unknown, not healthy.
- `window-index.jsonl`: experiment/entity/cutoff, group/split and feature-row SHA256; split metadata stays outside predictor payloads.
- Content-addressed report: code/input/profile/split/evidence hashes, candidate/eligible/exclusion accounting by role/entity, separate store hashes and numeric identity independent of source label/acquisition lineage.

`no-recorded-interference-reference` is a source research proxy, **not verified operational health**. Request activity/completeness and SLO labels are unavailable in this transform; idle/missing traffic must not be counted as healthy in live acceptance. Four scoped development Nginx log files were also inspected as a bounded diagnostic: current files contain errors/Lua stack traces, while previous-container files contain kubectl errors. They do not establish duration, HTTP response-status or request-denominator mapping. That limited inspection does not prove absence across every GAMMA modality.

## Execution and next gate

Commit code and split config first; the CLI refuses dirty checkout. Output is exclusive and bounded in candidate-row count; interruption/failure cannot create a successful report or overwrite an old dataset.

```bash
.venv-ml/bin/python scripts/build_gamma_dataset.py \
  --manifest data/raw/public/manifests/COHORT_HASH.json \
  --admission artifacts/research/gamma-native-admission/RUN/ADMISSION_HASH.json \
  --output artifacts/research/gamma-grouped-dataset/RUN
```

Inspect measured feature/reference support before deciding model-fitting readiness. Keep assignments and thresholds unchanged when support fails. Model-specific evaluation, reference/health acceptance and live source compatibility remain separate; `training_ready=false`, `health_labels_available=false`, `risk_target_available=false` and `activation_allowed=false` are explicit. Tests use synthetic fixtures only for software correctness; actual training/evaluation evidence must come from the approved real source.
