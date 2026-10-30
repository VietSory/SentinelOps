# Native development evaluation and ablations

Current manifest execution reuses the canonical [ADR-013 adapter](RCAEVAL_NATIVE.md)
and `configs/rcaeval_native_profile.json`. The complete trusted 12-file inventory
is admitted before cache reads. Only the original three development cases are
permitted; validation and sealed holdout stay closed. No native service aliases,
missing-value fills, SLO fabrication or resource-as-IF conversions are introduced.

The fixed trailing 300-second event-time interval, microsecond span observations,
exact native identities, exception-marker policy and missing receipt clocks are
those of ADR-013. The existing canonical CLI remains unchanged. The supplemental
CLI below uses the same source adapter and scoring engine, adding three comparisons
computed **before** evaluator labels are read:

- Actual admitted native trace-duration fraction and exception-marker support.
- Remove only observed native log support.
- Remove only observed native trace support.

Each ablation keeps the original frozen weights, floors and source interval; no
other modality gains invented evidence. A removed component becomes unavailable,
not zero. These are sensitivity comparisons, not proof of causality. Report top-1,
top-3 and MRR of unqualified descriptive rankings over all selected cases, retaining
abstentions/quarantines in the denominator; separately report abstention, qualified
prediction count and coverage. No conditional qualified accuracy exists with zero
qualified predictions, and independence/uncertainty is not invented.

```sh
python -m scripts.evaluate_rcaeval --manifest data/raw/public/manifests/HASH.json \
  --output artifacts/phases/rcaeval-canonical-ablations.json
```

The protocol/code tree must be committed before measurement; output paths are
exclusive. The original canonical evaluation retains its business-output identity
and local/live method admission remains unchanged. Neither path writes local IERs,
fits models, opens final data or authorizes actions.

The earlier whole-capture generic error-token experiment at `f1b997c` is retained
as historical evidence in the Phase 3 report. Its old config and pure scoring helper
remain compatibility fixtures only; current manifest execution uses the bounded
canonical source admission and fixed-window profile above. Its old numerical results
must not be substituted for measurements of the current profile.
