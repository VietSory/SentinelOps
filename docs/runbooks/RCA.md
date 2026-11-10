# Evidence-linked deterministic RCA

The RCA path uses strict policy/result contracts, deterministic scoring/abstention,
a raw → health → correlation adapter, analytical replay and a computed-IER producer.
Use the current source/profile and commands below for validation. Research quality,
model compatibility and action eligibility have separate gates.

[rca_policy.json](../../configs/rca_policy.json) records the initial technical-spec weights: 0.25 anomaly, 0.20 temporal, 0.25 dependency/trace, 0.15 log and 0.15 observed change. Score/coverage/margin floors are 0.75/0.75/0.15. They are initial development policy, not calibrated probabilities or benchmark results. No model fitting or RCAEval threshold selection is performed by scoring.

The core retains every dependency candidate upstream of the affected services. An unobserved candidate keeps `score=null`, zero coverage and explicit missing component reasons. Observed zero and missing are distinct. Contributions use the original weights; missing weights are not redistributed. A candidate's score is not its coverage-normalized strength. Only positive observed support enters hypothesis ranking; all candidates remain in the detailed result.

Component methods are versioned and require evidence of the correct modality and candidate. RDS may receive an explicitly labeled infrastructure rule, never fabricated HTTP/IF output. Temporal values must agree with disjoint onset intervals under the declared uncertainty; equal/overlapping SLO buckets cannot earn a precedence claim. Input fact IDs, source-reference allowlist, group/scope, observed/availability times and source modalities are checked.

[rca-result.schema.json](../../contracts/rca-result.schema.json) includes the full correlation snapshot, policy, source-linked facts, component values/reasons, coverage, ranking, margin and abstention reasons. Validation recomputes scores and decisions, so changing a score/coverage/rank or suppressing abstention and rehashing does not make the result valid. Missing/weak/tied support, unobserved dependency candidates, uncertain source availability and ambiguous correlation retain abstention.

Mode is `event-time-analytical`; historical emissions are unverified and `actions_allowed=false` regardless of score. A high suspected-cause score is not action authority or a causality guarantee. Actual source adapters must derive support cards from verified raw evidence; an arbitrary client-authored card is not authenticated source evidence. Raw fault schedules/targets and LLM output are not RCA inputs.

Software fixtures exercise semantics only. The implementation does not change the frozen datasets, five live HTTP features, pre-SLO XGBoost target or default no-model API behavior. S3 ranks Payment by observed support but abstains; that result is not localization benchmark/causality evidence. No G3/G4 or action acceptance is claimed from these component checks.

The adapter accepts one immutable source manifest with `evidence_refs` for trace, completion-log and infrastructure snapshots. It rebuilds upstream health/correlation before extracting facts. Logs are restricted to business routes and completion fields; controls and evaluation truth cannot supply support. Native trace timings must resolve to matching business logs. Known future receipts are excluded; unknown original receipt clocks remain unknown and retain abstention. Same-process client spans must fit inside their parent server span before duration/error support is used. Source support is a bounded heuristic, not causal proof or an IF score.

`python -m scripts.replay_rca --help` lists the three source stores, correlation reference, policy, optional analytical cutoff and exclusive output directory. The CLI requires a clean committed tree before actual-source scoring. Each output archives checksum-verified source objects, standalone RCA result objects and a replay report, so the bundle can be reproduced without the original source directories. Fresh Step 4 captures provide an immutable aggregate evidence manifest for this chain; CI consumes it without requiring a chosen root cause or fabricating missing support.

For an S4 development capture, `--post-capture-cutoff` uses the latest recorded
receipt from the verified operational source and correlation scope, including
PostgreSQL lock waits and projected lock logs. It rejects missing operational
receipt clocks. A database log contributes RCA support only when its source
identity matches the lock snapshots and its wait overlaps a verified PostgreSQL
span from a violating request. Use the
default correlation cutoff or `--as-of` when replay must represent an earlier
decision. All three modes preserve the same policy and abstention rules.

## Local computed incident producer

Prepare once with `python -m scripts.publish_rca --bundle <archive> --prepare-report-ref <report-ref>`. This verifies the archived raw chain and freezes a new actual emission clock in a content-addressed intent. Then publish with `--bundle <archive> --intent-ref <that-exact-intent-ref>` and `CONTROL_DATABASE_URL` selecting the dedicated localhost `sentinelops_control` database as `control_writer`. Keep credentials in the environment, not logs or command arguments. Reuse the intent reference after a crash; preparing again creates a distinct replay intent.

Every publish recomputes the report against the selected `--policy` before touching the repository. Rehashed client scores, altered severity, corrupted raw objects and backdated intents are rejected. The local CLI does not expose a public computed-write API or grant cloud deployment authority. The selected producer clock must not precede the analytical cutoff or exceed the DB clock.

The legacy IER projection contains no fabricated anomaly/risk signal. It keeps the detailed RCA reference, source SLO identities, coverage/abstention facts and UNKNOWN availability. Stable incident/idempotency identities persist OPEN, RCA_READY and then ESCALATED because analytical replay cannot authorize an action or recovery. Existing CAS, immutable audit and transactional outbox handle retries. No generated explanation is inserted into authoritative facts.

`scripts/check_rca_producer.py --help` describes real-source DB/HTTP acceptance. It requires an empty exclusive local DB; the optional explicitly named DB container is stopped/restarted only after its published port matches the selected DB. Use an explicit stable host port. It consumes test outbox events in that exclusive DB and leaves shared databases alone. CI exercises the same path from a fresh Step 4 capture.

For fresh captures, the replay's virtual cutoff may still be ahead of wall time. The acceptance driver waits at most the local health policy's 60-second lag, in bounded intervals, before preparing an intent. Unexpected farther-future cutoffs fail. Production intent preparation keeps its future-clock rejection; no clock is backdated or assertion removed. Known source receipts after the analytical cutoff remain unavailable even when the actual publication happens later.

## Revision-pinned explanation

The [H2 explanation backend](EXPLANATIONS.md) renders bounded, quoted evidence
from a validated IncidentEnvelope. Provider selection cannot change ranking,
metrics, availability or action authority. HTTP/UI integration remains a separate
consumer boundary.

## Prospective HTTP development trials

After the entire nonfinal corpus passes G2 and its exact admission is frozen,
run the no-model analytical baseline from a clean committed checkout over
**every** development group:

```bash
python -m scripts.evaluate_local_rca \
  --store /absolute/corpus/root \
  --dataset-ref artifact://manifests/<frozen-admission-sha256>.json \
  --output /absolute/new-evaluation-directory
```

The [versioned protocol](../../configs/local_rca_evaluation.json) pins the
existing health/RCA policies, topology, forensic cutoff and group-selection rule.
Original raw-source admission and the exact freeze are recomputed before replay
or output creation. Blocked source exits with status 2 and creates no output.
The destination must be new and outside the source corpus. Source pointers and
paths are checked again; sealed-final, train and calibration trials are not
selected for diagnostic evaluation. Every selected whole group must retain its
three original healthy/Payment-gradual/Inventory-gradual trials.

This reuses the existing raw-verified health, correlation and RCA replay chain.
Only operational source references and policies reach diagnosis. Expected
intervention roots and independent SLO confirmations are used afterward for
evaluation. The decision clock is the maximum correlation cutoff and operational
receipt time, so this is retrospective forensic analysis. No historical alert,
online latency, MTTD or causal ordering is established.

When a trial has several groups, the earliest onset wins, with group ID breaking
a tie; the evaluator never selects a later group because its answer matches the
expected root. Missing incident groups, abstentions, wrong roots and fault trials
without an independent confirmed SLO remain visible. All fault interventions are
the denominator of descriptive trial-level top-1/top-3 counts; conditional
qualified top-1 has a separate denominator and is null when none qualify. Healthy
trials with an incident group are reported separately. These are not the
incident-level Q-04/Q-05 metrics. Trials within a whole source context are
correlated, so the report makes no IID confidence interval or quality claim.

The v2 evaluation report also compares top-1, top-3 and mean reciprocal rank
(MRR) across four views of the same selected group. Full diagnostic uses every
positive RCA score. Full qualified keeps a ranking only if RCA did not abstain.
Anomaly-only uses positive measured anomaly strengths. Topology/time-only uses
the correlation group's structural candidates and orders them by verified onset
precedence when every candidate has that evidence. Otherwise they share a tie.
Each tied candidate receives the last position in the tie. All four views divide
by every fault trial, including missing rankings, and report how many trials
each view could rank. These comparisons do not change RCA weights or decisions.

The output contains `evaluation.json` and per-run health/correlation/RCA archives.
Original corpus objects and Git history are still needed to recompute admission.
A failed replay leaves its partial output for investigation and does not produce
a successful evaluation. The software baseline excludes model outputs; it does
not replace the model-integrated worker evaluation. G4/final/activation/action
authority remain false regardless of descriptive results. Actual admitted-corpus
execution requires the exact source freeze. The
[local development baseline](../reports/RCA_DEVELOPMENT.md#corpus-http-local-đã-khóa)
records a real replay of 15 admitted trials after D-23.

Focused checks: `python -m pytest -q tests/test_local_rca_evaluation.py`.
