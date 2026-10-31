# Controlled Payment rollback — component status

[ADR-009](../adr/ADR-009-payment-rollback-runbook.md) remains the authority for
`payment-rollback-known-good/v1`. No action endpoint, Kubernetes executor,
approved known-good ledger, durable action journal or closed-loop acceptance is
implemented yet. UI remains read-only. No AWS resources are required for this
local work.

## Post-action telemetry verifier

`sentinelops.remediation.verification.verify_post_action_health` is an internal,
read-only component for the future controller. It accepts a `HealthLedgerReader`,
an explicit action-effective UTC clock, decision cutoff and expected environment.
The controller must bind that reader's stream/policy to its durable action intent;
an arbitrary timestamp, fixture or manually supplied health observation is not
proof that an action occurred.

The verifier uses the existing raw-source verification path, including accepted
PostgreSQL history, immutable raw/normalization/signal artifacts and policy/source
identity. It reproduces the stream from its origin instead of accepting an
unchecked streak/checkpoint. A bounded history budget (128 batches by default)
returns `inconclusive` on exhaustion. Large histories need a separately verified
checkpoint design before controller integration; callers may explicitly increase
the bounded budget to at most 10,000. No table migration or worker state changes
are introduced.

- Exclude any 60-second bucket whose start precedes action effectiveness, even
  when its health decision occurs afterwards. Do not inherit a pre-action clear
  streak or count a `recovery_confirmed` event as three new buckets.
- Require three consecutive eligible Payment buckets with clear SLO observations.
  Existing health policy checks requests, coverage, latency/error limits and lag;
  no alternate recovery threshold is introduced here.
- UNKNOWN, insufficient traffic or a breach resets the verifier's own streak.
  Duplicate, skipped or reordered source buckets fail integrity validation.
- Bound available evidence by the explicit decision cutoff and a 600-second
  verification deadline. Late database receipt cannot repair an expired run.
  Database/artifact errors propagate; callers must handle them as unavailable,
  retain durable intent and escalate when their deadline expires.

Outputs are internal dictionaries, not a new public API or an IER lifecycle
transition. `telemetry_verified` only means these SLO observations qualify.
`action_dispatch_authorized`, `deployment_identity_verified` and
`recovery_accepted` remain **false** for every result. There is no permission to
dispatch, no Kubernetes readiness observation and no causal recovery claim.

Run the source-backed codec fixtures:

```sh
python -m pytest -q tests/test_remediation_verification.py tests/test_health_source.py
```

The verifier was also run twice against B's actual accepted local health ledger,
using the controlled Payment fault-reset clock `2026-10-31T16:15:33.015128Z` and
capture cutoff `16:20:33.448862Z`. It reproduced the same three eligible buckets
ending `16:17`, `16:18`, `16:19` UTC. This is retrospective validation of the
telemetry component on actual observations. The reference operation was a fault
reset, **not** an image rollback or an autonomous recovery. It does not satisfy
ADR-009 closed-loop acceptance. Machine-local output is retained under the owned
ignored runtime root, not committed as a PASS report.

## Next controller integration

Implement the independent local executor and guard tests under ownership B.
Keep actual dispatch blocked until H4 qualification and an approved immutable
known-good image ledger are available. Required pieces still include explicit
local cluster/namespace/resource UID binding, digest-only image selection,
resource-version preconditions, allow-list/enable checks, server dry-run,
blast-radius limits, durable audit-before-patch, idempotency/lock/budget/cooldown,
crash reconciliation and kill-switch recheck. Kubernetes-ready plus a successful
patch response is insufficient. Recovery also needs verified target image/pod
identity and this telemetry check, persisted through the action journal.

Do not change the existing natural-recovery incident consumer to imply an action
ran. Its analytical RCA contract has `actions_allowed=false`; H4 producer and
consumer need an explicitly agreed supplemental boundary before action authority
can be integrated. H4 absence blocks dispatch, not independent executor work.
