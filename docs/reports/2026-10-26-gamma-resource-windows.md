# GAMMA raw-resource windows — measured processing and replay

This is feature engineering under the frozen GAMMA/RCAEval contract, not a model-training or live-activation report. The versioned [profile](../../configs/gamma_resource_profile.json) and [processing semantics](../GAMMA_RESOURCE_FEATURES.md) preserve the existing HTTP registry and XGBoost pre-SLO target.

## Actual execution

Implementation/profile were committed before execution. Clean commit `ad9a6c7` built features from the hash-locked acquisition manifest `249d60f50a99ca909be07e4f3938d1d870dc2ac81f0727f3056ac52ddd621c3a`, experiment `cpu_aug12_25min_200_0`. Profile SHA256: `777fa4d25417e0c8bb38a630aae04e143b5d607fa8e48c31574ad8bc8b9ba9ef`.

- **29 native entities; 580 candidate five-minute windows.** Jaeger's four monitoring series are explicitly excluded.
- **437 feature-eligible windows; 143 excluded for insufficient common time coverage.** Missing intervals remain uncovered and invalid vectors remain null. These are feature-quality counts, not healthy-label counts or an IF recall score.
- Six numeric features use raw CPU/network counter deltas and bounded time-weighted memory bytes. No RPC latencies, HTTP error assumptions, source labels, phase schedules or IDs enter the numeric vector.
- Coverage varies materially: `media-frontend` admits 1/20 windows and `url-shorten-memcached` 5/20, while three entities admit 20/20. This scope does not establish the live 95% source-coverage target.

Two independent executions of the original clean `c94852a` pipeline produced byte-identical frame files. The later clean `ad9a6c7` execution adds a source-lineage-independent numeric hash and still reproduces the exact same frame bytes. Frame SHA256 is `71ce820c38278094df9be1aa4cabe4da2d5615230dcc85d0e7b942a5ff4dc4ce`. The full [machine-readable report](evidence/2026-10-26-gamma-resource-windows.json) records numeric hash, per-entity counts, exclusions, raw input hashes and source/policy/profile identities.

The [contract sample](../../samples/research-resource-window.sample.json) is an actual eligible resource window from this execution. Eligibility is not a healthy-state declaration; its quality metadata includes the rejected bucket as well as four accepted buckets. The report/JSONL retain `activation_allowed=false`, unverified arrival times and `training_ready=false` at dataset level.

## Verification and remaining gates

216 tests passed in the ML environment. Targeted resource tests also pass after the final lineage/hash changes: they cover counter arithmetic, resets, stale gauges, unknown intervals, cutoff invariance and independence from actual source label/schedule fields. The installed wheel loads the supplemental schema/module and passes API smoke outside the checkout. Schema/sample validation and the remote CI replay are recorded at the final branch checkpoint.

The raw counter/gauge transform is implemented and replayed. Independent experiment/session grouping, healthy/anomaly labels, development/calibration/holdout partitions, XGBoost precursor evidence and live source parity remain open. No model was fitted or activated, and no G2/G3/G7 completion is claimed.
