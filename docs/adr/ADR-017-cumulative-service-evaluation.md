# ADR-017 — Cumulative per-service evaluation when services are added

**Status:** Accepted 2026-11-10 — explicit owner approval (D-24). Recorded before the layer-2 epoch 2 (`alb-*`)
produced any data. The owner asked that later services build on the 3-service
results instead of restarting.
**Amended:** 2026-11-10 (Amendment 1, after Codex's independent review, before any
service was added and before epoch 2 was evaluated). It tightens decisions 3 and 5 and
relaxes nothing. See the end of this ADR.
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

## Amendment 1 — 2026-11-10 (independent review)

Codex's review found that decision 3 was too narrow and that the decision-5 guard
blocked a correct migration. Nothing has been added under the original wording, so
these changes are made in place and are recorded here. Where they conflict with the
text above, this section wins.

**1a. Evidence identity (revised after the second and third reviews).** A version in
`configs/system_versions.json` has three distinct identities:

- **Run identity: must be equal for any carry-forward.** Model version, bundle
  manifest, source profile, SLO health policy and scoring protocol. If any of these
  changes, every service is re-measured.
- **Capture identity: describes the capture, not a carry-forward criterion.** Epoch,
  source commit, evaluation plan and topology hash. These normally change when a
  service is added, because the plan gains kinds and the topology gains a node. A
  change here does not by itself disqualify an old service.
- **Service identity: the per-service equivalence a carried service must keep.**
  Route, transitive downstream call path, shared infrastructure, code tree
  (`services/<name>` at the capture's source commit), and the workload kinds
  measured for it. Version-1 evidence for a service carries forward only if its
  service identity is unchanged and none of its shared build inputs changed.
  Shared build inputs are recorded per version: `services/common`, `src`,
  `contracts`, `Dockerfile.service` and `pyproject.toml`. A change to
  `services/common`, the Dockerfile or `pyproject.toml` re-measures every service. A
  change to `src` or `contracts` re-measures every service unless a diff declared
  before capture shows it touches no module or contract the service image uses. When
  unsure, re-measure.

A declared version is immutable. The guard pins each version entry's hash. Editing
system-v1 in place, for example adding an edge and updating its topology hash,
fails. A change needs a new version and a new pin. The guard also checks that the
current version's per-service downstream equals the topology's transitive call
path, and that its routes match `BUSINESS_ROUTES`.

**3a. Which existing services are re-measured.** Before capture, the topology diff
and an infrastructure inventory are declared. An existing service is re-measured if
any of the following holds:

- its call path changes **directly or transitively**: any component reachable from
  it gains or loses a downstream edge;
- it is upstream of the new service;
- it shares infrastructure with the new service: database or schema, connection pool,
  queue or broker, cache, gateway or ingress, or the same node or resource pool.

When unsure, the service is re-measured. Only services that meet none of these carry
their version-1 evidence forward.

**3b. Regression slots cover Q-07.** Healthy plus abrupt slots cannot re-establish Q-07,
which is about early warning for gradual degradation. Each group for a re-measured
service therefore contains healthy, one abrupt and one gradual kind. Without gradual
evidence on the new version, that service's Q-07 result is reported as
**not re-measured** and is not carried forward.

**5a. Service names per contract version.** `configs/system_versions.json` declares
each system version's services and which version is current. The current version must
equal `BUSINESS_ROUTES` and the RCA topology. A contract belongs to the current version
unless `frozen_contracts` pins it to an older declared version. Adding a service
therefore means a new contract version (ADR-007) plus a pin for the old one. Names are
recognised by their shape (`*-service`), not by membership in a known list, so an
enumeration whose names are all misspelt is still caught. Every declared version must
carry the run, capture and service identities of 1a, and is pinned. The guard
`tests/test_service_registry_single_source.py` checks every contract against its own
version, so a valid v1 contract keeps passing while any forgotten current enumeration
fails.
