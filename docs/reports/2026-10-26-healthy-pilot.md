# Phase 5 — real healthy pilot

Status: **local runner/label/freeze pilot PASS; G2 IN_PROGRESS**. This is one S0 training run, not a sufficient development dataset.

## Provenance and measured result

- Clean serving-source checkpoint: `62452c10ca74a806c9ce59273fb8d6ef43ecef1b`; loaded module/schema hashes and actual Docker image IDs are in the run truth manifest. Independent incident-storage files were developed during collection without changing the loaded experiment/telemetry code.
- Run: `run-18d10ead1fd549298306b837dc1266a3`; `train` declared before load; seed 42; generator setting 3 requests/s with seeded jitter.
- Actual UTC interval: `2026-10-26T03:01:16.168309Z` → `2026-10-26T03:11:57.247639Z`; warmup ended `03:06:34.828046Z`. Requested durations are settings; these are measured wall-clock boundaries.
- **1,800 / 1,800** responses were 200; no unexpected outcomes. Each service has 1,800 independent server completion logs and no sustained SLO breach.
- **30 observations**, 27 valid / 3 incomplete; **18 eligible feature windows**. The initial partial scrape bucket is incomplete, not silently healthy; overall short-run observation coverage is 90%, not a sustained ≥95% claim.
- **10** complete business trace snapshots; serving logs exclude control/fault events. Optional PostgreSQL source was not enabled for this run (native DB capture was verified separately in P4).
- Both sandbox fault endpoints were read back as healthy, delay 0 after completion.

## Freeze and gate result

The loader read checksum-protected run/source snapshots and recomputed the SLO labels from the captured raw-duration server logs. The pilot freeze retained **15 rows = 5 healthy windows per service** and excluded 15 warmup/ineligible/unknown-truth rows.

Strict freeze exited nonzero for the expected missing validation/test runs, insufficient healthy train/calibration windows, insufficient independent healthy runs and missing gradual-positive classes/runs. `--allow-pilot` created `local-healthy-pilot-v1` with `training_ready=false` and the exact blockers. No model was trained.

Sanitized counts, image IDs, evidence/dataset manifest references and blockers are in [the committed summary](evidence/2026-10-26-healthy-pilot-summary.json). Raw captures/logs/traces live at `artifacts/experiments/run-18d10ead1fd549298306b837dc1266a3/`; frozen data live at `artifacts/datasets/`. Both are ignored runtime artifacts, not uploaded training data. CI separately checks the runner's inherited live telemetry path; this full phase-5 run was executed locally.
