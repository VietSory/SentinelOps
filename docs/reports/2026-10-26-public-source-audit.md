# Public dataset acquisition and capability audit

The owner requested external published datasets as the primary training direction. ADR-010 and PUBLIC_DATA_PLAN replace the local-primary assumption. No local load generator was running at the strategy-change check; the two completed local pilots are preserved as integration evidence. The latest interrupted run has no terminal manifest and is not admitted into a dataset.

## Downloaded and verified

| Dataset | Pinned revision | Actual acquired scope |
| --- | --- | --- |
| RCAEval | `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e` (Hugging Face) | Full 735-case index; three Online Boutique cases, one per suite; metrics and available logs/traces |
| SMD | `7fb0e0acf89ea49908896bcc9f9e80fcfff6baf4` (publisher GitHub) | `machine-1-1`: official train, test, binary test labels and license/readme |

Every file passed exact-size and publisher SHA256/Git-object checks. Immutable download manifests are retained under `data/raw/public/manifests/`; raw data and licenses remain ignored artifacts, not Git payloads. The declared data license is MIT for both selected sources; code from unrelated RCAEval baseline repositories was not downloaded or executed.

## Observed contents

- SMD machine-1-1 has **28,479 train rows, 28,479 test rows, 38 channels**, and 2,694 positive test points. This is one machine, not a result over all 28 machines. The original train has no point labels; its nominal-data assumption is recorded rather than independently certified.
- RCAEval sample RE1-OB: **4,201 metric timestamp rows / 49 channels**, no null metric cells, 2,100 pre-injection rows. Metric-only case.
- RCAEval sample RE2-OB: **1,441 rows / 72 channels**, 4 null cells, 720 pre-injection rows; retained traces contain **391,997 spans**.
- RCAEval sample RE3-OB: **1,441 rows / 69 channels**, no null cells, 720 pre-injection rows; retained traces contain **165,302 spans**.
- The full index shows no traces for RE2/RE3 Sock Shop; RE2 Train Ticket has logs for 89/90 cases. A broad description of “multimodal” is not treated as evidence that every case has every modality.

Counts above come from reading the verified files, not generated fixtures. Full sanitized schema/count reports: [RCAEval](evidence/2026-10-26-rcaeval-source-audit.json), [SMD](evidence/2026-10-26-smd-source-audit.json).

Validation: **173 tests passed** in the isolated ML environment; all 16 schemas/12 samples validated and dependency checks passed. Downloader tests cover official Git-object format, traversal/Windows paths, corruption, oversized streams, budgets and manifest integrity. CI adds a real pinned RCAEval download/audit path. The published Parquet conversion is audited as provided; equivalence to every original Zenodo CSV cell was not independently rechecked here.

## Compatibility and next experiment

Public files have not been relabeled into the five live RED features. SMD's anonymous channels need a separate research feature identity. RCAEval's injection/root-cause labels do not establish project SLO onset, gradual degradation or calibrated future risk; trace timestamp/duration units and request/error semantics still require audit.

The frozen SMD protocol selects the first machine before scoring, fits on the first 70% of official train, calibrates on the remaining 30%, and reserves the original test for evaluation. IF and median/MAD receive the same calibration exceedance budget. No point adjustment or test-label threshold search is permitted. Training/evaluation output belongs in a subsequent artifact/report; this source-audit report does not claim a trained model or G3.

Primary references: [RCAEval data card](https://huggingface.co/datasets/phamquiluan/RCAEval), [RCAEval paper](https://arxiv.org/abs/2412.17015), [SMD publisher](https://github.com/NetManAIOps/OmniAnomaly). Optional sources and dataset-specific limits are recorded in [PUBLIC_DATA_PLAN](../PUBLIC_DATA_PLAN.md).
