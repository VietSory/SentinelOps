# Phase 5 — real gradual-degradation pilot

Status: **local S3 collection/label/replay pilot PASS; G2 IN_PROGRESS**. This is an independent training run with real Payment latency ramp, not a detector/lead-time or recovery-action acceptance.

## Provenance

- Run `run-94c146e96e3f4ae0a0c5e98755db3550`, clean loaded-source checkpoint `3d18b08e25962b09a007ff54ee5bd5430986ee88`; `train` declared before load, seed 43. Loaded experiment/telemetry modules and the then-existing contracts were unchanged throughout collection; API work used independent files, with shared validator changes delayed until the run completed.
- Actual UTC: start `03:16:54.677219`, fault `03:22:19.015032`, recovery control `03:33:03.871392`, end `03:36:18.919168`, all on 2026-10-26. Settings: 300s warmup, 600s gradual ramp to 1,200ms, 180s recovery, requested 3 requests/s. Reported times are actual boundaries rather than assumed settings.
- Native PostgreSQL observation source enabled; actual image IDs/source profile and immutable evidence references are in [the sanitized summary](evidence/2026-10-26-gradual-pilot-summary.json).

## Measured results

- **3,241 / 3,241 requests returned 200**. Latency alone breached the independently defined SLO; HTTP success did not conceal it.
- Payment and Checkout first sustained violating bucket: **03:29:00 UTC**; confirmation **03:31:00 UTC**. Inventory had no sustained breach. Each service has 3,241 business server completion records.
- **60 observations**: 57 valid, 3 insufficient-traffic initial partial-bucket observations; **48 eligible feature windows**. Short-run observation coverage is 95%; this does not establish sustained coverage across all supported loads/scenarios.
- **20 complete business trace snapshots** and **20 native DB observations** retained, with logs and raw captures in the serving artifact store. Control/configuration truth remains separate.
- Both original fault controls were restored to healthy, delay 0. The two full post-reset clean buckets have Payment raw p95 ≈3.265/3.645ms and Checkout ≈17.032/18.402ms. The final partial bucket is excluded from objective SLO eligibility. Only two full clean recovery buckets are present: this is not a three-bucket G6 verification or successful automated action claim.

## Dataset result and next gate

Independent log labels produce **6 precursor-phase rows each for Payment and Checkout**; Inventory's 14 eligible post-warmup rows are healthy. Post-incident/recovery affected-service windows retain null risk labels rather than being relabeled as precursors.

Combined with S0, `local-healthy-gradual-pilot-v1` has **57 rows**; healthy train counts are Checkout 5, Payment 5, Inventory 19. There is only one gradual-positive training run and no validation/test data. `training_ready=false`; all minimum counts, independent-run/calibration and holdout blockers remain recorded in its immutable manifest. No threshold selection, IF/XGBoost fitting or promotion was performed.

These are retrospective phase labels and availability-bounded batch features. Six precursor labels are not six successful live alerts or measured lead-time improvements. Raw evidence remains at `artifacts/experiments/run-94c146e96e3f4ae0a0c5e98755db3550/`, dataset objects at `artifacts/datasets/`; only filtered metadata/counts are committed.
