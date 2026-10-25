# ADR-008 — Event-time data quality and honest evaluation

**Status:** Accepted  
**Date:** 2026-10-26  
**Authorization:** Repository owner requested architecture reconciliation and full technical verification.  
**Related:** ADR-002/003, TECHNICAL_SPEC §5–10, CONSTRAINTS_AND_GATES.

## Decision

- Use UTC aligned half-open buckets and past-only windows. Initial local design uses 60-second buckets, 300-second windows, 60-second steps and measured/versioned lateness policy.
- Preserve observed, available and ingestion times. Arrival-aware replay is required for online-equivalent lead time/MTTD; analytical replay is labeled separately.
- Derive rates from reset-aware counter deltas and percentiles from merged distributions. No averaging pod p95 values as service p95.
- No traffic, incomplete/stale telemetry and warmup are explicit quality states. They cannot become zero errors, healthy inference or successful recovery.
- Keep experiment truth/labels outside serving. Split entire independent runs/sessions before fitting preprocessing, models, calibration or thresholds; lock final holdout.
- Report model-only alerts, statistical controls, SLO-rule alerts and combined operational incidents separately. A rule-triggered incident cannot be credited as model success.
- Version source semantics and validate local/AWS parity. Numeric thresholds, collection budgets and quality targets remain proposed until feasibility/calibration freeze; this ADR does not declare them measured or competition-mandated.

## Consequences

Data may be withheld from inference instead of fabricated. A small number of independent runs cannot be inflated through overlapping windows. Reports include exclusions, denominators, source lag and uncertainty. AWS percentile publication requires an actual source-path check; local history does not prove AWS source parity.
