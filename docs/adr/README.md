# Architecture Decision Records

ADRs protect frozen product decisions from accidental redesign during implementation.

Status values: `Proposed`, `Accepted`, `Superseded`.

Breaking changes require a new ADR before code is merged.

## Current decisions

| ADR | Status | Scope |
| --- | --- | --- |
| [001](ADR-001-system-boundaries.md) | Accepted | Pipeline responsibilities |
| [002](ADR-002-ml-models.md) | Accepted | IF/XGBoost roles and leakage boundary |
| [003](ADR-003-rca-strategy.md) | Accepted | Deterministic evidence-based RCA |
| [004](ADR-004-grounded-llm.md) | Accepted | Explanation-only LLM and fallback |
| [005](ADR-005-controlled-remediation.md) | Accepted | Approved, verified, audited actions |
| [006](ADR-006-control-plane-and-storage.md) | Accepted 2026-10-26 | Modular control plane, adapters and storage |
| [007](ADR-007-contract-compatibility-and-provenance.md) | Accepted 2026-10-26 | Strict legacy payloads, supplemental envelopes, semantic validation |
| [008](ADR-008-event-time-and-evaluation.md) | Accepted 2026-10-26 | Time, missingness, provenance and evaluation semantics |
| [009](ADR-009-payment-rollback-runbook.md) | Accepted 2026-10-26 | Known-good Payment image rollback |
| [010](ADR-010-public-training-data-and-transfer.md) | Accepted; source choice superseded by 011 | Public provenance, local integration and source-transfer gates |
| [011](ADR-011-frozen-gamma-rcaeval-contract.md) | Accepted 2026-10-26 | Frozen GAMMA v1 training / RCAEval RCA validation and evaluation; at most two sources |
| [012](ADR-012-continuous-incident-consumer.md) | Accepted for local implementation | Accepted continuous-health lineage, stable delayed grouping and durable incident publication; full G4 remains separate |
| [013](ADR-013-native-rcaeval-evaluation.md) | Accepted for native development software | Exact source clocks/entities, research-only support and post-business label evaluation; no operational/quality G4 acceptance |
| [014](ADR-014-proposed-local-training-evidence.md) | Proposed — owner review required | Concrete replacement training-source/oracle/label/partition proposal; existing contract remains authoritative |

ADR-001…005 remain current. ADR-007 supersedes the contradictory unknown-field rule and example wire formats in historical PRODUCT_SPEC v1.0; that source is archived. Numeric thresholds/performance targets are not automatically accepted or measured by these ADRs.
