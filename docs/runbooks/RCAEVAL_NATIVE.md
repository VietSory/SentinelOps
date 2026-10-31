# Native RCAEval development evaluation

[ADR-013](../adr/ADR-013-native-rcaeval-evaluation.md) defines this research-only path.
It uses only the three previously inspected development cases at revision
`afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`. [Frozen partitions](RCAEVAL_PARTITIONS.md),
the GAMMA/RCAEval source contract and the sealed holdout remain unchanged.

## Source and business boundaries

`load_native_inputs` admits the complete canonical manifest inventory and frozen
roles before any source bytes; it also rejects revision symlinks outside the public
root, undeclared cases and a disguised dataset label. The complete selected file set, including shared metadata, must also match the
[trusted 12-file inventory](../../src/sentinelops/public_data/rcaeval_native_inventory.py)
of the original publisher-verified acquisition. Caller-declared hashes alone cannot
authorize bytes: additions, omissions, size or SHA drift are rejected before reading
the first cache file. Each admitted file is bounded and checked against publisher
size/checksum, acquisition SHA256 and the trusted inventory. Parquet is decoded
from those retained verified bytes, avoiding a second path read after verification.

`adapt_native_case` accepts only metrics/logs/traces byte buffers. It has no argument
for case identity, root, fault, injection, local service mapping or SLO truth. The
adapter preserves integer Unix seconds for metrics/logs and microseconds for trace
start/duration, with integer nanosecond conversion and an independent millisecond
pair check. Logs and trace starts must fall within the metric capture. Trace input
order may move backwards; metric time must be strictly increasing. IDs are strings;
null parents stay null. Native nullable status codes are retained without HTTP/RPC
interpretation. Null metric cells and native NaN values remain distinct; infinities
are rejected. No imputation or certified-health inference occurs.

The event-time window is `[last_metric_second + 1 - 300, last_metric_second + 1)`.
Only spans starting within the window and finishing by its end contribute support.
The profile does not infer source receipt clocks. It records exact native entity
identities, metric channel/window statistics, modality counts and normalized-stream
SHA256 digests in source order. Log messages appear only as per-row hashes in the
digest stream; raw message text is never emitted. Raw files remain immutable.

Observed parent links use `(traceID,parentSpanID)` to find the parent service and
record parent-service → child-service counts, including unresolved links. They are
observed span relationships, not a complete topology, a guaranteed DAG or causal
evidence. Candidate identities are the union of observed native metric/log/trace
entities. `frontend` and `frontendservice` remain separate. No missing service is
invented from evaluator labels or silently mapped into the local three-service testbed.

The [frozen profile](../../configs/rcaeval_native_profile.json) limits manifest bytes
to 256 KiB, individual files to 128 MiB, aggregate files to 512 MiB, each table to
3 million rows/512 MiB decoded bytes, metrics to 512 columns, distinct entities to
512 and unique span IDs to 1 million. Footer bounds precede batch decoding; batches
contain at most 4,096 rows. Oversized or semantically incompatible source data is
rejected with an explicit reason. Parser/runtime failures fail the run rather than
being counted as successful diagnosis.

## Policy and evaluation

The native scorer shares existing RCA component arithmetic and summary thresholds
through a separate method allowlist. The live RCA allowlist/wire contracts remain
unchanged. The retained pre-ADR-013 log-only helper has its own legacy method
allowlist: its `native-log-pattern-presence/v1` cannot enter live RCA, and live
`business-completion-support/v1` cannot enter that legacy helper. The canonical
ADR-013 allowlist remains separate from both. The measured regression fix
preserves every canonical case result. The existing policy is hash locked; there
is no weight redistribution:

| Component | Weight | Native profile support |
| --- | ---: | --- |
| Anomaly | 0.25 | Unavailable: no approved source-compatible model |
| Temporal precedence | 0.20 | Unavailable: no objective SLO onset observations |
| Dependency/trace | 0.25 | Entity recorded-span-duration / all eligible recorded-span-duration |
| Log | 0.15 | Presence of a fixed generic exception marker among observed messages |
| Recent change | 0.15 | Unavailable: no deployment observations |

Trace fractions measure cumulative recorded work, including overlapping spans;
they do not measure exclusive latency, critical paths, anomaly or causality. A zero
duration denominator remains unavailable. Marker absence is zero lexical support,
not health; all-null or missing log messages remain unavailable. Marker patterns are
case sensitive and fixed in the profile, without fitting rarity or severity.

Maximum coverage is 0.40 versus the unchanged 0.75 floor. Missing receipt clocks
also require abstention when any support is used. A positive diagnostic ranking is
not a qualified root prediction. No native result can write an IER, activate a model
or authorize an action. Full G4 still needs independent quality evidence and its
other prerequisites.

The evaluator freezes all business observations/results before decoding the pinned
label index. Only then does it join root labels and record label-clock validity.
All three selected cases appear in denominators; quarantines are excluded from the
evaluated count and never treated as correct. Conditional qualified accuracy is
`null` when zero predictions qualify. Three OB development cases do not establish
independent sessions, historical online timing or cross-system accuracy.

## Reproduce and recover

Use an existing publisher-verified acquisition manifest for the default three cases.
Commit code/profile before measuring; do not acquire additional telemetry cases.

```bash
.venv-ml/bin/python -m scripts.evaluate_rcaeval_native \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --output artifacts/evaluations/rcaeval-native-new/report.json
```

The command runs the complete path twice, compares deterministic results and
creates an exclusive output carrying source commit, file/profile/policy hashes,
business digest, per-case observations/diagnoses and denominators. `PASS` means all
three source-to-policy paths executed; it is not an accuracy or production gate.
Any quarantine yields `INCOMPLETE` and a nonzero CLI exit. Never overwrite a prior
measurement or change thresholds/targets after inspecting its output.

The public-data CI job repeats the CLI on fresh pinned acquisition and retains the
native report in `public-data-source-audit`. Unit fixtures, actual cache execution,
installed package and exact-head CI/artifact evidence must be reported separately.
To recover, stop this offline path and revert its code if needed; existing live RCA
and raw caches remain independent. Do not rewrite source files, partitions or prior
reports to repair a failure.
