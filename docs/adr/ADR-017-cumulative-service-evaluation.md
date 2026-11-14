# ADR-017 — Cumulative per-service evaluation when services are added

**Status:** Accepted 2026-11-10 — explicit owner approval (D-24). Recorded before the layer-2 epoch 2 (`alb-*`)
produced any data. The owner asked that later services build on the 3-service
results instead of restarting.
**Amended:** 2026-11-10 (Amendment 1, after Codex's independent review, before any
service was added and before epoch 2 was evaluated). It tightens decisions 3 and 5 and
relaxes nothing. See the end of this ADR.
**Amended:** 2026-11-14 (Amendment 2, owner-approved, before any later version was declared): the identity
records the telemetry pipeline. Tightens decision 1a; `system-v1`…`v3` unchanged.
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

**1a. Evidence identity (final form after five reviews).** A version in
`configs/system_versions.json` has a `declared` part and a `computed` part.

- **Declared** (not derivable from git): epoch, model version, bundle manifest,
  shared infrastructure per service, and `carry_forward_from` plus
  `carried_services`.
- **Computed** by `python -m scripts.system_identity --commit <capture source commit>`
  from git at the capture's commit, never from the working tree, because `main`
  keeps moving after a capture. The guard recomputes it in CI, which needs full
  history.
  - *Run identity:* source profile, feature set, SLO health policy, plan scoring,
    base protocol, and the git blob of every file in the evaluator's transitive
    in-package import closure. The closure includes imports inside functions, for
    example feature generation in `alibaba_training`.
  - *Capture identity:* source commit, plan hash and topology hash. These describe
    the capture and are not a carry-forward criterion.
  - *Shared build inputs:* `services/common`, `src`, `contracts`,
    `Dockerfile.service` and `pyproject.toml`.
  - *Service identity:* route, transitive downstream, `services/<name>` tree, the
    kinds that enter the service's measurement, and the workload recipe hash.

**Carry-forward is mechanical.** A version may list `carried_services` only if the
run identity, model and bundle, shared build inputs, and each carried service's
identity and shared infrastructure equal the predecessor's. Otherwise the guard fails.

**Consequence for the current evaluator.** `score_by_route` scores every service on
every plan kind. Adding a new service's fault kinds therefore changes every old
service's measured kinds, and no old service can carry forward. A per-service
slot-selection path in the evaluator is a **prerequisite**. It must be built and
tested with a real supplementary plan before any carry-forward. Building it changes
the scoring code, so it becomes part of the new version's run identity. It has
not been built yet: epoch 2 runs on the frozen 3b17205 evaluator, and changing
scoring code now would not affect it.

A declared version is immutable: the guard pins each entry's hash, and pins are only
ever added. (Exception, recorded here: system-v1 was re-pinned during the 2026-11-10
reviews, before any carry-forward had used it.) The current version must match the
repository's routes and present topology.

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
carry the declared and computed identity of 1a, and is pinned. The guard
`tests/test_service_registry_single_source.py` checks every contract against its own
version, so a valid v1 contract keeps passing while any forgotten current enumeration
fails.

## Amendment 2 — 2026-11-14 (telemetry pipeline)

Lane B's `5f29a16` changed metric delivery (SDK export 2 s → 1 s, no Collector batch for metrics) to pass
source parity. The SDK change moved `shared_build_inputs`, but the Collector and Prometheus configuration were
not part of the identity, so a change to either alone would not have been visible.

- `scripts/system_identity.py` records `telemetry_pipeline`: the git objects of `docker-compose.yml` (the
  capture's Compose file: Collector/Prometheus images, commands, mounts), `observability/otel-collector-config.yaml`,
  `observability/prometheus.yml` and the `observability/rules` tree at the capture commit. A new version must
  record exactly these inputs.
- Every version declared after `system-v3` must record it, and a service can carry forward only if it is equal.
  `system-v1`…`v3` were frozen without it and are neither edited nor recomputed with it.
- `python -m scripts.system_identity --commit HEAD --compare <version>` lists which identity blocks differ
  from a declared version, so either lane can check before changing the measurement path.
- Consequence: the current `main` differs from `system-v3` (`services/common`, `src`). The `ald` result stays
  valid for `system-v3`; the next evaluated epoch declares a new version.
