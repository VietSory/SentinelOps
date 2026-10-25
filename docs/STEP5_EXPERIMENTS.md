# Step 5 — Experiment truth and dataset protocol

Status: manifest/labeling/dataset-freeze implementation; live experiment harness and sufficient independent data are tracked separately. G2 is not PASS merely because these modules or synthetic fixtures exist.

## Separation of truth and serving

Each run declares its split, opaque run/session IDs, seed, source/code/image versions, parameters and scenario before collection. Ground truth, control phases and SLO labels belong to a separate `truth/` store. Serving capture/feature objects belong to `serving/`; run/scenario/phase IDs and labels never become model vector entries.

`experiment-run.schema.json` covers running/completed/failed metadata and timestamps. Completed runs require full 300s warmup and source/log/request evidence. `dataset-manifest.schema.json` records the exact run references, feature table, source/SLO/label versions, counts, exclusions and training readiness.

The two committed samples are synthetic (`fixture`/`fixture://`), not collected data or metric evidence. Existing seven wire schemas remain unchanged.

## Independent SLO ground truth

`sentinelops.experiments.labels.measure_slo` reads unsampled server `request_completed` logs for business routes. It deduplicates stable request-instance IDs and rejects conflicting duplicates/non-finite durations. Health/internal controls never count as business failures.

P95 uses the raw-duration linear percentile. Each eligible 60s bucket needs at least 30 requests. A breach is p95 >750ms or 5xx >5%; two consecutive eligible buckets confirm it. Gaps/ineligible buckets reset persistence. The onset is the start of the first violating bucket; confirmation is the end of the last required bucket. Partial buckets outside run boundaries are excluded.

These labels do not come from IF/XGBoost output or from histogram interpolation. Label thresholds/configuration are frozen before model comparison; quality targets remain proposals until their stated gate.

## Precursor labels and feature boundary

For gradual S3 runs, `risk_label=1` is restricted to affected services after fault onset and before that service's objective SLO onset/recovery. Healthy windows get 0. Abrupt S1, post-incident and recovery windows do not become precursor positives. No-breach gradual runs retain their censored interpretation and do not substantiate a failure-time prediction.

Warmup, invalid features and unknown truth are excluded. All vectors must match exactly the five registered numeric features. IDs, timestamps, labels and scenario metadata live outside `features`; the extractor rejects extra fields.

## Dataset freeze

Splits are declared before collection and checked as train → validation → test in time. A session cannot cross splits. Single-testbed runs cannot overlap, and neither source captures nor feature IDs may be reused as independent runs. Datasets cannot mix environments/source profiles/SLO versions. Dirty or failed runs are rejected.

The default assembler requires ≥300 healthy training and ≥100 healthy calibration windows per service, ≥3/2 independent healthy train/validation runs, both risk classes and ≥3/2 independent gradual-positive train/validation runs. These are minimum development gates, not a guarantee of generalization. Release holdout/intensity coverage still follows the delivery spec.

Insufficient data requires explicit `--allow-pilot`; the resulting manifest records `training_ready=false` and every blocker. Logical dataset IDs are bound atomically to immutable contents. Repeating the same inputs preserves the original manifest/date; reusing an ID for changed contents is rejected.

```bash
python scripts/build_dataset.py \
  --run artifacts/experiments/RUN_DIRECTORY \
  --output artifacts/datasets \
  --dataset-id pilot-v1 --allow-pilot
```

The loader verifies the run's immutable truth copy and recomputes SLO labels from captured logs before assembly. This CLI requires real completed run directories; the example directory is a placeholder.

## Validation scope

Unit tests cover sustained/gapped/partial-bucket SLOs, raw-log deduplication, affected-service labels, abrupt/post-incident exclusions, vector metadata leakage, session/time/source separation, copied features, dirty sources and immutable dataset IDs. Live collection, run inventory and model quality require separate evidence; no model is trained or promoted by this phase's fixture tests.
