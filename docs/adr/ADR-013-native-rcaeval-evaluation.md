# ADR-013 — Native RCAEval adaptation and guarded evidence-policy evaluation

**Status:** Accepted 2026-10-29 for the measured native development software scope;
profile/code committed before diagnostic measurement. No operational/quality G4 acceptance.
**Scope:** The three previously inspected development cases at the frozen RCAEval
revision. No validation/holdout telemetry, ML fitting, local IER writes or action
permissions are introduced.

## Context

The publisher's Parquet files retain native service names, metric columns and
trace/log formats. They do not contain SentinelOps accepted SLO decisions, approved
IF output, deployment receipts or verified online evidence-availability clocks.
The local continuous RCA producer requires those source contracts; manufacturing
HealthSignals or remapping native services to Checkout/Payment/Inventory would
invalidate the evaluation.

The pinned publisher README defines metric timestamps as Unix seconds and columns
as `{service}_{metric}`. The original Jaeger [JSON model](https://github.com/jaegertracing/jaeger/blob/v1.52.0/model/json/model.go)
defines `startTime` and `duration` in microseconds; the publisher's pinned
[trace adapter](https://github.com/phamquiluan/RCAEval/blob/2fca61b9730a69c21ef72411844735634dbb010f/RCAEval/e2e/tracerca.py)
also describes its microsecond clock. Both native trace files were checked against
the independent `startTimeMillis` column using integer arithmetic. Logs have the
same integer-second capture interval as metrics. No receipt clock is inferred.

## Decision

1. Preflight the entire manifest inventory before any raw/cache bytes. Canonical
   revision paths must match declared case selection and frozen roles. A declared
   development selection cannot conceal another case in a later file, nor can a
   different dataset label bypass the RCAEval cache guard. The native consumer
   additionally requires the exact original selected file set and trusted
   publisher-verified size/SHA256 inventory; a self-consistent caller manifest
   cannot authorize replacement bytes.
2. Stream only the existing three admitted development cases, under the
   [versioned native profile](../../configs/rcaeval_native_profile.json). Preserve
   null/NaN distinctions, integer clocks and string trace/span IDs. Retain raw bytes
   and record deterministic normalized-stream digests; never fill missing values.
3. Select a fixed trailing 300-second interval ending after the last metric second.
   Injection/root/fault labels cannot select the diagnostic window or features.
   Native identities remain exact: `frontendservice` and `frontend` are not silently
   merged. Status codes remain uninterpreted native values.
4. Keep the native result separate from local `correlation-result`/`rca-result` and
   authoritative IER contracts. A case window is not an observed SLO opening group.
   Reuse the frozen component weights and abstention arithmetic through an explicit
   native scoring entry point; local method admission stays unchanged.
5. Native trace support is the fraction of **recorded cumulative span durations**
   for an entity in the selected interval. It is not exclusive wall time, critical
   path attribution or causality. Native log support records the presence of fixed
   generic exception markers, not learned rarity, verified severity or HTTP errors.
   Marker patterns are fixed before inspecting diagnostic rankings or labels.
6. Anomaly, onset-order and deployment slots stay unavailable. Missing source receipt
   clocks force unverified availability. No weight redistribution, model fitting,
   new threshold or promotion is allowed. These native methods are excluded from
   live RCA method admission and cannot be submitted as live RCA results.
7. Freeze the business result before parsing evaluator labels. Report all selected
   cases, unavailable modalities, quarantine reasons, candidate support and
   abstention denominators. Qualified accuracy is unavailable when no prediction
   clears the gates; abstention must not be counted as a correct localization.

The two supported native modalities have at most 0.40 total component coverage,
below the unchanged 0.75 floor even before receipt-clock uncertainty. Therefore
this profile cannot qualify an operational root cause. Its purpose is to execute
and measure the real source-adaptation/evaluation path and expose compatibility
gaps; it does not validate cross-source RCA quality or close full G4.

## Verification and recovery

Require path/role rejection before raw reads, bounded decoding, exact time/ID and
missingness tests, label separation, local/native method isolation, and real
three-case source → adapter → policy → evaluator execution. Reproduce output from
the same file/profile hashes and inspect actual publisher bytes. A schema-only or
all-quarantine run does not prove the complete consumer path. Preserve earlier
local consumers, sealed partitions, training sources and thresholds; stop the new
offline evaluator on incompatible input without rewriting raw data. Every published
candidate needs its own completed CI and transported evidence.

[Measured three-case execution](../reports/RCA_DEVELOPMENT.md)
accounts for all selected cases without quarantine: three abstentions, zero
qualified predictions and identical repeated business results.

The source-admission correction
locks that inventory without changing the numerical profile, policy or selected
cases; repeated business outputs remain identical.

Method-scope integration verification
keeps the retained legacy log-only helper in its own method allowlist. It cannot
add native pattern methods to the live wire contract or admit live business-log
methods into the legacy helper. Canonical ADR-013 results and ablations are unchanged.
