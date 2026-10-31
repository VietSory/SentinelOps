# ADR-014 — Proposed observed local HTTP training source

**Status:** Proposed — owner review required; not an authorization to capture, admit, train or activate.  
**Date:** 2026-10-31  
**Related:** ADR-002, ADR-007, ADR-008, ADR-010, ADR-011; [measured feasibility decision](../reports/AI_RCA_NEXT_DECISION.md).

## Problem and evidence

GAMMA remains the only accepted primary training source. The measured 142-file capability scope and separate 48-file client scope yield zero certified live-service healthy train/calibration windows and zero independent verified pre-SLO precursor runs. Resources/RPC observations do not establish the live HTTP feature, workload-denominator and SLO semantics. The existing source contract cannot be amended by passing flags or relabelling interventions. RCAEval retains its separate frozen evaluation role; its three inspected development cases abstain at a maximum 0.40 coverage.

This proposal makes Option B from the feasibility report concrete. It does not approve itself or weaken any existing gate. Until accepted and implemented, ADR-011 and `configs/training_data_contract.json` remain authoritative, source-backed training stays blocked, and serving remains unavailable.

## Proposed decision for owner review

Replace GAMMA as the active primary training source with a **new, prospectively collected SentinelOps local HTTP corpus**, using the existing Checkout/Payment/Inventory testbed and telemetry pipeline. Keep the pinned RCAEval revision exclusively for RCA evaluation. GAMMA remains historical research evidence, outside fitting and active evaluation for these live HTTP models. No source rows are concatenated. Existing local pilot/S5/worker captures are integration evidence and are not retroactively promoted into this corpus.

Approval would authorize this specific replacement, not arbitrary additional datasets, a changed prediction task, modified RCA policy, or model activation. An accepted successor contract/version must be committed with an explicit migration before acquisition for training. This proposal makes no edits to that contract, feature schemas, partition files or model release ledger.

## Proposed source evidence and admission authority

Each prospectively registered acquisition session records immutable source/config/image identities, workload seed/parameters, service routes, original request attempts/outcomes, server telemetry, source receipt/ingestion clocks, reset/gap observations and host/session context. Failed/incomplete runs remain in the inventory with exclusion reasons. Raw outcome capture must account for attempts, successes, HTTP statuses, timeouts and missing completions separately; a timeout cannot silently become an HTTP 5xx or a completed server request.

An evaluator-only request-outcome oracle must independently establish activity and SLO truth. Validate event-time alignment, latency units, denominators and sampling against the serving source before admitting windows. Differences between client and server observations require explicit reconciliation or UNKNOWN, not an invented common denominator. Each admitted row binds exact original hashes, service/profile/units, past-only feature cutoff, observation availability and independent label evidence. Ground truth, intervention controls, future outcomes and run/scenario identifiers remain inaccessible to inference inputs.

A healthy certificate requires actual workload activity, complete eligible observation coverage, objective SLO compliance under the frozen policy and verified source/live semantics throughout the certified interval. Absence of interventions, low CPU, a warm-up filename or missing error lines cannot certify health. The existing 60-second bucket/300-second feature rules and 300/100 healthy train/calibration floors remain unchanged.

A gradual precursor certificate requires an independently observed later SLO onset/confirmation plus actual pre-onset deterioration. Control schedules may corroborate a gradual run, but cannot supply the target alone. Positive predictors must end strictly before the independently observed first violating bucket. Censored, incomplete and instantaneous-fault intervals remain explicit; they cannot be relabelled as early warnings.

For review, propose a **300-second forecast horizon** for XGBoost: positive means a verified gradual SLO onset in `(t, t+300s]`; negative requires complete independent outcome observation through `t+300s` with no qualifying onset. A no-breach run ending before that endpoint is censored. This horizon is a proposed label-protocol choice, not a change implemented here or a promise of five-minute warning. Owner acceptance must freeze it before any fitting or model comparison. Intervention labels and post-onset windows are excluded from this target.

The source certificate verifier must be a deterministic producer from original observations, not a caller-controlled approval flag or an arbitrary readable artifact. It must validate complete scope, provenance, independent groups, chronology, units, missingness and label boundaries before numeric estimators can be constructed. Existing G2 requirements still apply globally; this proposal does not authorize fitting one model around a failed gate.

## Proposed prospective partition and experiment plan

Freeze whole acquisition groups before windowing. Runs sharing acquisition context, persistent fault state or a common correlated session must stay together. Distinct filenames/process restarts do not prove independence. Preserve chronological train → calibration → development-validation → sealed final roles. If the measured grouping collapses runs together, collect additional groups rather than count correlated windows as independent experiments.

The proposed minimum acquisition plan is:

| Role | Healthy support | Gradual support | Use |
|---|---|---|---|
| Train | Existing floor: 300 eligible certified windows/service, at least 3 verified groups | At least 3 verified groups with valid positives and negatives | Train-only preprocessing and fitting |
| Calibration | Existing floor: 100 eligible certified windows/service, at least 2 verified groups, disjoint from train | At least 2 verified groups with both target classes | Allowed threshold/calibration procedure, frozen before final evaluation |
| Development-validation | Proposed 5 healthy and 5 gradual runs, with verified whole-group independence | Same predeclared target, complete censoring accounting | Same-denominator MAD/IF/XGBoost comparison and uncertainty limits |
| Sealed final | Proposed 5 healthy and 5 gradual runs in future disjoint groups | Exact protocol frozen before acquisition; raw access restricted | One owner-authorized final evaluation after model/config freeze |

The development/final counts are proposals for a bounded MVP, not proof of statistical sufficiency. Report realized group denominators and uncertainty; small samples do not support broad generalization. All three modeled services need valid evidence; a failed service cannot inherit another service's counts.

At 60-second stride with 300-second lookback, 100 consecutive valid windows require at least 104 minutes of observations; 50 require 54 minutes. Allocating the existing floors evenly across 3 train and 2 calibration groups implies **at least 420 minutes of eligible healthy observation**, excluding resets, warm-up exclusions, gaps, workload/source reconciliation and all gradual/development/final experiments. These overlapping windows remain correlated. This lower bound is arithmetic, not a completion estimate or an instruction to start a long-running primary generator now. Independent-session acquisition and review make full research acceptance within the remaining schedule uncertain.

## Implementation after acceptance

1. Commit the accepted successor ADR/contract, source scope, group/role protocol and oracle/label semantics. Keep final partitions sealed.
2. Implement the original-observation certificate verifier and negative/corruption/leakage tests. Add no serving route during admission work.
3. Prospectively capture permitted groups and report exact admitted/excluded counts. If any gate remains unmet, preserve BLOCKED.
4. Reuse current `ml/corpus.py`, numerical fitting/evaluation, checksummed bundles and separate validated release ledger. Fit preprocessing only on train; calibrate only in its allowed role; compare against MAD with independent-run denominators.
5. Activate only after the existing source-parity, validation and operator release approvals. Missing models continue to produce unavailable signals; RCA weights/coverage/abstention and remediation requirements are unchanged.

## Alternatives and scope

Continue bounded GAMMA semantic investigation under ADR-011, or propose a separately scoped offline resource-anomaly task. Neither is an admitted substitute for current live HTTP anomaly/pre-SLO prediction. Reusing RCAEval for fitting, silently promoting existing local fixtures, lowering the 0.75 RCA coverage floor, or changing fault-occurrence labels into predictive targets is rejected.

No new corpus is acquired for training, no model is fitted/promoted, and no final data is opened by this proposed ADR. The owner may accept, revise or reject the concrete source replacement and protocol before implementation proceeds.
