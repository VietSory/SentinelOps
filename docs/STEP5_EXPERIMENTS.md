# Step 5 — Experiment truth and dataset protocol

Status: manifest/labeling/dataset-freeze implementation and a bounded local S0/S1/S3 runner, with new S5 development support. Actual S5 development capture/evaluation is not accepted from implementation or fixture tests alone. Live acceptance evidence and sufficient independent data are tracked separately. G2 is not PASS merely because these modules or synthetic fixtures exist.

## Training data sources

The frozen [training-data contract](../configs/training_data_contract.json) and [ADR-011](adr/ADR-011-frozen-gamma-rcaeval-contract.md) govern [published public datasets](PUBLIC_DATA_PLAN.md): GAMMA v1 is primary training; pinned RCAEval is RCA validation/holdout only, without ML fitting. At most these two public sources are used, with no row concatenation. SMD is excluded historical research and is not expanded. The local three-service/PostgreSQL captures below are integration/transfer evidence, not the primary training corpus. They are experimental workload measurements, not production-user traffic or hand-authored metrics.

| Source | Training/evaluation use |
| --- | --- |
| Local Prometheus, populated by service OTel metrics | Live adapter/integration: closed 60s observations → 300s features at 60s steps; five registered RED/trend features |
| Healthy independent local runs | Source parity, nominal calibration/transfer evidence for a compatible live model |
| Healthy + gradual-degradation local runs | Independent integration labels and validation of live precursor semantics |
| Unsampled business server logs | Independent raw-duration/error SLO labels; not labels derived from model predictions |
| Jaeger, structured logs, native PostgreSQL observations and actual change events | Retained RCA/evaluation evidence; not automatically appended to the five-feature model vector |
| Out-of-band run truth | Fault/scenario/root-cause timing for labeling/evaluation only; excluded from serving model input |
| Future AWS CloudWatch/X-Ray captures | Separate environment/source-profile inventory, parity checks and AWS training/recalibration before activation |

Full planned coverage is S0…S5 in the delivery/technical spec. The CLI supports S0/S1/S3 and S5 development capture; real deployment/DB-pressure adapters and final multi-fault heldout evaluation remain required. Local model weights cannot be assumed valid for AWS without the source/environment gate. Collected pilot fixtures/phase labels alone do not establish detector accuracy or calibrated risk probabilities.

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

The loader verifies the run's immutable truth copy and recomputes SLO labels from captured logs before assembly. It also checks every referenced raw/normalized/feature object, reconstructs the exact normalization manifest from raw captures, rejects control truth in serving evidence, and verifies retained business traces against server logs (status, correlation/span IDs, routes and CLIENT/SERVER parentage). A hash-valid caller-authored feature is insufficient without reproducible raw lineage. Infrastructure references must validate and match the run's environment. This CLI requires real completed run directories; the example directory is a placeholder.

## Validation scope

Unit tests cover sustained/gapped/partial-bucket SLOs, raw-log deduplication, affected-service labels, abrupt/post-incident exclusions, vector metadata leakage, session/time/source separation, copied features, dirty sources and immutable dataset IDs. Live collection, run inventory and model quality require separate evidence; no model is trained or promoted by this phase's fixture tests.

## Local experiment runner

On Linux/WSL, start the Docker Compose stack and commit the source, then run from the repository root with the installed Python 3.11 environment:

```bash
python scripts/run_experiment.py --scenario S0 --split train \
  --warmup-seconds 300 --active-seconds 300 --seed 42
```

Each invocation creates an opaque `artifacts/experiments/run-UUID/` directory. The runner holds a nonblocking file lock for the entire experiment, validates the actual Prometheus 5s scrape configuration, captures image IDs and loaded source/schema hashes, and persists the declared split before generating traffic. Keep the testbed exclusive: do not run Step 2/3/4 smoke tests, another load generator or deployment changes during an experiment. External business traffic causes failure; the lock coordinates this runner only.

S1 applies a bounded Payment latency/error fault. S3 ramps Payment latency every 30s, then observes recovery. Fault experiments require at least 120s recovery; every run requires at least 300s warmup. S2/S4 are rejected until actual deployment/DB adapters exist. Default load is 3 requests/s with seeded ±5% jitter; queue saturation fails instead of accumulating unlimited pending requests. Requested rate is a generator setting, not a measured throughput claim.

Prometheus queries run after the 30s watermark and blocking capture I/O runs off the traffic event loop. The runner archives business logs, available full business traces and optional native PostgreSQL observations when `DATABASE_URL` is explicitly provided. Control/configuration logs and phase-tagged client outcomes stay in `truth/`; serving evidence excludes control events. Raw duration logs supply SLO labels independently from histogram features.

UTC readiness is rechecked after bounded monotonic sleeps; waking a timer does not itself prove the source watermark elapsed. A UTC wait times out after 120s; only an explicit not-yet-ready watermark can trigger the bounded capture retry. Other validation errors remain failures. Failed runs retain exception class and file/function/line diagnostics in truth without exception messages, locals or source text.

The runner restores both original sandbox fault states in `finally`, including preflight failure/cancellation. It persists a failed run and partial evidence when possible; failed/dirty runs cannot enter a dataset. A hard process/host kill cannot execute cleanup: inspect both `/internal/faults` endpoints before reusing the sandbox. `--allow-dirty` is only for diagnostic pilot evidence; it does not bypass the dataset clean-source gate. Editing loaded source/contracts during collection marks the run dirty.

After completion, use `scripts/build_dataset.py` as above. A single 10-minute healthy run must require `--allow-pilot`; it cannot satisfy the independent-run, calibration, gradual-positive or holdout requirements. Do not train or claim G2 from this smoke-sized dataset.

## S5 independent-fault development

[s5_development_protocol.json](../configs/s5_development_protocol.json) predeclares a dual-latency and a blocking-error development case. Both use 300s warmup, 180s active fault and 180s recovery, 3 configured requests/s at each of the three business entry routes, and distinct fixed seeds. Source/config must be committed before capture. Its byte hash is recorded before load and checked again by the evaluator; post-outcome protocol changes cannot be accepted as the original protocol.

Both Payment and Inventory receive the configured fault independently. Direct requests keep Payment observable when Inventory prevents Checkout from calling it. The rate is per request entry, not total service throughput: downstream requests add to actual per-service volume. Server-log completeness uses observed request-start/completion identities plus top-level request provenance, retaining the 95% threshold; it does not assume one Payment call for every Checkout. Full logs and client phase outcomes stay in truth, while admitted business completions and explicitly scoped [native direct traces](TRACE_TIMING.md) remain serving evidence.

The manifest requires two `root_causes_ground_truth` values for S5 and `root_cause_ground_truth=null`; other scenarios retain their legacy scalar contract. Both fault states are restored in `finally`. S5 is rejected as an ML training split and does not create gradual precursor labels. Response controls are fixed before capture: warmup/recovery and the latency profile expect 200; the error phase expects Checkout 502 and direct Payment/Inventory 503. Unexpected statuses/transport failures remain failures. No fault flag is represented as an observed deployment change.

Run a case using the exact parameters from the protocol, for example:

```bash
python scripts/run_experiment.py --scenario S5 --split validation --seed 5101 \
  --rps 3 --warmup-seconds 300 --active-seconds 180 --recovery-seconds 180 \
  --fault-mode latency --maximum-delay-ms 1200
python -m scripts.evaluate_s5 --run artifacts/experiments/RUN_DIRECTORY \
  --output artifacts/evaluations/S5_CASE
```

The evaluator rejects heldout input before opening its raw telemetry, verifies immutable run/source/log coverage and matches the frozen case parameters. Health and correlation retain their fixed virtual clocks. RCA's predeclared forensic cutoff is the maximum of correlation cutoff and operational receipt clocks; fault timing/root truth does not choose diagnostic windows or scores. Evaluation truth is used after diagnostic output to check independent groups and directly observed failure-path support. This post-capture analysis is not an online alert/MTTD claim and does not change the earlier fixed-cutoff RCA reports.

Acceptance requires independent request-log SLO control with at least three full eligible warmup buckets, separate Payment/Inventory groups, an ambiguous shared Checkout group, direct native support for both roots, and preserved RCA abstention/no-action authority. Missing roots, merged groups, bad warmup or absent direct evidence fail with retained artifacts. Model/data gates remain open. At least five heldout multi-fault trials and final evaluation remain reserved for P11; these development cases cannot replace them.
