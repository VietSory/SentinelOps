# ADR-015 — Detector event coverage for prospective local HTTP evaluation

**Status:** Accepted 2026-11-08 — explicit owner approval ("Phê duyệt ADR-015").
Authorizes only the evaluation-only acquisition and evaluator below; it does not
approve G3, activate a model or dispatch an action.
**Date:** 2026-11-08
**Related:** ADR-001, ADR-002, ADR-008, ADR-014; TECHNICAL_SPEC §7.1/§9.1;
CONSTRAINTS_AND_GATES §7/§8/§10.

## Problem

ADR-014 and `local-http-prospective-plan/v1` acquire only `healthy`,
`payment-gradual` and `inventory-gradual` runs. Development-validation therefore
contains detector events only as SLO onsets **inside gradual runs**.

The frozen product scope assigns different roles to the two models:

- Isolation Forest detects incidents (ADR-001/002). Q-04/Q-05 are defined over
  S1–S4, and S1 (abrupt Payment latency/error) expects *detection, not forecast*.
- XGBoost forecasts pre-SLO gradual degradation (S3). CONSTRAINTS §7 states that
  an alert before onset is credited only in risk evaluation.

Measured development result for candidate `38bcc766…` (protocol
`local-model-development/v1`, unchanged):

| Service | Gradual SLO events | IF/MAD event TP | IF episode already active at onset | XGBoost AP / precursor recall |
| --- | --- | --- | --- | --- |
| Checkout | 10 | 0 | 10 | 0.97 / 1.0 |
| Payment | 5 | 0 | 5 | 1.00 / 1.0 |
| Inventory | 5 | 0 | 5 | 0.93 / 1.0 |

In every gradual run the detector episode opened during the verified precursor,
about ten minutes before onset. Under the frozen matching rule it is correctly
counted as unmatched, and no new alert is emitted at onset. The result is valid
for the stated protocol. It does not measure the detector's own role: the
development set contains no abrupt event where detection is the expected outcome,
so the required IF-versus-MAD comparison (G3) carries no information
(both F1 = 0). The omission comes from the acquisition plan, not from the
evaluator or the model.

Product wiring confirms the operational role. Incidents open only from objective
SLO decisions (`trigger_sources` must be `slo`). The IF percentile score enters
RCA as `anomaly_strength` (`if-normalized-score/v1`). H4 qualification requires
an available validated registry. On the measured S2 Kubernetes case, offline
projection of the candidate IF score raises the Payment RCA candidate from score
0.549 / coverage 0.55 (abstained) to 0.799 / 0.80 / margin 0.40. This is a
counterfactual projection, not qualification.

## Decision

1. **Keep everything already frozen.** The matching rule, the
   `local-model-development/v1` result (IF 0/20), model roles, features,
   training/calibration rows, the frozen corpus, thresholds, the XGBoost target
   and the RCA policy are unchanged. The gradual-run detector result stays in
   every report beside the new result.
2. **Add the abrupt detector events the frozen spec already requires.** Register a
   new evaluation-only acquisition epoch, `local-http-detector-events-plan/v1`,
   from the same local HTTP source and source profile. It is not a new data
   source, and its rows never enter fitting, preprocessing, calibration or
   threshold selection. Each development group contains, in fresh owned contexts:
   - `healthy` — same healthy development recipe as plan v1;
   - `payment-abrupt` — scenario S1, Payment fault starting at a predeclared step;
   - `inventory-abrupt` — scenario S1, Inventory fault starting at a predeclared step.

   Fault mode alternates by predeclared group ordinal: odd groups use latency
   (1200 ms), even groups use error. Use the plan v1 warmup (300 s), entry rate
   (1 request/s per entry) and recovery (360 s). Abrupt active time is 600 s,
   long enough for two-bucket SLO confirmation plus the 300-second event
   deadline. Minimum 5 development groups, maximum 10, with the same group
   isolation, chronology, oracle and source certificates as ADR-014. Sealed-final
   capture remains forbidden by this ADR.
3. **Evaluation protocol `local-model-development/v2` adds; it does not replace.**
   - Detector events (IF and MAD) are SLO onsets measured independently by the
     oracle in abrupt runs. The matching rule, deadline, entry/exit persistence
     and false-alert budget are identical to v1.
   - Risk metrics stay on the v1 gradual protocol. Abrupt runs supply no
     precursor positives (ADR-014: instantaneous faults are never early warnings).
     Abrupt-run pre-fault windows are reported separately and do not enter the
     risk curves.
   - Report v1 (gradual) and v2 (abrupt) detector results side by side, with
     run/group denominators and paired whole-group bootstrap.
4. **H4 stays a separate Kubernetes S2 path.** The corpus runner cannot create a
   real deployment change. After an owner G3 decision and B activation of the same
   release, H4 evidence comes from a real S2 bad-deployment incident on the local
   Kubernetes testbed through the existing `NativeH4Authority` and executor guards.
   Nothing in this ADR relaxes those guards.
5. **G8 / Q-04 implication.** The final protocol (A4) must include abrupt S1
   events for detector claims, frozen before final acquisition. Development
   results are not final evidence.

## Precursor-interval accounting (decided before any v2 result)

The v1 evaluator counts SLO-compliant buckets inside gradual runs, including the
verified precursor interval, as eligible healthy service time, and counts
detector episodes opened there as false incidents. CONSTRAINTS §7 defines false
incidents over "eligible healthy service-hours" without saying whether a verified
precursor interval is healthy.

The owner delegated the choice. Decision: **v2 reports a detector episode opened
inside an independently verified precursor interval as a separate
`precursor_interval_episodes` count. Those buckets are excluded from eligible
healthy service time and from healthy false-incident counts.** Rationale: the
interval contains independently verified pre-onset deterioration (ADR-014
precursor certificate), so it cannot satisfy the healthy certificate. The episodes
are never credited as detection true positives either, so CONSTRAINTS §7 still
holds. Unverified pre-onset intervals, censored runs and gradual runs without a
verified precursor keep v1 accounting. v1 results remain unchanged and reported.

## Known remaining defects (not solved by this ADR)

Real healthy-run false incidents remain on development: Checkout 5 episodes
(≈1.2 per eligible healthy service-hour), Payment 8 (≈1.6), Inventory 0. They
exceed the Q-04 internal target (≤1) and must be addressed before G8 with better
healthy calibration evidence, not by raising thresholds or using test outcomes.

## Implementation, verification, recovery

- **Code:** new capture kinds and plan v2 config; oracle/verifier support for
  abrupt S1 events with no precursor certificate; evaluator v2 beside v1; tests
  for abrupt detection inside/after the deadline, an episode open before the
  fault, healthy-only runs, and refusal of abrupt rows in fitting and risk curves.
- **Order:** commit contracts/code and get CI before any registration. Then capture
  prospectively, audit, evaluate the unchanged candidate, and report.
- **Recovery:** v1 artifacts, the frozen corpus and its candidate are untouched.
  Reverting this ADR means discarding the separate epoch; no history is rewritten.

Acceptance of this ADR authorizes only the acquisition and evaluation above.
G3 approval, activation, H4 qualification and dispatch remain separate decisions.
