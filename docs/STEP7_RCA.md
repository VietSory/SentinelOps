# Evidence-linked deterministic RCA

Implementation is being built under P7. The current core supplies strict policy/result contracts and deterministic scoring/abstention. Source extraction, verified actual-source replay, IER projection and protected persistence must be completed and measured before this slice is accepted; full G4 also needs separate localization/multi-fault evaluation and worker integration.

[rca_policy.json](../configs/rca_policy.json) records the initial technical-spec weights: 0.25 anomaly, 0.20 temporal, 0.25 dependency/trace, 0.15 log and 0.15 observed change. Score/coverage/margin floors are 0.75/0.75/0.15. They are initial development policy, not calibrated probabilities or benchmark results. No model fitting or RCAEval threshold selection is performed by scoring.

The core retains every dependency candidate upstream of the affected services. An unobserved candidate keeps `score=null`, zero coverage and explicit missing component reasons. Observed zero and missing are distinct. Contributions use the original weights; missing weights are not redistributed. A candidate's score is not its coverage-normalized strength. Only positive observed support enters hypothesis ranking; all candidates remain in the detailed result.

Component methods are versioned and require evidence of the correct modality and candidate. RDS may receive an explicitly labeled infrastructure rule, never fabricated HTTP/IF output. Temporal values must agree with disjoint onset intervals under the declared uncertainty; equal/overlapping SLO buckets cannot earn a precedence claim. Input fact IDs, source-reference allowlist, group/scope, observed/availability times and source modalities are checked.

[rca-result.schema.json](../contracts/rca-result.schema.json) includes the full correlation snapshot, policy, source-linked facts, component values/reasons, coverage, ranking, margin and abstention reasons. Validation recomputes scores and decisions, so changing a score/coverage/rank or suppressing abstention and rehashing does not make the result valid. Missing/weak/tied support, unobserved dependency candidates, uncertain source availability and ambiguous correlation retain abstention.

Mode is `event-time-analytical`; historical emissions are unverified and `actions_allowed=false` regardless of score. A high suspected-cause score is not action authority or a causality guarantee. Actual source adapters must derive support cards from verified raw evidence; an arbitrary client-authored card is not authenticated source evidence. Raw fault schedules/targets and LLM output are not RCA inputs.

Software fixtures exercise semantics only. The core does not change the frozen datasets, five live HTTP features, pre-SLO XGBoost target or default no-model API behavior. Actual source metrics, immutable replay, IER integration and local PostgreSQL checks will be recorded separately when implemented; no G3/G4 or action acceptance is claimed from the core alone.
