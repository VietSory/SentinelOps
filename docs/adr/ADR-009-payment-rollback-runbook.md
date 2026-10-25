# ADR-009 — First closed-loop action: known-good Payment image rollback

**Status:** Accepted  
**Date:** 2026-10-26  
**Authorization:** Repository owner requested adoption and implementation of the delivery architecture.  
**Related:** ADR-005, TECHNICAL_SPEC §13.

## Decision

The first automatic runbook is `payment-rollback-known-good/v1` on Kubernetes, exercised locally before AWS. It restores an immutable image digest recorded in an approved known-good deployment ledger. An actual bad deployment/change event must precede the incident.

Policy checks include target namespace/resource identity/current version, evidence freshness and confidence, incident revision, enable flags, known-good availability, per-target lock, cooldown, attempt budget, blast radius and API dry-run. The controller has no commerce-data or IAM mutation privileges.

Action intent/audit must persist before a side effect. Retries after a crash reconcile observed Kubernetes state. A patch response or ready Deployment alone is not recovery: verify consecutive eligible post-action telemetry buckets with adequate traffic.

Failed/inconclusive rollback verification escalates. The controller never automatically reintroduces the known-bad image as compensation. Fault flag reset, native pod restart and HPA behavior do not satisfy this action's acceptance.

## Consequences

Scaling/restart runbooks are deferred until separately specified; the existing remediation schema enum does not authorize them. Numerical bounds remain versioned policy defaults subject to validation. Kill switch is checked again immediately before dispatch; already sent mutations require reconciliation/audit.
