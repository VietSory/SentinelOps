# G4 RCA evidence resolution — 2026-10-30

**Continuation 2026-10-31:** owner-approved local HTTP training acquisition is
implemented separately in the [prospective source report](2026-10-31-prospective-http-admission.md).
It produces no new RCAEval modality or accepted ML signal at this checkpoint.
The measured G4 results below remain unchanged: zero qualified predictions and a
0.40 maximum coverage, with frozen policy intact and final data unopened.

**Verdict: G4 quality remains unaccepted.** Three authorized development cases still abstain; no qualified prediction. The corrections improve evidence safety and auditability, not measured diagnostic accuracy. Policy weights, coverage 0.75, score 0.75 and margin 0.15 are unchanged.

Implementation checkpoint `d72e654`, baseline merged main `dd12339259b4f36bd61948eb2401a5939762cd1f`. [Per-case machine report](evidence/2026-10-30-rca-blocker-resolution.json) contains every candidate, all five component values/methods/reasons, score, known-weight coverage, rankings, margins, evaluator-only truth, observed entities, source hashes, event timestamps, dependency links and source-reference resolution. The business result hash is identical before/after:

`4d1ab2e228e559abc9e9a0d6bd66f9748573db05c20ebf68f4591bde09257101`

## Evidence ceiling and semantic interpretation

| Component | Frozen weight | Actual admitted source support | Deficiency classification |
|---|---:|---|---|
| Anomaly strength | 0.25 | None | No validated compatible model; raw CPU is not IF inference |
| Temporal precedence | 0.20 | None | Event clocks exist, objective SLO onset and source receipt clocks do not; injection time remains evaluator-only |
| Dependency/trace | 0.25 | RE2/RE3 | Exact trace/span parent links and recorded duration fractions; not critical-path or causal proof |
| Logs | 0.15 | RE2/RE3 | Fixed exception markers in actual messages; no assumed HTTP error semantics |
| Deployment change | 0.15 | None | No verified deployment observation; fault label is not a deployment |

Maximum possible known category weight in these admitted cases is **0.25 + 0.15 = 0.40**. RE1 supplies neither admitted log nor trace support, yielding zero. Known-weight coverage is not confidence. Scores remain raw weighted sums with missing support unredistributed; even a perfect score in both observed categories cannot reach 0.75. Adding verified timing alone would raise the theoretical ceiling to 0.60, still insufficient. Receipt-clock absence independently blocks qualified native conclusions. Native timestamps cannot recover arrival times.

The adapter already preserves exact microsecond/millisecond pairs, nullable status codes, original native identities, trace IDs and parent IDs, without silently merging `frontend` and `frontendservice`. Status numbers have no independently verified HTTP meaning here. Parent-to-child links describe consumer-to-dependency observations; they do not establish a complete service topology, full sampling or fault causality. Metric resource values lack the capacity/model interpretation needed for the anomaly component. Missing modalities are not adapter bugs that can be solved by assigning arbitrary support.

## Per-case breakdown

| Case / evaluator-only truth | Candidates | Top three descriptive hypotheses | Margin | Maximum coverage |
|---|---:|---|---:|---:|
| RE1 `adservice_cpu_1` / adservice | 14 | None; no positive support | unavailable | 0 |
| RE2 `checkoutservice_cpu_1` / checkoutservice | 13 | frontendservice, checkoutservice, recommendationservice | 0.2204466463 | 0.40 |
| RE3 `adservice_f3_1` / adservice | 12 | frontend, recommendationservice, checkoutservice | 0.2025083907 | 0.40 |

RE1 abstains for `no-positive-observed-support`, `unobserved-dependency-candidates`. RE2/RE3 abstain for `insufficient-coverage`, `insufficient-score`, `source-availability-unverified`, `unobserved-dependency-candidates`. The margin passing in RE2/RE3 does not override failed score/coverage or missing-source requirements. Ground truth was joined only after all diagnoses, ablations and breakdown consistency checks completed.

RE2 and RE3 each have 12 distinct observed parent/entity links and zero unresolved parent links in the selected window. This rules out unresolved parent lookup as the reason for the 0.40 ceiling in these cases. It does not establish complete tracing or correct diagnosis.

## Implemented corrections

1. Removed the legacy research `native-log-pattern-presence/v1` from operational method admission. The legacy research helper now explicitly admits only its own log method. The ADR-013 methods remain separately bounded. A foreign lexical marker cannot masquerade as verified business-completion evidence.
2. Reject cyclic multi-span ancestry before producing dependency links. Validation is iterative and linear in node count, handles forward references, preserves missing parents as unresolved, and never joins identical span IDs across different trace IDs. Tests include two-node cycles, valid forward parents and cross-trace isolation. No actual selected case was quarantined by this correction.
3. Emit a reproducible evidence breakdown resolving each diagnostic support ref to original modality SHA256, native entity, fixed event-time interval, actual support counts and formula. Receipt timestamps remain explicitly null. Tampered scores fail consistency checks. Ground truth never enters this helper.
4. Report actual ablation exposure: log removal affects **2/3 cases**, trace removal **2/3**. RE1 remains part of the fixed three-case denominator with no removed observed support. No temporal/ML/deployment ablation is claimed as an observation.

## Before/after and ablations

| Evidence variant | Top-1 | Top-3 | MRR | Abstention | Qualified | Coverage by case |
|---|---:|---:|---:|---:|---:|---|
| Available trace/log evidence | 0/3 | 1/3 | 1/6 | 3/3 | 0 | 0, 0.40, 0.40 |
| Without observed logs | 0/3 | 1/3 | 1/6 | 3/3 | 0 | 0, 0.25, 0.25 |
| Without observed traces | 0/3 | 0/3 | 0 | 3/3 | 0 | 0, 0.15, 0.15 |

These are unchanged from the reference. Ranking metrics include abstained hypotheses. Conditional qualified accuracy is **undefined**, not zero or a success percentage. Three previously inspected cases and unproven acquisition independence do not support a generalization estimate or an independence-based confidence interval. No extra case, validation tuning or sealed holdout access occurred.

## Local operational evidence, separately evaluated

[Local incident breakdown and integrity replay](evidence/2026-10-30-local-rca-blocker-resolution.json) reconstructs the three published captures from source commit `1b87950f86eb081a2261a6be374345968f13249b` using current code and independently recorded health/incident snapshot hashes. **50 buckets / 150 accepted signals / seven incidents / 28 immutable revisions** replay successfully; actual captured receipt clocks are preserved. This is verification of earlier real captures, not a new live detection claim.

| Prior real capture | Incidents | Actual leading score / coverage | Outcome |
|---|---:|---|---|
| Payment → Checkout | 1 | Payment 0.3974136494 / 0.40 | Abstain: coverage, score |
| Independent latency faults | 3 | Payment 0.3997971481; Inventory 0.3998592379; each 0.40 | Two separate roots; shared Checkout ambiguous |
| Independent blocking errors | 3 | Payment/Inventory 0.40 / 0.40 | Abstain; missing RDS support; shared Checkout tie/ambiguity |

The operational collector already captures owned business logs, selected Jaeger traces, source receipt times and eligible infrastructure observations. Raw refs, selection and receipts are revalidated during replay. Simultaneous/overlapping SLO onset intervals do not support precedence. There are no validated ML signals or independently observed deployment records. No unused verified modality was found that could legitimately close the gap; collector behavior and incident persistence were not redesigned.

For business services, trace + log + genuinely disjoint onset evidence would at most supply 0.60 without model/change evidence. A deployment record could make coverage 0.75 **only if actually observed and eligible**; it cannot be invented for a latency injection or assumed from a scheduled intervention. Even then score, margin and every safety condition must also pass. Existing local runs do **not** produce qualified RCA under unchanged policy.

Actual current PostgreSQL/Uvicorn HTTP check on owned control port 55434 also passed for the separately retained canonical S1 incident, all four revision payloads, protected access, readiness and no duplicate writes. This is one read-only API integration run; seven transported CI incidents were checked by raw replay, not inserted into this database. Historical source/DB outage, restart and concurrent-consumer evidence remains attributed to its original captures. Fresh workload verification for the new PR belongs to its own CI jobs.

## Executed commands and results

```sh
python -m scripts.evaluate_rcaeval --manifest data/raw/public/manifests/3445957277004895c3a61460808355cf078123c890aad6d582c08373f666450f.json --output artifacts/blockers/rcaeval-baseline.json
# After committing the adapter/protocol corrections:
python -m scripts.evaluate_rcaeval --manifest data/raw/public/manifests/3445957277004895c3a61460808355cf078123c890aad6d582c08373f666450f.json --output artifacts/blockers/rcaeval-after.json
python -m scripts.evaluate_rcaeval_native --manifest data/raw/public/manifests/3445957277004895c3a61460808355cf078123c890aad6d582c08373f666450f.json --output artifacts/blockers/rcaeval-repeat.json
python -m scripts.check_native_incident_api --run artifacts/phases/canonical-native --output artifacts/blockers/native-api
```

API command used the existing local control-writer DSN through `CONTROL_DATABASE_URL`; credentials are not part of committed evidence. Raw replay invoked `sentinelops.workers.incident_replay.replay` for each capture with both externally recorded snapshot hashes; see the machine report for exact inputs and outputs. Baseline regression **599 pass**; corrected regression **612 pass**, zero skips; contracts and installed wheel pass. Missing-source/model and isolation/abstention/holdout checks remain covered. No failing test was suppressed.

**G4 no-go:** additional trace parsing cannot recover absent anomaly, objective onset, deployment and receipt facts. Preserving abstention is the correct result. The [decision report](AI_RCA_NEXT_DECISION.md) separates operational engineering completion from the unaccepted scientific gate.

## Concurrent main reconciliation

While this topic branch was being verified, main advanced to `4846f1a649d8cefa6b92b836a44c4f5704eb2f93`, including the independently implemented method-isolation fix `903ef3d` and two regressions. The branch merges that history without rewriting it, retains upstream `LEGACY_NATIVE_METHODS` as the single implementation and preserves both test sets and historical reports. After conflict resolution, the complete suite passes **614 tests, zero skips**, one existing warning, 25.06s; contracts pass again. The earlier 612-test/source measurements remain attributed to their original checkpoint. PR #23 must verify its resulting exact HEAD; the concurrent main CI is not its certification.

## Latest main source evidence (pulled before further edits)

Main advanced again to `0f29b8d046625bcd61be8874e3192ff862a50ee1`; this branch pulled and retained all seven new commits. The upstream [GAMMA client audit](2026-10-30-gamma-client-capability.md), implemented at `8b582b0`, adds actual verification of **48 files / 24 capture pairs / 2,169,411 bytes** from the four quarantined groups. All 24 companion integer counts differ from printed whole-run request counts; event clocks, integer units, per-bucket HTTP 5xx denominators and source/live mapping remain unverified. This strengthens the no-go finding without admitting those groups. The earlier 142-file matrix remains explicitly scoped to its original acquisition; client observations are separately reported, not silently relabelled or mixed into training. Full trace JSON remains uninspected.

The same upstream update supplies [real incident metrics/recovery evidence](2026-10-30-incident-worker-metrics.md) at `07c57c7`: 23 buckets/69 signals, one incident/four revisions, positive group/abstention export and exporter/source/database outage recovery. This is independently attributed operational evidence, not ML readiness or qualified RCA accuracy. Both additions are reused rather than reimplemented.

After this pull/merge: **662 regression tests passed, zero skips**, one existing warning, 28.68s. Contracts and installed wheel pass. The earlier PR HEAD `d243421` passed all eight CI jobs in [run 40998863465](https://github.com/VietSory/SentinelOps/actions/runs/40998863465); this does not certify the newer merged candidate, which requires its own run.
