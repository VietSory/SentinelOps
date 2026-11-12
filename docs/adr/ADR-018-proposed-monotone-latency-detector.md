# ADR-018 — Proposed: serve the bundled MAD as the latency detector, confirm on a fresh epoch

**Status:** Proposed 2026-11-12. Needs explicit owner approval. It changes which
methods Q-04/Q-06/Q-07 judge and removes Q-05 and the XGBoost-versus-slope comparison
from this profile's gates. That is a change to gate scope, so the operator cannot
adopt it alone.
**Related:** ADR-002 (IF/XGBoost roles; this would amend it for serving), ADR-015, ADR-016, ADR-017, [layer-2 report](../reports/LAYER2_LOCAL_ALIBABA_BADF9DA0.md).

## Context

Layer 2 for bundle `badf9da0` evaluated the frozen model once on 5 local groups.
IF detected 1 of 12 latency events, and XGBoost raised no precursor warning (0 of 20).
The diagnosis points to a mechanism, not only to sampling noise, although 5 groups
and a conditional bootstrap cannot prove it general. IF raw scores level off at 0.60–0.65 for
any out-of-range latency, which is where the Alibaba-calibrated threshold 0.636
sits. XGBoost scores on labelled local windows stay below its 0.8 alert level. The
bundled controls do not have this ceiling: MAD (a maximum absolute z-score,
calibrated on Alibaba at the same 1% budget) detected 12 of 12 latency events, and
the relative-slope control warned on 20 of 20 gradual runs.
On the unseen Alibaba services (layer 1), the two showed no significant difference:
event F1 0.286 (MAD) against 0.295 (IF), IF−MAD 95% CI [−0.022, +0.039]. This does not
prove non-inferiority.

Those control numbers were seen before this proposal, so they cannot confirm it.
Choosing MAD because it scored well on epoch 2 is selection on that epoch. Only a
fresh epoch can provide evidence.

## Proposal

1. **Successor serving profile, same bundle.** No refit. The detector and risk
   artifacts stay `badf9da0` / manifest `2d1a5376…`. Latency alerts come from the
   bundled MAD at its Alibaba calibration threshold. Early warning comes from the
   relative-slope control at the protocol threshold 0.05. IF and XGBoost remain in
   the bundle as reported diagnostics and do not drive alerts.
2. **Gate scope change (owner decision).** The levels stay: Q-04 recall ≥ 0.85,
   precision ≥ 0.80, ≤ 1 false incident per service-hour; Q-06 p90 ≤ 300 s; Q-07
   precursor recall ≥ 0.70 at lead ≥ 60 s. What changes is the method each gate
   judges. Q-04 and Q-06 would judge the serving detector (MAD), not IF. Q-07 would
   judge the serving warning (slope) against prevalence; the "XGBoost above the slope
   control" comparison no longer gates, because slope is now the served method.
   Q-05 (IF against MAD) is still reported as written but does not gate this profile.
   No threshold, budget, persistence or deadline changes. Approving this ADR means
   approving these scope changes to `CONSTRAINTS_AND_GATES.md`.
3. **Fresh confirmation epoch.** This is a new plan with group prefix `alc` and a
   new `seed_base`. The workload recipe, source profile, SLO policy and evaluator code
   are the same as in epoch 2; the evaluator already scores MAD and slope, so no
   scoring code changes. The serving choice in decision 1 and the gate scope in
   decision 2 are frozen by this ADR before capture, as ADR-017 requires. The epoch
   is registered before capture, 5 whole groups, single evaluation, under the ADR-016
   rules. Epoch-2 groups become development data and are never pooled with it. Under
   ADR-017 its seeds and code make it a new system version, `system-v2`, with
   the same services and no carry-forward from `system-v1`.
   The recipe covers only S1 (abrupt latency/error) and S3 (gradual). The result is
   development evidence for those scenarios. It is not the full S1–S4 scope of Q-04,
   and not G8.
4. **Not in scope.** A refit of IF or XGBoost needs its own design frozen on
   Alibaba data. AWS, activation and G8 are separate.

## Consequences

- If the owner approves, the operator first records the approval: this ADR's
  status becomes Accepted, and a decision row is added to `CONSTRAINTS_AND_GATES.md`
  together with the gate-scope change. Then the operator pushes the prepared epoch
  commits, declares `system-v2`, registers epoch 3 and starts capture from a clean
  checkout at the plan commit. That is the commit `system-v2` is computed at, not the
  later registry commit. Nothing in code checks the approval, so this order is
  the control.
- One run is about 25 h of host time; the only operator action is the campaign CLI.
- Risk: on checkout in epoch 2, MAD raised 6 false incidents (3.4 per healthy
  service-hour, precision 0.50). Q-04 asks for precision ≥ 0.80 and ≤ 1 false
  incident per service-hour, so it may fail on the fresh epoch. That outcome would be
  reported as is.
- If the owner rejects it, the current candidate keeps its D-22 scope, and the
  next step is a refit design on Alibaba.
