> Archived guide: retain its original scope for reproduction. For current work,
> use the [documentation index](../../README.md) and active runbooks.

# GAMMA causal resource feature profile

This implements the next processing slice under ADR-011. It does not change the frozen GAMMA/RCAEval pair, model responsibility boundaries, live `http-red-5m/v1`, or the pre-SLO gradual XGBoost target. It does not establish labels or permit training/activation by itself.

## Source and units

Use the four selected **raw** resource series, never resource columns attached to processed request rows. Names retain publisher-native entity identities from placement metadata; the monitoring entity `jaeger` is explicitly excluded. CPU is cumulative CPU seconds; its delta per elapsed second yields cores, not utilization percent. Memory is bytes, not percent of an unknown capacity. Network deltas yield bytes/second, not HTTP request rates.

These units follow the original [cAdvisor metric definitions](https://github.com/google/cadvisor/blob/master/docs/storage/prometheus.md). Raw time/value arrays use the [Prometheus API's Unix-second timestamps](https://prometheus.io/docs/prometheus/latest/querying/api/). Millisecond/microsecond clocks are rejected at source admission rather than guessed/scaled. RPC latency units and node/service alignment remain separate unresolved questions and are not required or fabricated in this raw-resource-only profile.

## Cutoff, coverage and missingness

[gamma_resource_profile.json](../../../configs/gamma_resource_profile.json) freezes six ordered numeric features under `gamma-resource-5m/v1`. Buckets are 60 seconds, lookback 300 seconds, step 60 seconds. Exclude the first 300 seconds of source warmup; each emitted window must start after warmup. This is conservative source-quality exclusion, not a declaration about the source fault phases.

- For a counter, use only consecutive observations available by bucket end. Over an admitted interval, integrate its delta/time rate across the interval's overlap with the bucket. Exclude negative-reset and >30-second-gap intervals; never clamp an unknown/reset interval into zero load. Coverage is actual admitted interval time, not row count.
- For memory, left-hold an observed gauge for at most 30 seconds. Future samples cannot refresh a stale gauge. Unknown time beyond that bound remains uncovered.
- A bucket is usable only when all four metrics cover at least 75% of its time. A window needs at least four common usable buckets. Weighted means use those same buckets and their actual covered seconds. CPU/memory slopes use actual bucket-end minutes, not compacted row indices.
- Invalid windows carry `features=null` and explicit quality reasons. No future interpolation, global scaling, ID/phase/label input or missing-to-healthy conversion is used. Appending/changing observations after a cutoff cannot change that cutoff's features or coverage.

The output conforms to the supplemental [research-resource-window schema](../../../contracts/research-resource-window.schema.json); it is **not** an API FeatureEnvelope and cannot be served under the live HTTP feature registry. Raw metric hashes, source manifest/policy/profile hashes and clean source commit support replay.

`minimum_metric_coverage` includes rejected buckets; feature eligibility depends on at least four common accepted buckets, each meeting the 75% requirement. The frame hash includes source lineage. A separate `feature_values_sha256` excludes acquisition-manifest identity so the same raw observations/profile can be compared across independent acquisitions without timestamp/manifest metadata changing the numeric feature identity.

## Execution and gates

Commit the implementation and profile first; the CLI refuses to freeze features from a dirty checkout. Acquire the source scope using its project file lock, then:

```bash
.venv-ml/bin/python scripts/build_gamma_resource_windows.py \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --output artifacts/research/gamma-resource-windows/RUN_ID
```

Output directories are exclusive. The JSONL and checksum-backed report remain ignored artifacts. Eligible features are not certified healthy or precursor samples. Independent-session grouping, source labels and development/calibration/holdout partitions still gate model fitting. Processing is event-time analytical replay: arrival/ingestion times are unavailable, so no online latency/lead-time claim or automatic activation is allowed.
