# ADR-010 — Public training data and source compatibility

**Status:** Accepted  
**Date:** 2026-10-26  
**Authorization:** Repository owner requested published datasets from established sources as the training direction instead of relying on the small local experiment corpus.  
**Related:** ADR-002, ADR-007, ADR-008; supersedes the local-primary training-source assumption in the initial delivery plan.

**Source selection superseded:** [ADR-011](ADR-011-frozen-gamma-rcaeval-contract.md) freezes GAMMA v1 training and RCAEval RCA validation/heldout evaluation. SMD below describes historical initial research. The other provenance and transfer requirements remain current.

## Decision

- Use externally published, version-pinned datasets as the primary research/training/evaluation corpus. Keep local captures for integration tests, source parity, calibration/transfer and closed-loop verification.
- Historical source selection (superseded by ADR-011): RCAEval format audit and SMD anomaly benchmark. OpenRCA and other surveyed candidates are not admitted to the frozen v1 pair.
- Download data only, verify publisher checksums and retain source/revision/license declarations. Do not run dataset-provider scripts or copy provider preprocessing that leaks future data.
- Freeze source-specific splits, feature definitions, labels, preprocessing, metrics and thresholds before test scoring. Test labels cannot select a winning model/threshold.
- Keep IF as anomaly detector and XGBoost as degradation-risk model. Root-cause labels, injection timestamps and binary anomaly labels are not automatically precursor/SLO labels. Audit or derive appropriate labels under a separately versioned causal protocol before XGBoost fitting.
- A public benchmark model is not automatically compatible with `http-red-5m/v1`. Preserve original features/units/missingness; never rename anonymous server channels into HTTP metrics or manufacture missing request/error denominators.
- Activate an external-data model only after the live adapter has matching feature/label/source semantics, a justified transfer/calibration evaluation and the normal G3/G7 gates. Until then, bundles remain research artifacts and `/v1/detect` stays unavailable.

## Consequences

Published data broaden independent coverage but do not prove production provenance or deployment transfer. RCAEval itself uses controlled faults; SMD's publisher describes Internet-company server measurements. Local experiments were measured telemetry, while the two small local pilots remain integration evidence rather than the primary training basis.

The original seven wire contracts, model responsibility boundaries and remediation safeguards remain unchanged. The local 300/100 five-minute-window gate is not satisfied by counting unrelated public rows as live RED windows. Public research uses its documented source-specific protocol; live acceptance still needs feature/source parity and an independent evaluation.

Sources: [RCAEval publisher](https://github.com/phamquiluan/RCAEval), [Hugging Face data card](https://huggingface.co/datasets/phamquiluan/RCAEval), [SMD publisher](https://github.com/NetManAIOps/OmniAnomaly), [OpenRCA](https://github.com/microsoft/OpenRCA).
