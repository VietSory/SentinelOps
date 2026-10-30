# G2/G3 dataset and model resolution — 2026-10-30

**Verdict: G2 BLOCKED; G3 BLOCKED; zero admitted/activated models.** This investigation began at merged main `dd12339259b4f36bd61948eb2401a5939762cd1f`, on topic branch `fix/ai-rca-evidence-blockers`. Implementation checkpoint: `d72e654` (ML `f2c5a98`, RCA `baf7a8d`). No source policy, objective, threshold, accepted ADR or frozen schema changed. No final holdout was opened.

## Measured source and scope

[Machine-readable capability matrix, source references and failed conditions](evidence/2026-10-30-gamma-blocker-resolution.json) records original manifest `cb83d36e92f9329399799444fbc45b8cab07c772c9ec9c38f8269cd790843e1e` and frozen policy hash. Actual re-acquisition verified **142 files / 141,632,879 expanded bytes** for the already inspected development experiment `cpu_aug12_25min_200_0`, metadata and three phase logs. Transport compressed bytes: 15,242,606. It includes 120 nonempty resource series / 14,163 observations, 11 graph-path files and three processed request tables. No resource counter reset was observed in this bounded sample; counter reset/gap handling remains tested by existing resource tests.

The complete ZIP64 member catalogue was independently re-read: **129,510 entries**, 194 experiment argument files, 190 processed experiment names, 182 names covering all three request types; 21,493,042 range-response bytes. This is full *filename inventory*, not verification of every member's contents. Catalogue includes 85,872 metric files, 12,870 app-pod logs, 9,652 kube-pod logs and 9,128 processed-trace members. The four experiments with full `compose/home/user_traces.json` and request latency/log files are `cpu_july27_60min_800_0`, `cpu_july28_800_0`, `cpu_july29_800_0`, `cpu_test_july24_800_0`; all are assigned **quarantine** in the existing split. Their compose traces alone are 1,098,948,869 / 867,639,412 / 1,951,988,273 / 966,583,639 bytes. Those full traces were not opened or silently admitted. Their existence prevents a claim that the entire archive has no request observations; their filenames do not establish usable HTTP truth.

Earlier complete resource-cohort evidence is preserved: 30,364 members, 194 cases, 101 native-evidence admitted / 93 quarantined, 19 conservative source groups. Counts below are recomputed from the **committed historical grouped report**, not a new read of final raw partitions. Conservative groups are not demonstrated independent experiments.

| Processed request type | Actual rows | Normal / anomaly source labels | Numeric missing cells | Backward time transitions |
|---|---:|---:|---:|---:|
| Compose | 15,279 | 8,997 / 6,282 | 0 | 243 |
| Home | 126,846 | 71,578 / 55,268 | 0 | 5,003 |
| User | 64,650 | 35,836 / 28,814 | 0 | 1,303 |

No HTTP status column was found. User table has **28,814 disagreements** between trace labels and any RPC label. Compose mapping has one malformed three-field row, 32 source rows vs 25 unique valid pairs, six duplicate pairs; Home 9 vs 8, User 11 vs 10. Numeric RPC timestamps/durations exist but this source audit does not certify their units, complete workload sampling or service mapping. Rows must not be treated as chronologically sorted HTTP requests or independent experiments.

## Source-to-feature compatibility

Classifications apply to verified bytes, not every unacquired archive member. Full path references and formulas are in the machine report.

| Target | Classification | Interpretation / derivation |
|---|---|---|
| Native resource entity | VERIFIED_DIRECT | Native filename identity; no Checkout/Payment/Inventory alias |
| CPU cores | VERIFIED_DERIVED | Δ cumulative CPU seconds / covered seconds; exclude resets/gaps |
| Memory bytes | VERIFIED_DERIVED | Covered-time weighted, left-held gauge with bounded age |
| Network bytes/s | VERIFIED_DERIVED | Δ cumulative bytes / covered seconds; not requests/s |
| CPU/memory utilization percent | SEMANTICALLY_INCOMPATIBLE | Cores/bytes without verified capacity do not yield utilization percent |
| Native RPC timing | AVAILABLE_BUT_UNVERIFIED | Recorded start/duration numbers; mapping, units, sampling unverified |
| HTTP request/error rate, p50/p95, latency/error slopes | SEMANTICALLY_INCOMPATIBLE | Available resources/RPC tables do not establish HTTP status denominators and live feature identity |
| Connection utilization, timeout/DB error counts, log frequencies | NOT_AVAILABLE in acquired scope | Logs elsewhere in catalogue remain unverified, not silently zero |

The existing resource profile defines 60-second buckets / 300-second history / 60-second step, at least four valid buckets, 0.75 covered time, bounded 30-second gaps/gauge age. Slopes use OLS on actual bucket-end minutes; only observations available by cutoff enter features. CPU/memory/net observations cannot become HTTP RED features by renaming columns. No future labels or intervention metadata were introduced into features.

## Health, precursors and exact readiness

No accepted source observations certify complete active traffic, objective HTTP SLO compliance, independent healthy groups and matching live identities. Absence of recorded interference is only a reference proxy. Phase logs certify interventions, not independently observed incident/SLO outcomes. Therefore **zero health certificates and zero pre-SLO labels were generated**. Classes remain unknown/reference/intervention-context as appropriate; normal-looking curves and missing errors are not converted into healthy observations.

| Support | Available | Required |
|---|---:|---:|
| Healthy train, each of Checkout/Payment/Inventory | 0 | 300 windows |
| Healthy calibration, each service | 0 | 100 windows |
| Independent healthy groups, train/calibration, each service | 0 / 0 | 3 / 2 under current reader |
| Verified gradual precursor groups, train/calibration | 0 / 0 | 3 / 2, both target classes and valid chronology |
| Approved model artifacts / serving routes | 0 / 0 | Source-compatible validated releases |

Historical resource windows: **29,610 candidate / 22,771 feature-eligible / 6,839 excluded**. Train 7,909 / 6,058; calibration 11,434 / 8,776; development 5,716 / 4,456; historical test aggregate 4,551 / 3,481. Only aggregates were read for test; raw final data stayed sealed. Healthy-reference proxies are 590 train rows (14 cases, four conservative groups) and 553 calibration rows (20 cases, two groups), only 8–26 and 8–23 rows/entity respectively. They are **not** health certificates or independent-run counts.

## Implemented corrections

- Added `public_data/gamma_capabilities.py` and `scripts/audit_gamma_capabilities.py`: source revalidation, bounded feature matrix with formulas/paths, exact historical readiness, per-condition support/deficit and repairability. Corrected obsolete source-audit wording claiming resource profiles/splits were not frozen.
- Closed a real ML admission hole: `gamma-verified-training/v1` caller flags and arbitrary readable evidence objects previously could enter the corpus reader. No source-backed health/precursor verifier exists. Admission now rejects even all-PASS self-attestation **before table/evidence access or fitting**. This deliberately exposes the incomplete scientific admission boundary rather than claiming the dormant structural reader is production-ready.
- Registry rejects foreign service routes and resource feature names even when the manifest claims the live version. Inference enforces the actual 300-second profile, preserving source/schema/freshness checks.
- Real IF/MAD/XGBoost fitting, serialization, checksums and inference still execute on numerical software fixtures. **No dataset/model metric from fixtures is a research result.** Existing tests protect train-only preprocessing, invalid values, corrupted bundles, forbidden fixture promotion and unavailable-model responses.

No approved model was fitted or promoted. IF metrics, XGBoost metrics, baseline benchmark, detection delay and lead time are **not available**. The minimum missing evidence is legitimate traffic/SLO health and source parity for IF, plus independent gradual histories and independently measured outcome/negative-label support for XGBoost. An accepted verifier must validate these against original bytes. Editing booleans cannot unlock training.

## Executed verification

Environment: `.venv/bin/python` 3.11.15; numerical commands used `OPENBLAS_NUM_THREADS=1` and Nix GCC/zlib `LD_LIBRARY_PATH` (recorded in the execution manifest). Actual commands:

```sh
python scripts/download_gamma.py --file-lock configs/gamma_v1_phase_files.json --include-phase-evidence --result-file artifacts/blockers/gamma-acquisition.json
python scripts/inventory_gamma_archive.py --file-lock configs/gamma_v1_archive_catalog.json --result-file artifacts/blockers/gamma-inventory.json
python -m scripts.audit_gamma --manifest data/raw/public/manifests/cb83d36e92f9329399799444fbc45b8cab07c772c9ec9c38f8269cd790843e1e.json --output artifacts/blockers/gamma-source-audit.json
python -m scripts.audit_gamma_capabilities --manifest data/raw/public/manifests/cb83d36e92f9329399799444fbc45b8cab07c772c9ec9c38f8269cd790843e1e.json --output artifacts/blockers/gamma-capabilities.json
python -m pytest -q
python scripts/validate_contracts.py
python scripts/check_installed_package.py
```

Baseline 599 passed; updated **612 passed, zero skips**, one existing Starlette/AnyIO deprecation warning, 26.15s. Contracts: **23 schemas + 17 samples + 2 configurations PASS**. Installed wheel outside checkout: **PASS**. Initial added duration tests expected a narrower exception than the existing schema supplies for reversed/empty windows; corrected expectations, preserving rejection. A subsequent label-order regression exposed a new report consistency check after label access; moved all checks before the evaluator join. Both failures are fixed; none were bypassed.

**Feasibility:** verified admitted bytes cannot satisfy current live targets. This is not proof that every uninspected archive log lacks useful information. Quarantined full traces require a separate bounded semantic/admission investigation and cannot retroactively supply missing independent SLO truth by assumption. See the [owner-review decision](AI_RCA_NEXT_DECISION.md); no new source or objective is approved here.
