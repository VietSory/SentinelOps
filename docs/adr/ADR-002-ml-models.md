# ADR-002 — ML model baseline

**Status:** Accepted

- Statistical baseline is retained for comparison and guardrail experiments.
- Isolation Forest is the primary unsupervised anomaly detector.
- XGBoost is the supervised degradation-risk model.
- Model input at time `t` may use only information from the past/current window `[t-L, t]`.
- Future SLO breach data may be used only as a training label, never as an inference feature.
- A risk score must not be described as a calibrated probability unless calibration is measured.

Additional model families require a new ADR and evidence that they improve an agreed evaluation metric enough to justify extra complexity.
