# Objective SLO health — verified source replay

Status: health producer and captured-source replay **PASS**; continuous live worker, correlated IER writes, RCA accuracy, model and action acceptance remain open. Executed 2026-10-26 from clean committed implementation/policy `9c0ef671ff4c2f5b39aa6153af4298de82826632`.

## Actual inputs and results

The [health producer](../runbooks/SLO_HEALTH.md) independently verified raw→normalized parity and read the immutable serving manifests of the existing [healthy S0](2026-10-26-healthy-pilot.md) and [gradual S3](2026-10-26-gradual-pilot.md) pilots. No fault schedule, target, scenario label or experiment truth store was supplied to processing. The S0/S3 names below identify inputs for reporting only.

| Captured source | Raw captures / observations | Bucket times | Signals | Eligible / UNKNOWN | Incident openings | Recovery events |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| S0 pilot | 30 / 30 | 10 | 30 | 27 / 3 | None | None |
| S3 pilot | 60 / 60 | 20 | 60 | 57 / 3 | Checkout, Payment | Checkout only |

Each initial partial/ineligible bucket remains UNKNOWN, one per service per capture. Missing/invalid data was not substituted with healthy scores. Both source stores have zero quarantined captures; synthetic negative tests additionally verify missing buckets, quarantined endpoints and complete source-query outage.

In S3, Checkout and Payment each have first sustained violating bucket start `03:29:00Z`. The second violating bucket ends `03:31:00Z`; the fixed bucket-end +60-second replay policy confirms at virtual decision time `03:32:00Z`. Inventory has no incident opening. These are objective SLO rule results from captured Prometheus measurements, not Isolation Forest or XGBoost outputs.

Checkout's normalized buckets ending `03:34`, `03:35`, `03:36` are eligible/clean, producing a recovery event at virtual `03:37`. Payment's `03:34` bucket still has captured histogram p95 ≈1092.708ms, followed by only two clean buckets; it remains active/recovering at capture end. No recovered IER or successful remediation action was written. The earlier pilot's independent unsampled-log results remain separate; histogram-derived measurements and request-log label evidence are not interchangeable certification.

## Execution and integrity

The CLI was run separately against both real stores, twice with exclusive output directories. Source replay takes place in a disposable store and preserves the original raw/normalized/feature artifacts. All 90 signal objects and both content-addressed reports were read back, checksum-verified and compared with their corresponding second executions. Signal refs/IDs and complete report contents are identical.

```bash
.venv-ml/bin/python scripts/replay_slo_health.py \
  --source-store artifacts/experiments/run-94c146e96e3f4ae0a0c5e98755db3550/serving \
  --manifest-ref artifact://manifests/bc4c790e504db574892f0d0fb0ba50cb875e9d36961a7c41e6d6a00023202ee7.json \
  --output artifacts/incidents/slo-health/s3-v1
```

All four actual CLI executions exited 0. The [machine-readable evidence](evidence/2026-10-26-slo-health-replay.json) includes source/normalization/policy identities, report refs, signal-list hashes, event/counter denominators and final health. S0 report hash: `93528175c8ffb477e112531c5ef0ee114712a2500f5c05c15c82c6529509644a`; S3: `604fa0a5cb1276dc449acc955f2e7ab91a590b7754698b7bb716465fdd5de059`.

## Software verification and limits

- 18 focused state/replay tests passed, including cutoff/ingestion/no-traffic/stale, duplicate/revision/gap handling, retained active incidents and raw/manifest integrity failures.
- Full regression: 299 tests passed. 20 schemas, 15 samples and the actual frozen training-data policy validate. Installed-wheel module/schema/API smoke passed; the final wheel check also executes the health producer and its semantic dispatch outside the checkout.
- Docker CI now consumes newly captured Step 4 telemetry through the health producer, asserting raw parity and an actual Payment SLO opening while model/action flags remain false. Its exact-head outcome is tracked separately; declaring this step does not prove a future run passed.

`evaluated_at` is a virtual replay decision clock, not an actual historical alert emission. No online MTTD, precursor recall, RCA top-k, recovery success or production accuracy is claimed from two development captures. Finite capture bounds do not prove continuous outage monitoring. Source data and model gates remain unchanged/not-ready. Next: consume these explicit SLO signals with conservative correlation/evidence-linked RCA, then integrate durable incident processing under its own checks.
