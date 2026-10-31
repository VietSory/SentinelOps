> Archived guide: retain its original scope for reproduction. For current work,
> use the [documentation index](../../README.md) and active runbooks.

# Continuous incident integration

The canonical runtime, durable journal, recovery ownership and nonpooled writer
are documented in [STEP8_INCIDENT_WORKER.md](../../runbooks/INCIDENT_WORKER.md) and accepted
[ADR-012](../../adr/ADR-012-continuous-incident-consumer.md). Use its migration
`004-incident-consumer.sql`, CLI and frozen S1/S5 protocols. The earlier isolated
prototype is retained only as pure offline projection/replay compatibility for
its measured artifacts; it is not a second runnable consumer or database schema.

Optional `--model-store` and `--model-release-ref` on `scripts.run_incident_worker`
load a separately approved, checksum-pinned release. The evidence profile binds
that release to the existing consumer identity. Changing it on an existing stream
requires an explicit migration; a restart cannot quietly create duplicate episodes.

The supplemental `continuous-incident-evidence/v2` capture records the exact
accepted consumer state, feature batch, feature/model signal identities and actual
inference/receipt times. The journal verifies that source step against raw history
before accepting or projecting evidence. Replay re-runs the pinned estimator.
The existing v1 evidence path remains byte-compatible when no release is configured.

Actual IF output may contribute the existing 0.25 anomaly weight. Actual risk stays
separate explanatory evidence and never invents a new RCA component. Legacy IER
signals require verified inference; unavailable models/features stay null/absent,
with explicit reasons in model evidence. No compatible validated model currently
exists, so ordinary SLO processing continues with empty model signals.

Optional `--metrics-otlp-endpoint http://127.0.0.1:4317` exports private bounded
metrics under `sentinelops-incident-worker`. Assign distinct `--metrics-instance
incidents-0`…`incidents-15` slots. Source eligibility, accepted-window age, readiness,
actual main-loop heartbeat and post-restart local-decision guards retain the health
worker semantics. `sentinelops_incident_groups_total` counts accepted group intents;
`sentinelops_rca_abstentions_total` counts accepted RCA enrichments. Use heartbeat
and local-decision guards before displaying cached gauges. Durable journal history
is authoritative after restart; missing metrics never mean healthy.
