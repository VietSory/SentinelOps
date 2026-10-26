# GAMMA v1 — measured initial capability audit

The owner-approved GAMMA/RCAEval pair is fixed by ADR-011 and `configs/training_data_contract.json`. This report describes actual acquisition/audit of one preselected GAMMA experiment; no model fit or live activation occurred.

## Provenance and acquired scope

- Kaggle publisher `gagansomashekar/microservices-bottleneck-detection-dataset`, version 1 / version ID 7638732; declared Apache-2.0.
- Experiment `cpu_aug12_25min_200_0`, selected before model scoring. All three published processed request types, eleven graph-path metadata files, args, placement/RPC maps and 120 raw resource series were acquired.
- **139 files; 141,631,641 expanded bytes; 15,241,368 response-body bytes counted by HTTPX.** The latter excludes headers and follows HTTP content decoding (`wire_bytes` in the acquisition record); it is not an invoice/network-interface measurement. The source supplies file sizes, not independent per-file SHA256. ZIP transfers passed single-member identity/size/CRC checks. Project SHA256 hashes freeze the acquired bytes in [the file lock](../../configs/gamma_v1_audit_files.json).
- Acquisition manifest SHA256 `8f10220b4532ed9673034361493a93ae394da2976ec4141898e31e5026459640`; inventory SHA256 `6bd34277d64a8e376238113d8ab984466798cce2739fcb84fd334382ab2963ba`.
- Frozen source-policy hash `ef0eb1b1c95a67faf424501e657c5e6530d49a1d18af7cbf59f9c07740afa21f`.

The processed-file inventory contains **190 experiment names**, of which **182** have all three published request types. Names do not certify independent acquisition sessions. Other raw modalities and experiments were not downloaded by this scoped audit.

## Actual observed tables

| Request type | Trace rows | RPC latency columns | Mapping rows | Normal / anomalous trace labels | Decreasing root-time transitions |
| --- | ---: | ---: | ---: | ---: | ---: |
| compose | 15,279 | 30 | 32 | 8,997 / 6,282 | 243 |
| home | 126,846 | 7 | 9 | 71,578 / 55,268 | 5,003 |
| user | 64,650 | 9 | 11 | 35,836 / 28,814 | 1,303 |

All three tables have zero numeric missing cells in this scope, but they are not chronologically ordered. A processed row is a request trace with repeated resource data, not an independent training window. Counts above are trace labels, not certified healthy windows or future-risk labels.

The compose RPC mapping contains an unquoted comma in a service annotation at zero-based row 9, yielding three CSV fields. The annotation also mentions a missing trace. This row is retained unchanged and reported as ambiguous, not silently repaired or mapped to a service. All request-type mapping lengths differ from their processed graph column counts; source-specific node/graph alignment still needs verification.

All 120 selected raw metric files are nonempty time/value series. CPU/network fields are cumulative counters; memory is bytes. The args declare warmup and phase durations, but neither they nor bottleneck flags independently establish gradual precursors, live SLO onset or sampling completeness. Processed tables do not provide the live HTTP status/error fields.

## Gate result

- Acquisition/integrity and capability reporting: completed for this scope.
- IF training readiness: **NOT READY** until source/time/RPC mapping, causal features, grouped splits and healthy-window admission are established.
- XGBoost readiness: **NOT READY**; independently measured SLO and gradual pre-breach labels are unproven. The product target is unchanged.
- Live G2/G3/G7 and `/v1/detect` activation: **not passed / not enabled**. No p90/p95, RPC/HTTP or missing/error substitution is used.

The full machine-readable [audit evidence](evidence/2026-10-26-gamma-source-audit.json) contains exact raw-series coverage, counts and blockers. Raw data, acquisition manifests and full inventory remain ignored artifacts. Reproduction is documented in [PUBLIC_DATA_PLAN](../PUBLIC_DATA_PLAN.md).

Validation at this implementation checkpoint: **207 tests passed** in the ML environment; **17 schemas, 12 samples and the frozen policy configuration** validate. A wheel built and installed outside the checkout loads the new modules/contracts and passes the API smoke check. CI adds the exact project-hash-locked GAMMA acquisition and real-table audit; its remote result is verified separately after push.
