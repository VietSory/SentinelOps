# Step 5 — Experiment truth and dataset protocol

Status: manifest/labeling/dataset-freeze implementation and a bounded local
S0/S1/S3/S5 runner. Inspect the current run's original inputs/results before data
admission. Module availability or a prior successful run does not accept G2,
model quality or final heldout results.

## Training data sources

[ADR-016](../adr/ADR-016-public-production-training-source.md) authorizes only
Alibaba v2022 production traces for fitting and calibration. Follow the
[model pipeline](MODEL_PIPELINE.md) for the admitted dataset and release. Local
three-service/PostgreSQL captures measure controlled workloads for integration and
evaluation. Pinned RCAEval serves RCA evaluation only. The old GAMMA and local
training contracts are historical references.

| Source | Training/evaluation use |
| --- | --- |
| Local Prometheus, populated by service OTel metrics | Accepted v2 history supplies 60s observations for the released model's `http-relative-5m/v1` features |
| Healthy independent local runs | Source parity and evaluation on nominal traffic |
| Healthy + gradual-degradation local runs | Independent integration labels and validation of live precursor semantics |
| Unsampled business server logs | Independent raw-duration/error SLO labels; not labels derived from model predictions |
| Jaeger, structured logs, native PostgreSQL observations and actual change events | Retained RCA/evaluation evidence, separate from model features |
| Out-of-band run truth | Fault/scenario/root-cause timing for labeling/evaluation only; excluded from serving model input |
| Future AWS CloudWatch/X-Ray captures | Separate environment/source profile with parity and activation gates |

The delivery and technical specs cover S0…S5. This CLI supports S0/S1/S3/S4/S5;
native S2 uses `scripts.run_s2_h4`. Final multi-fault evaluation remains a
separate holdout. AWS activation
requires parity in the target environment. Pilot fixtures and phase labels
alone cannot establish detector accuracy or calibrated risk probabilities.

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

Warmup, invalid features and unknown truth are excluded. The legacy experiment
feature vector has five registered numeric fields. The Alibaba release derives
its separate relative feature vector from accepted v2 history. IDs, timestamps,
labels and scenario metadata stay outside both feature vectors.

## Historical dataset manifest verification

The manifest assembler below remains available to verify legacy evidence. Current
Alibaba evaluation uses the [pinned local epoch](MODEL_PIPELINE.md#evaluation).

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
python scripts/run_experiment.py --scenario S0 --split validation \
  --warmup-seconds 300 --active-seconds 300 --seed 42
```

Each invocation creates an opaque `artifacts/experiments/run-UUID/` directory. The runner holds a nonblocking file lock for the entire experiment, validates the actual Prometheus 5s scrape configuration, captures image IDs and loaded source/schema hashes, and persists the declared split before generating traffic. Keep the testbed exclusive: do not run Step 2/3/4 smoke tests, another load generator or deployment changes during an experiment. External business traffic causes failure; the lock coordinates this runner only.

S1 applies a bounded Payment latency/error fault. S3 ramps Payment latency every 30s, then observes recovery. Fault experiments require at least 120s recovery and every run requires at least 300s warmup. Default load is 3 requests/s with seeded ±5% jitter. Queue saturation fails the run and prevents unbounded pending requests. Measure actual throughput from the run receipts.

Prometheus queries run after the 30s watermark and blocking capture I/O runs off the traffic event loop. The runner archives business logs, available full business traces and optional native PostgreSQL observations when `DATABASE_URL` is explicitly provided. Control/configuration logs and phase-tagged client outcomes stay in `truth/`; serving evidence excludes control events. Raw duration logs supply SLO labels independently from histogram features.

UTC readiness is rechecked after bounded monotonic sleeps; waking a timer does not itself prove the source watermark elapsed. A UTC wait times out after 120s; only an explicit not-yet-ready watermark can trigger the bounded capture retry. Other validation errors remain failures. Failed runs retain exception class and file/function/line diagnostics in truth without exception messages, locals or source text.

The runner restores both original sandbox fault states in `finally`, including preflight failure/cancellation. It persists a failed run and partial evidence when possible; failed/dirty runs cannot enter a dataset. A hard process/host kill cannot execute cleanup: inspect both `/internal/faults` endpoints before reusing the sandbox. `--allow-dirty` is only for diagnostic pilot evidence; it does not bypass the dataset clean-source gate. Editing loaded source/contracts during collection marks the run dirty.

Use the manifest assembler above when reproducing historical corpus evidence. A
new local run is integration or evaluation evidence under ADR-016.

## S4 database lock-wait evidence

Run the development scenario from a clean checkout on its own Compose project.
The experiment ports must be free before starting the stack.

```bash
export COMPOSE_PROJECT_NAME=sentinelops-s4-development
docker compose up --build -d --wait checkout-service payment-service inventory-service postgres prometheus otel-collector jaeger
env -u DATABASE_URL python -m scripts.run_experiment --scenario S4 --split validation --all-entry-traffic --output /absolute/s4/runs
```

The runner checks that the dedicated project owns the loopback endpoints. It
applies bounded PostgreSQL locks to Payment writes and records native snapshots
through warmup, fault and recovery. The source manifest carries the raw snapshot
references alongside the existing SLO, trace and completion-log evidence.

For native lock-log evidence, add a local override when starting the S4
testbed. Use the same Compose file set for capture and teardown.

```yaml
services:
  postgres:
    command: ["postgres", "-c", "log_lock_waits=on", "-c", "deadlock_timeout=100ms", "-c", "log_timezone=UTC"]
```

The runner retains only the waiting and acquired events for the Payment table
in its own PostgreSQL database. It records the database and capture clocks
without SQL statements or bind values.

Set `DATABASE_URL` to the owned PostgreSQL testbed using a read-only monitor
role with `pg_read_all_stats`, then collect a raw observation.

```bash
python -m scripts.collect_infrastructure --lock-waits --output /absolute/evidence/store
```

The collector records the current database's waiting client count, native lock
start when available, database observation time and collector receipt time. It
rejects incomplete activity visibility. The snapshot contains no SQL text and
stays separate from the existing cluster connection-capacity observation. Capture
the downstream SLO and traces separately for S4 evaluation.

## S5 independent-fault development

[s5_development_protocol.json](../../configs/s5_development_protocol.json) predeclares a dual-latency and a blocking-error development case. Both use 300s warmup, 180s active fault and 180s recovery, 3 configured requests/s at each of the three business entry routes, and distinct fixed seeds. Source/config must be committed before capture. Its byte hash is recorded before load and checked again by the evaluator; post-outcome protocol changes cannot be accepted as the original protocol.

Both Payment and Inventory receive the configured fault independently. Direct requests keep Payment observable when Inventory prevents Checkout from calling it. The rate is per request entry, not total service throughput: downstream requests add to actual per-service volume. Server-log completeness uses observed request-start/completion identities plus top-level request provenance, retaining the 95% threshold; it does not assume one Payment call for every Checkout. Full logs and client phase outcomes stay in truth, while admitted business completions and explicitly scoped [native direct traces](../architecture/TRACE_TIMING.md) remain serving evidence.

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
