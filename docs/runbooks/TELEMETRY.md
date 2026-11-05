# Step 4 — Telemetry and deterministic feature pipeline

Contracts/transforms, the local Prometheus adapter and capture/replay CLI are
implemented. Use the current capture and smoke/replay commands below to validate
a target environment. This path does not train models or execute remediation.

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

The sample envelope provenance uses `fixture://` and is synthetic example data.
Validate G1 on a current live capture; G2/G3 require independent data and actual
model evaluation. No historical PASS document makes a new environment ready.

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

Inspect the current command result and capture quality for incomplete buckets or
source errors. Retain raw/evaluation artifacts when required for reproducibility;
routine smoke PASS needs no additional Markdown report.


## CloudWatch HTTP RED adapter preparation

The read-only adapter in \`sentinelops.telemetry.cloudwatch\` implements the
technical spec's log-rollup path. It has no SDK dependency or default AWS client,
credentials, endpoint or metric-name discovery. Instantiate \`CloudWatchSource\`
with explicitly configured Logs/CloudWatch clients and an operator catalog:

```python
catalog = {
    "catalog_version": "cloudwatch-http-red/v1",
    "service": "payment-service", "environment": "aws-development",
    "log_group_arn": "arn:aws:logs:REGION:ACCOUNT:log-group:GROUP",
    "namespace": "DEPLOYED_CUSTOM_NAMESPACE",
    "request_metric": "DEPLOYED_REQUEST_METRIC",
    "error_metric": "DEPLOYED_ERROR_METRIC",
    "dimensions": {"service": "service", "environment": "environment",
                   "method": "method", "route": "route"},
    "counter_semantics": "per-request-count/v1",
}
source = CloudWatchSource(logs_client, metrics_client, catalog)
capture = source.capture(minute_aligned_utc_end)
ref = trusted_store.write("raw", capture)
```

Import \`CloudWatchSource\` from \`sentinelops.telemetry.cloudwatch\`. Catalog
placeholders must be replaced by the deployed publisher's verified mapping,
including actual dimension names. Both clients' configured region must match the
log-group ARN; metric queries pin its account. The publisher must provide
per-business-request count samples, summed per UTC minute, with observed zero
errors when appropriate. Existing cumulative exporter counters/statistic sets
are not accepted as this catalog. These are deployment prerequisites, not a
claim that this publisher or an AWS environment is currently available.

Clients require operator-configured connect/read/retry bounds. The adapter bounds
capture to 20 pages / 20 MiB / 200,000 events and retains partial/API failures for
quarantine without SDK exception text. Cloud calls need their separate authorized
scope; constructing/importing the adapter makes no call.

\`FilterLogEvents\` continues through empty pages while a token exists. The query
uses milliseconds and an inclusive end of \`bucket_end - 1 ms\`, preserving
\`[start,end)\`. Events must contain the original application completion JSON,
with CloudWatch's event timestamp equal to \`timestamp_unix_ms\`; ingestion and
actual receipt clocks are verified separately. A wrapped/transformed log format
or a collector using another event clock needs an explicitly validated adapter.
Only the three business route templates are accepted. Dedup uses source event ID
and application request-instance ID; shared correlation IDs/content do not merge
separate requests. Conflicting duplicates fail.

The adapter calculates the linear sample p95 from all deduplicated completion
\`duration_ms\` values. It never averages pod p95 values or asks CloudWatch for a
percentile of arbitrary statistic sets. Independent \`GetMetricData\` queries
must be \`Count / Sum / 60s\`, complete, with exactly one bucket and matching log
request/error totals. Missing metric data is unknown, not an observed zero.
Missing/mismatched records, partial pagination, clock/units errors and low traffic
cannot produce a healthy payload. No-traffic buckets retain undefined p95/error
ratio. A service cannot mix catalogs within a replay manifest.

Replay already captured objects without credentials or network calls:

```sh
python -m scripts.replay_cloudwatch --store /trusted/source \
  --capture-ref artifact://raw/CAPTURE_HASH.json --environment aws-development \
  --output /new/exclusive/replay
```

Repeat \`--capture-ref\` for multiple buckets (maximum 1,000). Raw captures,
observations, quarantine and five-feature windows use the existing artifact
store and schemas. Original receipts control feature availability; reprocessing
never backdates an observation. New results remain separate immutable artifacts.

\`cloudwatch-log-rollup-60s/v1\` is a **development source profile**, with the
spec's proposed 120-second watermark and exact sample quantile rather than the
local Prometheus histogram interpolation. It is not implicitly compatible with
local models; current registry guards reject that profile mismatch. Numerical
feature eligibility is not model/risk availability: this watermark exceeds the
current local forecast-anchor minute deadline. Deployment mapping, observed lag,
original source completeness, cost, source parity and a compatible release need
actual H3/G7 evidence before activation. No AWS/model/G7 acceptance is inferred
from software fixtures, and the default local worker remains Prometheus-only.

API semantics: [FilterLogEvents pagination](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_FilterLogEvents.html),
[GetMetricData units and timestamps](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricData.html),
[percentile restrictions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricStatistics.html).
