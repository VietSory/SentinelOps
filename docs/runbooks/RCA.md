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

## Local computed incident producer

Prepare once with `python -m scripts.publish_rca --bundle <archive> --prepare-report-ref <report-ref>`. This verifies the archived raw chain and freezes a new actual emission clock in a content-addressed intent. Then publish with `--bundle <archive> --intent-ref <that-exact-intent-ref>` and `CONTROL_DATABASE_URL` selecting the dedicated localhost `sentinelops_control` database as `control_writer`. Keep credentials in the environment, not logs or command arguments. Reuse the intent reference after a crash; preparing again creates a distinct replay intent.

Every publish recomputes the report against the selected `--policy` before touching the repository. Rehashed client scores, altered severity, corrupted raw objects and backdated intents are rejected. The local CLI does not expose a public computed-write API or grant cloud deployment authority. The selected producer clock must not precede the analytical cutoff or exceed the DB clock.

The legacy IER projection contains no fabricated anomaly/risk signal. It keeps the detailed RCA reference, source SLO identities, coverage/abstention facts and UNKNOWN availability. Stable incident/idempotency identities persist OPEN, RCA_READY and then ESCALATED because analytical replay cannot authorize an action or recovery. Existing CAS, immutable audit and transactional outbox handle retries. No generated explanation is inserted into authoritative facts.

`scripts/check_rca_producer.py --help` describes real-source DB/HTTP acceptance. It requires an empty exclusive local DB; the optional explicitly named DB container is stopped/restarted only after its published port matches the selected DB. Use an explicit stable host port. It consumes test outbox events in that exclusive DB and leaves shared databases alone. CI exercises the same path from a fresh Step 4 capture.

For fresh captures, the replay's virtual cutoff may still be ahead of wall time. The acceptance driver waits at most the local health policy's 60-second lag, in bounded intervals, before preparing an intent. Unexpected farther-future cutoffs fail. Production intent preparation keeps its future-clock rejection; no clock is backdated or assertion removed. Known source receipts after the analytical cutoff remain unavailable even when the actual publication happens later.
