# ADR-012 — Continuous incident input, grouping and publication ownership

**Status:** Accepted for the local continuous consumer; native development evidence and per-release CI remain separate from full G4.
**Scope:** Local continuous health → correlation/RCA → persisted IER within ADR-006/007/008 boundaries. This does not accept G3/G4, authorize actions or change training sources.

## Context

Continuous health has ordered accepted three-service PostgreSQL batches, immutable source objects and actual decision clocks. Existing correlation/RCA replay adapters require a virtual replay clock and cannot authenticate this ledger by changing a mode label. Recomputing correlation over every growing prefix also changes source-bound group IDs and can prematurely assign Checkout to Payment before a second independent root opens.

## Decision

- Verify each selected accepted batch against a consistent read of its stream checkpoint, three signal rows and preceding four source batches. Recompute normalization and health from immutable raw objects using the original recorded decision clock. Do not reinterpret UNKNOWN, quarantined or missed-deadline data as healthy.
- Consume every accepted bucket in order. A cluster admits onsets through `first_onset + 120s` inclusive. With common source entry persistence `k`, those onsets can open through `first_onset + 120s + k*60s`. Only an accepted input watermark through that bucket completes membership; wall-clock quiet does not. Report the grouping wait separately from computation time.
- Share the existing conservative temporal/topology partition. Preserve separate sibling roots and ambiguous shared consumers. Freeze the source scope at the first complete closing batch so later prefixes cannot replace already finalized group identities.
- Bind one consumer identity to one health stream. Bind its configuration to source/profile/policy, topology, selected RCA policy and artifact root; incompatible restart configuration requires an explicit migration. Do not silently replay old episodes into a new set of incidents.
- Persist cursor/state, finalized group intents and observed episode recoveries together. Publication uses an immutable command journal followed by immutable receipts referencing the actual incident revision. A crash after repository commit but before receipt reuses the same idempotency key, contents and original intent clock.
- Capture bounded operational business logs, native traces and available read-only infrastructure evidence independently of experiment controls. Preserve actual receipt times. Share RCA component extraction/scoring; missing/expired modalities retain missingness and abstention. Do not fill anomaly/risk components with SLO or intervention labels.
- Keep original SLO onset/evaluation times as evidence. IER `detected_at` is the frozen actual local intent-preparation clock; persisted revision clocks remain separately queryable. Neither is a reconstructed historical alert-notification time or an MTTD claim.
- Create OPEN before RCA_READY and escalation when required by abstention. Natural recovery requires the matching source episode's verified recovery decisions for all group members, including recovery received before delayed group publication. UNKNOWN cannot clear an incident. Existing lifecycle transitions, CAS, audit and outbox remain authoritative; no action path is enabled.

## Persistence and recovery

Additive schema version 3 separates immutable consumer identity/steps/jobs/evidence/commands/receipts from the mutable cursor pointer. Runtime can append and advance that pointer; it cannot rewrite accepted history. Existing incident/health readers must explicitly accept compatible schema 3 while retaining their privilege checks.

No SQL transaction spans source HTTP or Docker reads. Source file verification also runs after its read transaction is released. A per-consumer session lock serializes work on a dedicated **nonpooled** connection; every journal/repository mutation in that iteration uses that actual lock-owning connection. A pool proxy must not be held across external I/O: server disconnect can return its holder to the pool before the old iteration resumes. Source reads use a separate bounded pool. A disconnected old writer cannot switch to another connection. Source acknowledgment and durable intents are atomic. External writes remain at-least-once with stable repository idempotency identities.

Rollback normally disables the new consumer and retains additive data plus a compatible reader. Restoring a pre-upgrade database requires a rehearsed restore and accounting for every later accepted write; code revert alone is not data recovery. No volume deletion is an upgrade procedure.

## Verification and remaining gates

Temporal boundary, late independent-root, corruption and retry tests, real PostgreSQL upgrade/permissions/atomicity/restart, protected API history/outbox, and actual S1 plus both S5 development health→IER captures are recorded in the measured report. Captured-ledger source-reader integration alone does not prove a running continuous incident worker. Portable reconstruction and mutation probes verify transported ledgers without replacing the live captures. Every published candidate requires successful exact-head CI/artifact inspection. Final S5/RCAEval holdouts, full G4/model integration and production acceptance remain separate and unaccepted.
