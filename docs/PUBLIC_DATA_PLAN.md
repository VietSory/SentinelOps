# Public training data — frozen selection and execution plan

The owner-approved [ADR-011](adr/ADR-011-frozen-gamma-rcaeval-contract.md) and machine-validated [training_data_contract.json](../configs/training_data_contract.json) are authoritative. GAMMA v1 is the primary training source; RCAEval is the second and final public source for RCA validation and heldout evaluation. Public research does not automatically satisfy live G2/G3/G7. Local pilots remain integration evidence, not the primary training corpus.

## Frozen source roles

| Source | Identity | Role / boundary |
| --- | --- | --- |
| [GAMMA](https://www.kaggle.com/datasets/gagansomashekar/microservices-bottleneck-detection-dataset) | Kaggle v1, version ID 7638732; publisher Gagan Somashekar; declared Apache-2.0 | Primary IF/statistical training; XGBoost only after independent evidence supports its unchanged precursor target. Controlled DeathStarBench/Kubernetes bottlenecks, not production incidents. |
| [RCAEval](https://huggingface.co/datasets/phamquiluan/RCAEval) | `phamquiluan/RCAEval` at `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`; declared MIT | RCA development/validation and frozen holdout; no ML fitting, preprocessing or threshold calibration. Modality gaps remain explicit. |

Do not concatenate sources, invent cross-system joins or rename native services as SentinelOps services. A matching metric name alone does not prove equivalent instrumentation, sampling or units. Do not add a third dataset or change the target after a failed score. SMD's [single-machine report](reports/2026-10-26-public-smd-if.md) remains historical evidence; do not expand/promote it into v1 training.

## Acquisition and capability audit

1. Freeze version/inventory/file names before model comparisons. Acquire only data and relevant metadata, with individual/total compressed and expanded size limits. Never execute provider scripts such as `rename.sh`.
2. Preserve immutable raw data under ignored `data/raw/public/` with exact bytes, SHA256, source URLs and version identity. Verify independent publisher checksums when supplied. Otherwise identify locally frozen hashes and ZIP CRC honestly; neither is a publisher-signed checksum.
3. Audit request-type/RPC/node/service placement, time/duration units, cumulative counters/resets, sampling, missingness, empty files and ordering. Keep labels, args, injected targets and schedules outside model input.
4. GAMMA's processed preview repeats metrics across request rows and includes per-RPC timestamps and labels. Build causal service/time observations rather than fitting each trace row as an independent window. Cumulative counters are not instantaneous percentages.
5. Compare raw and processed collection. Graph variants and file order do not establish chronology or capture independence. The published normal-trace share is not a healthy-window or independent-run count.

Existing RCAEval acquisition/audit:

```bash
.venv-ml/bin/python scripts/download_public_data.py --dataset rcaeval
.venv-ml/bin/python scripts/audit_public_data.py --dataset rcaeval \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --output artifacts/research/public-datasets/rcaeval-audit.json
```

The initial three-case/735-index acquisition remains capability evidence. The same three admitted development cases now also have [native source-to-policy execution](RCAEVAL_NATIVE.md) and a [measured abstention report](reports/2026-10-29-rcaeval-native-development.md); no additional telemetry case was opened and no qualified RCA accuracy is established. The pinned release has no traces for RE2/RE3 Sock Shop and no logs for one RE2 Train Ticket case. Missing modalities cannot become fabricated evidence.

GAMMA's first capability scope is the first preselected CPU/200-RPS experiment, not a training/holdout selection. It includes all three published processed request types, graph-path metadata, args, four selected raw resource metrics and placement/RPC maps. Reproduce the exact 139-file scope using project-frozen hashes:

```bash
.venv-ml/bin/python scripts/download_gamma.py \
  --file-lock configs/gamma_v1_audit_files.json \
  --result-file artifacts/research/gamma-acquisition.json
.venv-ml/bin/python scripts/audit_gamma.py \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --output artifacts/research/gamma-audit.json
```

The result file supplies `DOWNLOAD_HASH`. New result/audit paths are exclusive and do not overwrite prior runs. The downloader bounds metadata pages, wire/expanded bytes and worker count, validates single-file ZIP size/CRC and refuses scripts/path escapes. The committed lock records **project** hashes, not independently published file checksums. [The measured initial audit](reports/2026-10-26-gamma-source-audit.md) records source defects and readiness blockers. Acquisition success is not permission to fit a model.

The [causal raw-resource windows](GAMMA_RESOURCE_FEATURES.md) slice is measured and replayed under the separate `gamma-resource-5m/v1` research identity. Its raw counter/gauge features do not require guessing RPC mapping or HTTP error fields. The subsequent [native interference-label audit](reports/2026-10-26-gamma-interference-labels.md) adds exactly three original VM phase logs while preserving all 139 hashes; [142-file exact replay](GAMMA_INTERFERENCE_LABELS.md) is now available. Its 23 eligible no-recorded-interference reference rows share one cutoff, so they do not establish sufficient independent healthy training data. The [complete archive catalogue](reports/2026-10-26-gamma-archive-catalog.md) enabled a predeclared [194-case raw-resource cohort](reports/2026-10-26-gamma-resource-cohort-acquisition.md): all 30,364 selected original members are now acquired, independently read back and tree-hash locked. Source-incomplete and contradictory cases remain visible for semantic admission. [Previously inspected development cases](../configs/gamma_development_inspections.json) and their connected session groups must stay out of final holdout. Eligible vectors and intervention labels still require healthy-reference admission, independent sessions and grouped splits before fitting.

The additional [client-capture audit](GAMMA_CLIENT_CAPABILITY.md) reads exactly 48 original small files from four whole quarantine groups. [Measured execution](reports/2026-10-30-gamma-client-capability.md) accounts for all 24 log/integer pairs and finds a count mismatch in each. Aggregate printed request/histogram totals and unitless integer files do not establish per-bucket SLO/healthy/precursor evidence; source roles, group/split assignments, feature definitions and G2/G3 requirements remain unchanged. This bounded result is not a claim about every GAMMA modality.

## Dataset freeze and modeling

The [measured native admission](reports/2026-10-26-gamma-native-admission.md) accounts for all 194 cases: 101 native-evidence admitted and 93 quarantined. [Group constraints](../configs/gamma_source_groups.json) freeze 19 conservative groups and full inspected-group exclusions; seven groups are candidate final holdout. The subsequent [whole-group split](../configs/gamma_resource_splits.json) was committed before cohort window generation. [Measured dataset support](reports/2026-10-26-gamma-grouped-dataset.md): 29,610 candidate / 22,771 feature-eligible windows. Reference proxies provide only 8–26 train and 8–23 calibration rows per native entity, without health/traffic certification; model fitting remains not-ready. Keep the source, split, quality rules and target unchanged rather than lowering gates or treating all reference rows as healthy.

1. Group all modalities, request types, graph variants, derived copies and overlapping captures from one experiment/session before windowing. Freeze a deterministic grouped development/validation/test partition and temporal embargo where continuous boundaries require it. Report independent run counts. No row-random splits.
2. Define a source-specific feature registry after capability audit. Fit verified healthy training windows only; use healthy validation for IF/MAD calibration under the same false-alert/persistence protocol. Freeze artifacts and thresholds before test scoring. No point adjustment. Report exclusions/missingness and uncertainty by experiment.
3. Keep XGBoost's target: gradual degradation **before independently measured SLO breach**. Bottleneck flags, warmup and phase names alone do not establish it. Require eligible healthy/benign negatives, gradual positives and independent development runs. Freeze evidence-backed labels/SLO before model comparison. Missing evidence means `not-ready`, not a current-fault classifier under the risk name.
4. [RCAEval partitions](RCAEVAL_PARTITIONS.md) now freeze all 735 cases into conservative interval/fault-family groups: 716 development, 8 validation, 11 sealed holdout. Acquisition and manifest reads block holdout before raw access; [the metadata-only replay](reports/2026-10-28-rcaeval-partitions.md) proves grouping, not accuracy or independent sessions. Keep evaluator labels/root-cause filenames out of diagnostic input. Report localization and abstention separately from anomaly/risk. Cross-source ML evaluation requires equivalent features and never permits fitting on RCAEval.
5. Preserve live `http-red-5m/v1`: no p90-to-p95 substitution, sampled-trace request denominators, gRPC-to-HTTP status renaming or missing-error-to-zero conversion. A public feature set needs its own compatible live adapter and transfer evaluation. No public bundle activates automatically.

## Required evidence and failure behavior

Retain source inventories/revisions/checksums, policy hash, admitted/excluded experiments, feature definitions, split/overlap checks, train-only transforms, label/SLO evidence, code/library versions, frozen model/threshold hashes and metric denominators. Raw data and model binaries stay ignored; sanitized reports/manifests belong in Git.

Failed gates do not authorize source replacement, relaxed labels, test tuning or a misleading completion claim. Keep the frozen pair and report the failed/not-ready gate. Changing these decisions requires an explicit owner scope change plus a new ADR/contract version. Continue independent implementation whose dependencies are satisfied.
