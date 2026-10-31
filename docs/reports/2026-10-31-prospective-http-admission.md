# Prospective HTTP source migration and admission — 2026-10-31

## Verdict and scope

The owner explicitly approved ADR-014. `training-data/v2` now authorizes a newly
registered local HTTP primary corpus; RCAEval remains evaluation-only. The v1
contract/schema and historical GAMMA evidence are preserved unchanged. Approval
of the source is not approval of a dataset, fitted model or diagnostic result.

Source registration, collection, original-byte admission, oracle/label generation,
guarded v2 training/bundles/inference and a bounded resumable acquisition controller
are implemented and software-tested. The first actual prospective run is still
capturing at this checkpoint. **G2/G3 remain BLOCKED; approved models: zero.** No
healthy/precursor certificate is claimed for an unfinished run. **G4 is unchanged**:
three RCAEval development cases, zero qualified predictions, maximum coverage 0.40
against the unchanged 0.75 floor. No sealed final data was accessed.

## Source and implementation identity

Main was fetched before implementation and fetched again on 2026-10-31. The latest
main `2d869687dde9b5f6bad3a901d4a37cdfd4fadf81` merges PR #23. Its exact-head CI
[run 41165593194](https://github.com/VietSory/SentinelOps/actions/runs/41165593194)
completed successfully; this is main's evidence, not certification of this branch.
The branch incorporated that merge without discarding working changes.

| Commit | Verified implementation |
|---|---|
| `7d20eb9` | Accepted successor ADR, strict v2 source contract/schema, v1 reproduction retained |
| `4ceace5` | Pre-traffic role/session/recipe registration, isolated fresh owned context, real multi-entry collector |
| `f88929c` | Original-source verifier, independent workload/SLO oracle, causal labels, post-run context observation and freeze lock |
| `b95d010` | Guarded local fitting, explicit v2 bundles, dataset/policy release compatibility, completion-aware risk qualification |
| `f9a1adb` | Bounded 30-slot nonfinal campaign, immutable progress events, original-worktree resume, CI/wheel coverage |

The live first capture uses immutable `/tmp/sentinelops-local-http-capture` at
`4ceace5548137a01e8c2c502ecc74407270834ba`. It owns only Compose project
`sentinelops-corpus-train-01`; PostgreSQL is loopback 55435. The actual run is
`run-c4392bf6fa0d4897a2c794867d01963a`, prospectively registered as train group 1,
healthy kind. Existing pilot/worker data was not promoted. Seven actual source
containers, a fresh database volume, image/config identities, creation clocks,
host boot hash and zero initial payments were recorded before traffic.

The campaign runs separately from an immutable worktree at `f9a1adb`, waiting for
this original slot rather than restarting it. It preserves the first group's
original capture checkout and records a new real post-run Docker observation with
the observer's own committed identity. Registration clocks are never retrofitted.

## Source-to-feature semantics

| Serving feature | New source and deterministic derivation | Availability |
|---|---|---|
| Request-rate mean | Existing source profile: per-route Prometheus 60s request-counter increase / 60; mean over five eligible buckets | Direct source semantics; actual run still needs completed replay/admission |
| p95 latency mean | Existing native HTTP histogram normalization to milliseconds; mean over five eligible buckets | Same live profile, no CPU/RPC proxy |
| p95 latency slope | Existing least-squares time slope over those historical bucket values | Derived only from past observed latency |
| Error-rate mean | Existing native HTTP 5xx counter increase / request increase; historical mean | Missing/zero denominator stays unavailable |
| Error-rate slope | Existing historical time slope over verified error-rate buckets | No outcome/intervention metadata in predictors |
| CPU/memory/network/DB saturation | Outside this exact five-feature profile | Not invented or substituted |

The unchanged authoritative formulas/units and reset/missingness behavior live in
`telemetry/core.py` and `telemetry/prometheus.py`. Admission calls that same feature
builder with original raw observations and actual receipt cutoffs, never future
observations. Numeric source replay precedes outcome labeling. Same profile names
alone are insufficient: original source hashes, receipts and identities must pass.

## Oracle and label controls

Health is independently derived from unsampled server request starts/completions
reconciled with the client ledger, observed traffic, status/latency and the frozen
750ms / 0.05 / 30 requests / two-bucket SLO. Client and server clock semantics are
checked separately, without pretending independently sampled monotonic/UTC clocks
are atomic. Missing traffic or completions produces UNKNOWN; conflicting source
identity/status/clocks rejects the source. Healthy runs must also agree with the
eligible serving observations. No unobserved error becomes zero.

Pre-SLO positives require the independently measured first violating bucket and
confirmation, strictly earlier predictor availability, onset within the approved
300-second horizon, observed pre-onset p95 deterioration and corroborated actual
gradual controls. Intervention times do not supply outcome truth. Negatives need
complete future observation including both confirmation buckets at the horizon
endpoint. Incident/post-incident/censored/incomplete rows remain unknown. Label
and scenario metadata never enter the five-feature numeric matrix.

The risk anchor is the closed UTC minute-aligned feature-window end. Availability
and actual inference completion must remain in that same minute, retaining the
same future onset set. A result completing across the next minute has unavailable
risk even while its IF window remains otherwise fresh. Replay applies the same
completion rule. This is causal qualification, not a threshold change or promised
warning lead time.

## Exact measured readiness at this checkpoint

The source-backed audit output is
`artifacts/local-http-corpus-v1/readiness-in-progress-3.json`, dataset reference
`artifact://manifests/fedcd12d2cf53c9989f1d5d2254b2cf2a15c945468160137addbc975b13d062a.json`.
Its actual counts and cutoff are retained in the linked
[machine-readable snapshot](evidence/2026-10-31-local-http-admission-progress.json).

| Support | Actual | Required |
|---|---:|---:|
| Registered groups | 1 | Acquisition alone is not admission |
| Verified completed runs / feature rows | 0 / 0 | Completed original source verification |
| Healthy train windows, each service | 0 | 300 |
| Healthy calibration windows, each service | 0 | 100 |
| Healthy train / calibration groups, each service | 0 / 0 | 3 / 2 |
| Verified precursor train / calibration groups, each service | 0 / 0 | 3 / 2 plus both target classes |
| Verified healthy development groups, each service | 0 | 5 |
| Verified gradual development groups | 0 | 5; per-service class support also required |
| Approved/activated models | 0 / 0 | Separate G2/G3/parity/release acceptance |

All three first-group slots are pending. Captured HTTP 200 progress is workload
evidence, not a healthy certificate or proof of complete p95/SLO coverage. Exact
deficits are emitted machine-readably; no count is inferred from duration or the
acquisition recipe. Shared-host fresh groups remain a conditional independence
protocol, not proof of statistical IID generalization.

## Executed verification

Environment: repository `.venv`, Python 3.11; installed pinned ML extras. ML commands
used `OPENBLAS_NUM_THREADS=1` and the installed GCC/zlib library paths recorded in
the machine-readable snapshot. Network/async tests ran outside the restricted
sandbox. No AWS resources or unrelated Docker projects were touched.

| Actual command | Actual result |
|---|---|
| `python -m pytest -q` before campaign | 719 passed, zero skips, one existing dependency deprecation warning, 45.15s |
| `python -m pytest -q` with campaign | 724 passed, zero skips, same warning, 47.06s |
| `python -m pytest -q tests/test_local_campaign.py` after checkout-selection refinement | 5 passed, 0.17s |
| `python scripts/validate_contracts.py` | 24 schemas + 17 samples + 3 configurations validated |
| `python scripts/check_installed_package.py` | Installed wheel smoke PASS outside checkout, including new module imports and v2 schema |
| `python scripts/audit_local_corpus.py --root artifacts/local-http-corpus-v1 --output artifacts/local-http-corpus-v1/readiness-in-progress-3.json` | BLOCKED, one registered group, zero verified completed runs/rows |
| `python scripts/train_models.py --store artifacts/local-http-corpus-v1 --dataset-ref artifact://manifests/fedcd12d2cf53c9989f1d5d2254b2cf2a15c945468160137addbc975b13d062a.json --output artifacts/local-http-corpus-v1/blocked-probe-3` | Expected exit 2; zero fitted models; no estimator constructed or output directory |
| `git diff --check` | PASS |

New tests cover missing/forged health and traffic, partial server evidence, future
cutoffs, exact horizon censoring, JSON oracle stability, source path/symlink/final
isolation, forged READY reports, chronological split overlap, process restart/source
clock contradictions, competing acquisition/freeze/controllers, failed/incomplete
capture retention, model admission/version forgery, and risk completion/replay at
the anchor boundary. Numerical fits are explicitly synthetic software tests.
They are not research metrics or accepted trained SentinelOps models.

## Remaining work and delivery

The complete fixed campaign has 30 nonfinal slots and 1170 minutes of configured
workload, before startup/replay/verification overhead. It does not automatically
retry failed slots, add favorable runs, freeze a blocked dataset, train or activate
models. Completed sources must pass verification; failures stop for investigation
with original evidence retained. Sealed final capture/read remains forbidden.

After admitted collection, recompute all exact counts, freeze a fully ready source,
fit through the existing pipeline and perform same-denominator MAD/IF/XGBoost
development comparison. Any event timing from retrospective evaluation must be
labelled analytical/replay, not actual historical online emission. G3 quality and
activation require separate evidence. Neither new source authorization nor fixture
software tests improve the unchanged G4 development result.

This checkpoint has actual acquisition progress and verified software, but no
completed real-source admission result, trained model, new research metrics or
qualified operational RCA claim. The new PR's exact-head CI must be checked after
publishing; the merged PR #23/main green runs do not certify this branch.
