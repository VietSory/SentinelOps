# SMD Isolation Forest / statistical control protocol

Historical experiment under ADR-010's original source choice, superseded by [ADR-011](adr/ADR-011-frozen-gamma-rcaeval-contract.md). SMD is excluded from v1 training and will not be expanded. This offline one-machine result does not activate serving, establish full-SMD generalization, or provide a trained XGBoost model. Reproduction now requires the explicit flag below and is restricted to the original machine.

## Frozen inputs and split

- Original SMD publisher, revision and checksums come from `configs/public_datasets.json` and its verified download manifest.
- Machine selection is fixed to `machine-1-1`, the first lexicographic machine, before model scoring. Published audit counts alone do not choose a different entity.
- `configs/public_smd_benchmark.json` was committed before the first benchmark run. Official train is split chronologically: first 70% fit / remaining 30% calibration. The original test remains the evaluation partition.
- For the audited 28,479 official train rows, the resulting fit/calibration counts are 19,935 / 8,544. All 38 channels retain their original order under research identity `smd-raw38/v1`. There is no claimed mapping to the five live RED features.
- The supplied train is unlabelled; it is treated as a nominal reference distribution, not certified perfectly healthy. No test labels enter preprocessing, fitting or threshold selection.

## Models and calibration

IF uses seed 42, 256 estimators and max_samples 256. RobustScaler median/IQR is fitted only on the fit prefix. Missing/nonfinite values are rejected, not imputed from future rows.

The control is maximum absolute deviation from the fit-prefix median divided by `max(1.4826 × MAD, 1e-6)` per channel. Each method independently takes the 99th-percentile calibration score, using the higher order statistic; an alert requires score strictly above threshold. This is a calibration exceedance budget, not a claim of measured 1% false-positive rate on a certified normal population.

The implementation saves the actual model binary and hashes, preprocessing, thresholds, source/protocol/code/library identities and split ranges **before** parsing test features/labels for scoring. A dirty source checkout is rejected. Output directories cannot overwrite previous attempts. Input tables must be covered by the publisher-verified manifest.

## Test reporting

Report both methods without choosing a winner from test labels. Metrics are raw point precision/recall/F1, average precision, ROC AUC and false positives per 1,000 labelled normal points, with TP/FP/TN/FN and point denominators. There is no point adjustment. No seconds-based MTTD is claimed because this audit has not established sampling interval semantics.

The first test evaluation establishes a baseline. It is not a validation set for subsequent threshold search. Further model selection needs separately frozen development entities/protocol and untouched holdout machines. A population confidence interval cannot be inferred from one machine.

```bash
.venv-ml/bin/python scripts/train_public_smd.py \
  --historical-reproduction \
  --manifest data/raw/public/manifests/6a3dbe8fd39a4612c24c53cd57690a14a78ac1df8100b82bf68ee94d9f23b106.json \
  --output models/public/smd-machine-1-1-seed42-v1
```

All raw/model/prediction files remain ignored artifacts. Commit only the sanitized report and references after execution. Bundles explicitly set `activation_allowed=false`; the API remains fail-closed without a compatible validated live activation. Binary anomaly test labels alone are not used to fabricate project precursor/SLO labels for XGBoost.
