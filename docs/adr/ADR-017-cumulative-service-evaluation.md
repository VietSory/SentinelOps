# ADR-017 — Cumulative per-service evaluation when services are added

**Status:** Accepted 2026-11-10 — explicit owner approval (D-24). Recorded before the layer-2 epoch 2 (`alb-*`)
produced any data. The owner asked that later services build on the 3-service
results instead of restarting.
**Related:** ADR-008, ADR-015, ADR-016.

## Context

The owner plans to grow the testbed from 3 to 5–6 business services. The ADR-016
bundle is service-agnostic: one model for all routes, with features relative to
each service's own trailing baseline. Adding a service therefore needs no refit.
The evaluation evidence, however, is tied to the system it measured. Layer 2
costs one 4.75 h group per host day, so re-running every epoch whenever a service
is added would grow without bound. Reporting rules must be fixed before results
are seen; otherwise a later choice to pool or split could be fitted to the numbers.

## Decision

1. **Unit of evidence.** Evidence is reported per *(system version, service,
   epoch)*. A system version is the set of business routes plus the topology,
   identified by `topology_sha256` and the route list. The epoch-2 report is the
   evidence for Checkout, Payment and Inventory in system version 1. It is never
   re-scored, re-run or replaced when services are added.
2. **Adding services creates a new system version.** It requires, in order:
   updated routes, schemas and topology; source parity v2 on the new version; a
   new serving release from the **same** bundle (no refit); and an owner G3 scope
   decision for that version.
3. **Supplementary epoch, not a full rerun.** Each new service gets its own epoch
   under the layer-2 rules (≥ 30 verified healthy minutes before each fault; whole
   groups; one evaluation attempt; replacement budget). It contains that service's
   healthy and fault kinds. An existing service is re-measured only if the
   topology diff, declared before capture, changes its call path, i.e. it gains
   or loses a downstream edge. Then it gets regression slots: healthy plus one
   abrupt kind per group. Services whose path is unchanged carry their
   version-1 evidence forward. The report states this explicitly.
4. **No silent pooling.** Results from different epochs or system versions are
   reported side by side. A pooled estimate is allowed only if the pooling rule is
   written into that epoch's plan before its capture starts.
5. **Single source of service names.** `BUSINESS_ROUTES` is the source. A guard
   test fails when any JSON-schema service enumeration or the RCA topology
   disagrees with it, so adding a service shows every place that must change.

## Consequences

- Layer 2 for the current 3 services stays valid and is the base for later versions.
- Adding a service costs roughly its own 5 groups (about 5 host days at one group
  per day), plus regression slots for services whose path changed. A full rerun
  of every service is not required.
- Schemas with service enums need a new contract version per system version (ADR-007).
- Claims stay per service and per version. There is no single "the model scores X"
  figure across versions unless a pooling rule was pre-registered.
