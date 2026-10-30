# RCA integration: preserve live and native method admission

Live RCA, the canonical ADR-013 native evaluator and the retained legacy log-only
experiment now use separate method allowlists with unchanged scoring arithmetic.
The integration correction was committed as
`903ef3d603f3c2303e85667d16036d3b911e2482` before real-data verification.
[Machine evidence](evidence/2026-10-30-rca-method-isolation.json) binds code, source,
profile, policy, tests and actual reports to that commit.

## Reproduced regression and correction

The incoming integration at `1b87950f86eb081a2261a6be374345968f13249b` retained the
trusted RCAEval file inventory and added model/consumer integration plus canonical
ablations. Review found that its compatibility log method
`native-log-pattern-presence/v1` had also entered the live RCA allowlist. Conversely,
the compatibility native kernel reused live admission, accepting
`business-completion-support/v1` for native log evidence.

Two regression tests failed before the production fix: live `evaluate_rca` accepted
the foreign native method, and the legacy native kernel accepted the live business
method. Both failures were missing validation errors; source values, weights and
thresholds were unchanged. A passing CI on the incoming commit did not cover those
two boundary violations.

The fix restores live log admission to its original business-completion method and
passes an explicit log-only legacy allowlist to the shared score function. The
canonical ADR-013 native allowlist remains separate. The legacy helper and its valid
fixture callers retain their original 0.15 log support and unknown-clock abstention.
No service mapping, model support, weight, threshold, source role or dataset changed.

## Verified behavior

- **Focused:** 100 passed across live RCA, canonical/legacy native evaluation,
  model-signal integration and incident evidence. Both directional regressions now
  reject their mixed-scope methods.
- **ML/dev regression:** 601 passed with no skips; one existing Starlette/anyio
  deprecation warning. Contracts: 23 schemas, 17 samples, two existing configurations.
- **Package:** installed-wheel import/runtime checks passed outside the checkout.
- **Actual source:** the three admitted RCAEval cases execute through the canonical
  CLI twice identically. All observations, scores, ranking, coverage and abstention
  match the prior trusted-inventory implementation. All 12 publisher files were
  verified again after execution.
- **Canonical ablations:** the separate ablation CLI executes the same bounded
  adapter and produces exactly the same baseline case outputs. Removing logs or
  traces introduces no new support or redistributed weight.

| Variant | Cases | Maximum coverage per case | Abstentions | Qualified predictions |
| --- | ---: | --- | ---: | ---: |
| Native traces and logs | 3 | 0.00 / 0.40 / 0.40 | 3 | 0 |
| Without native logs | 3 | 0.00 / 0.25 / 0.25 | 3 | 0 |
| Without native traces | 3 | 0.00 / 0.15 / 0.15 | 3 | 0 |

The baseline business-output SHA256 remains
`4d1ab2e228e559abc9e9a0d6bd66f9748573db05c20ebf68f4591bde09257101`.
Descriptive rankings include abstained hypotheses: trace-bearing variants retain
top-1 zero, top-3 1/3 and MRR 1/6; the log-only ablation has zero for all three.
These are not qualified localization results or independent-trial estimates.
Conditional qualified accuracy remains unavailable.

This was self-review by one agent. Guarded model software remains separate from
real data readiness: G2/G3 are blocked, no public
model was fitted or activated, and this correction does not accept full G4 or NFR-10.

Earlier ancestor and incoming-commit CI runs retain their original source
attribution. This correction requires its own published-head CI and transported
native artifacts before release closure; those earlier runs do not certify the
changed method admission.
