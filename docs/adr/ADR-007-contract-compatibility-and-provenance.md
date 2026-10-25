# ADR-007 — Strict v1 payloads and supplemental contracts

**Status:** Accepted  
**Date:** 2026-10-26  
**Authorization:** Repository owner requested architecture reconciliation and implementation.  
**Related:** PRODUCT_SPEC v1.1 §6, TECHNICAL_SPEC §4/9/11.

## Context

Original spec examples use different field names/schema versions/state enums from committed JSON Schema. The original statement that consumers ignore unknown optional fields conflicts with `additionalProperties:false`. Existing IER cannot carry rich lifecycle, timeline, revisions or action audit fields.

## Decision

- The seven existing strict JSON Schemas define the supported v1 wire payloads: `schema_version="1.0"`, `service` and existing enum values. Keep current shapes and endpoint response contracts.
- Supersede the v1.0 unknown-optional-field compatibility statement for these strict payloads. Do not silently relax validation or add fields consumers reject.
- Add separately versioned observation/feature/incident envelopes and experiment/model/action manifests. Nested legacy payloads must still validate independently.
- Provenance, quality, source/feature versions and rich lifecycle belong outside legacy payloads. Explicitly map internal lifecycle to the existing IER `state` enum.
- Add semantic validation for finite values, time order, unique evidence IDs, evidence-reference integrity, ranking order and compatible revisions.
- Infrastructure observations represent RDS measurements without fabricated required HTTP fields. The initial IF scope is the three business services; infrastructure evidence remains available to RCA.

## Consequences

Existing sample/API payloads remain usable. New contracts require their own fixtures and producer/consumer tests. Breaking a legacy shape still requires a new ADR/version/migration. Semantic rejection of unsupported evidence or invalid time ordering is intentional validation hardening, with regression tests and documented errors.
