# Step 4 — Telemetry and deterministic feature pipeline

Status: contracts/transforms, local Prometheus adapter and capture/replay CLI are implemented. Live acceptance results are tracked in dated reports. This phase does not train models or implement RCA/remediation.

## Contracts and code

- `contracts/observation-envelope.schema.json` wraps an unchanged TelemetryPoint when a bucket is eligible, and records explicit quality/availability/provenance when it is not.
- `contracts/feature-envelope.schema.json` wraps an unchanged FeatureWindow or an explicit abstention.
- `sentinelops.telemetry.core` implements shared normalization, histogram quantile and batch/online/replay window transforms.
- `sentinelops.telemetry.prometheus` uses an exact pinned metric/query catalog, versioned scrape coverage (current profile: 5s), a 30s watermark and immutable query responses.
- `sentinelops.telemetry.store` persists content-addressed objects with checksum validation and atomic create-without-overwrite semantics.
- `sentinelops.telemetry.pipeline` replays saved source captures without network access and quarantines invalid source responses.
- `sentinelops.contracts` enforces semantic time ordering, finite values and IER evidence integrity in addition to JSON Schema.

The seven original wire schemas are unchanged (ADR-007). Supplemental metadata is never appended to a frozen strict payload. Root `contracts/` remains authoritative and the wheel build bundles those same files.

## Exact v1 semantics

Buckets are UTC aligned, half-open, 60 seconds. The five-minute feature window contains buckets with end times in `(window_start,window_end]`. Only records with `available_at <= as_of` may enter serving replay. No future bucket, ground truth, run/scenario ID or late data becomes a feature.

`prometheus-increase-60s-5s/v1` uses reset-aware distribution/count increases from Prometheus. Counts can be fractional estimates because `increase` extrapolates scrape samples; the profile does not claim exact event-log counts. Interpolated histogram p95 has bucket-resolution error. Evidence capture retains the actual query results for audit.

Normalization requires at least 30 estimated business requests and ≥80% source coverage in a bucket. Counter/histogram totals must agree within max(1 request, 2%). Empty/missing/low-traffic/invalid buckets do not generate fake healthy payloads. A percentile in the +Inf bucket abstains because the finite upper bound cannot substantiate a latency.

Feature v1 requires at least four eligible buckets out of five:

| Feature | Definition |
| --- | --- |
| `request_rate_mean_5m` | Total estimated requests / covered seconds |
| `p95_latency_ms_mean_5m` | Mean of the valid per-bucket p95 values; not global window p95 |
| `p95_latency_ms_slope_5m` | OLS slope on actual UTC bucket times, milliseconds/minute |
| `error_rate_mean_5m` | Total estimated 5xx / total estimated requests |
| `error_rate_slope_5m` | OLS slope of bucket error ratios per minute |

Input order and identical duplicates do not change feature identity. Conflicting revisions for one service/bucket must be resolved explicitly. Mixed environments/source profiles are rejected. IDs exclude audit-only observation availability/feature evaluation timestamps so rerunning identical eligible inputs is deterministic.

## Validation

```bash
python scripts/validate_contracts.py
pytest -q
python scripts/check_installed_package.py
python scripts/collect_telemetry.py --output artifacts/capture --minutes 6
python -m scripts.step4_smoke_test --output artifacts/step4/local
```

Tests cover known histogram interpolation/overflow, invalid distributions, weighted ratios, timestamp slopes with a missing bucket, 3/5 abstention, late arrival, future cutoff, duplicates/conflicting revisions, mixed sources, fractional alignment and API semantic rejection.

The sample envelope provenance uses `fixture://` and is synthetic example data. It is not measured healthy/fault telemetry or ML evidence. G1 requires the additional live capture report; G2/G3 require independent datasets and trained model evaluation.

The live smoke runs about 540 seconds at three checkouts/second through healthy,
Payment latency, Payment error and recovery, while capturing each completed UTC
minute after its watermark. It then verifies canonical fault visibility, eligible
five-minute features, exact saved-capture replay, and one matching distributed
trace/structured-log chain. Both original Payment and Inventory faults are
restored in `finally`. Do not run another fault controller at the same time.

The CLI may query historical buckets, but records actual collection availability.
Manifest replay mode is `availability-bounded-batch`: rebuilt feature snapshots
are not historical online alerts and do not substantiate detector MTTD/lead time.
Capture artefacts remain local/ignored or are uploaded as CI artefacts.

Replay a saved manifest without querying Prometheus:

```bash
python scripts/collect_telemetry.py --output artifacts/capture --replay-manifest artifact://manifests/REPLACE_WITH_DIGEST.json
```

The initial 15s profile remains decodable for historical captures. The 5s profile expects 12 scrape samples per minute; source quality still requires ≥80% and never reinterprets an old profile at the new rate. The application histogram keeps the original boundaries and adds 1250/1500/2000/4000ms bounds to resolve the 1200ms fault more accurately. Fault APIs require explicit sandbox enable and remain disabled in non-sandbox environments.

## Infrastructure evidence and sandbox controls

`PostgresInfrastructureSource` reads `pg_stat_activity` and configured connection capacity through a read-only connection. A separate InfrastructureObservation records actual server measurement time, denominator, scope and credential-free source URI. The observer excludes its own connection; it does not invent RDS HTTP latency/error values or inject this data into the business feature vector. Percentages can exceed usable non-reserved capacity when reserved slots are occupied; the record preserves that fact rather than clipping it.

Set the local sandbox DATABASE_URL in your shell, then run:

```bash
python scripts/collect_infrastructure.py --output artifacts/infrastructure/local
```

This local PostgreSQL evidence does not certify an AWS RDS/CloudWatch adapter. Service DB client spans retain parentage without raw SQL/parameters; request-instance IDs allow log deduplication independently of a reused correlation ID. Fault APIs require ENABLE_FAULT_INJECTION=true and a local/test environment; Compose enables them only in the sandbox profile and binds published ports to loopback.

See the [dated local verification report](reports/2026-10-26-phase4-local.md) for actual counts, the failed first attempt, incomplete buckets and the distinction between test execution and raw-evidence archival.
