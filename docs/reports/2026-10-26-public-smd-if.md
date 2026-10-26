# First external-data IF/control benchmark

The models were actually fitted and evaluated on downloaded SMD data, not generated training fixtures. This is a one-machine research baseline, not live activation or a full-SMD score.

## Frozen provenance

- Training implementation: clean commit `fbc6b0f`; source SMD revision `7fb0e0acf89ea49908896bcc9f9e80fcfff6baf4`.
- Machine-1-1: **19,935 fit / 8,544 calibration / 28,479 original test points**, 38 original ordered channels. Test has 2,694 labelled anomaly points and 25,785 normal points.
- Protocol and source selection were committed before scoring. Preprocessing uses fit data only; each threshold uses calibration scores only. No test threshold search or point adjustment.
- Frozen model/threshold manifest was persisted at `2026-10-26T04:48:40.742914+00:00`; evaluation completed at `04:48:41.152113+00:00`. Model checksum and complete provenance are in [the result JSON](evidence/2026-10-26-smd-if-benchmark.json).

## Actual original-test results

| Method | Precision | Recall | F1 | Average precision | ROC AUC | FP / 1,000 normal points |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Isolation Forest | 0.3673 | 0.3630 | 0.3651 | 0.3343 | 0.7129 | 65.35 |
| Median/MAD control | 0.1628 | 0.8486 | 0.2732 | 0.3716 | 0.7980 | 455.85 |

IF has TP 978 / FP 1,685 / FN 1,716 / TN 24,100. The control has TP 2,286 / FP 11,754 / FN 408 / TN 14,031. Saved predictions reproduce the report metrics exactly. Both methods are reported; no winning threshold/model is chosen using test labels.

Calibration exceedances were approximately 0.995% (IF) and 0.702% (control), yet labelled normal-test false positives were much higher. This is evidence of a gap between nominal calibration and test behavior; a successful fit is not sufficient model quality. The control has higher recall/AP/AUC while IF has higher precision/F1 at the frozen thresholds. No general superiority is claimed.

## Artifacts and next step

`models/public/smd-machine-1-1-seed42-v1/` contains the actual model binary, frozen manifest, predictions and evaluation report; these remain ignored runtime artifacts. The model explicitly has `activation_allowed=false` and `serving_compatible=false` because its anonymous channels do not match live `http-red-5m/v1`. The API still returns model-unavailable rather than using these weights.

The next research step is coverage over the remaining publisher machines under a separately frozen expansion protocol, not threshold tuning on this consumed test set. RCAEval remains the primary external microservice/RCA corpus. XGBoost has not been trained from SMD's binary test labels because project precursor/SLO labels have not been established. Production transfer/calibration, live feature mapping and full G3/G7 remain open.
