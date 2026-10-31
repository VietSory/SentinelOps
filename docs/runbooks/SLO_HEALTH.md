# Objective SLO health signals

This producer implements the health/persistence prerequisite of P7 independently of unavailable IF/XGBoost models. Its input is the strict normalized business observation contract; its [supplemental output contract](../../contracts/health-signal.schema.json) keeps `source=slo` and `model_outputs_available=false`. It does not change DetectionResult, IER or the training-data contract.

## Policy and transitions

[slo_health_policy.json](../../configs/slo_health_policy.json) records `slo-health/local-v1`: p95 >750ms or 5xx ratio >5%, at least 30 requests and 80% source coverage per 60-second bucket; two consecutive eligible violating buckets open an objective incident, three consecutive eligible clean buckets confirm observed recovery. The operational cutoff allows at most 60 seconds after bucket end. This additional cutoff is a versioned local signal policy, not a new training label, source-parity proof or production SLA.

The reducer rejects future/unaligned decisions, source/environment/policy mixing, inconsistent request ratios and observation/checkpoint identity changes. Observation availability **and ingestion** must precede the decision cutoff. No-traffic, invalid/incomplete, missing, not-yet-available or stale input returns `availability=UNKNOWN`, `health=null`. Consecutive counters reset; a previously confirmed incident remains active. A skipped bucket also breaks persistence. Rebreach during recovery returns incident health within the same episode.

Identical decision retries return the original snapshot without incrementing counters. An already evaluated bucket cannot be revised with a new time/input; out-of-order updates require explicit replay from an earlier checkpoint. Signals contain policy/input/prior IDs, the current decision time, first violating bucket start, confirmation time and bounded supporting observation refs. Hashes establish content integrity, not producer authenticity; a future live worker must durably commit/checkpoint with concurrency control before acknowledging input or emitting downstream work.

Observed clean recovery is not action verification. This producer does not authorize actions, write recovered IERs, prove readiness of a rolled-back deployment, or replace missing model scores with zero.

## Verified source replay

Commit code/policy first. Given a captured store and source manifest:

```bash
.venv-ml/bin/python scripts/replay_slo_health.py \
  --source-store artifacts/experiments/RUN/serving \
  --manifest-ref artifact://manifests/MANIFEST_HASH.json \
  --output artifacts/incidents/slo-health/RUN
```

The CLI requires a clean committed checkout and exclusive output directory. It verifies source object hashes and reproduces the exact raw→normalized manifest in a disposable store, keeping the original store unchanged. It accepts the telemetry manifest itself or an aggregate serving manifest linked to its normalization revision. It reads no experiment control/truth store and rejects extra raw capture fields such as scenario/target metadata.

Replay covers every 60-second bucket between the **raw capture** bounds, including quarantined endpoints, missing services and gaps. A complete source-query outage produces UNKNOWN signals instead of an empty healthy report. Intervals outside the supplied raw extent are not observed; the finite replay is not a continuous live outage monitor.

The output store contains immutable `signals/` objects and a content-addressed `manifests/` report. `evaluated_at` is the predeclared virtual decision clock (bucket end +60 seconds); it is **not a historical alert emission timestamp**. Reports explicitly deny historical emission/lead-time, model, RCA accuracy and action claims. A live worker must separately record its actual emission/commit time.

The CLI is a replay consumer. [Downstream correlation/RCA and computed analytical IER writing](RCA.md) have their own verified path and actual replay-emission clock. Continuous polling, durable worker checkpoints and action execution remain separate deliverables. The existing API continues to return 503 without an activated compatible model.

## Verification

Tests cover sustained entry/recovery, duplicate replay, out-of-order/revised buckets, missing/late/ingestion/stale/no-traffic inputs, implicit gaps, source/policy changes, record/manifest tampering, raw normalization parity and quarantined outage extents. The installed-wheel smoke loads the new modules/schema outside the checkout. Docker CI consumes the real Step 4 capture through this producer after collecting telemetry, without a second long workload run. Actual pilot measurements are recorded separately after clean-source execution; software fixtures are not model or operational accuracy evidence.

The [continuous local producer](HEALTH_WORKER.md) now reuses this reducer with actual UTC decisions, durable PostgreSQL checkpoints and measured crash/source-outage/recovery evidence. Its forwarded health stream is separate from historical replay and downstream continuous correlation/RCA/IER or model/action activation.
