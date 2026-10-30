# Continuous local incident consumer

The separate local consumer reads the selected accepted health ledger, verifies raw lineage, closes conservative onset groups, captures bounded operational evidence and journals IER revisions. [Actual S1 and both S5 development captures](reports/2026-10-29-continuous-incident-local.md) now verify seven complete incident lifecycles, restart/competition, source-derived recovery, portable integrity replay and protected HTTP history. [ADR-012](adr/ADR-012-continuous-incident-consumer.md) is accepted for this local implementation. Each published revision still requires its own CI/artifact verification. This does not activate a model/action or accept full G4.

## Identity and source boundary

Select the explicit health stream and its original artifact root. One consumer is bound to that stream, health/RCA policies, topology, output root and operational evidence profile. Restart with the same values; moving the output directory or changing the profile is a migration, not a fresh consumer. Accepted raw files/history are immutable. A changed or missing source, hash-valid unsupported signal, skipped bucket or altered pending group fails closed.

Every accepted three-service bucket is consumed, including UNKNOWN. Onsets through the first onset plus 120 seconds can belong to one temporal cluster. With source entry persistence `k`, membership is complete only at the accepted input watermark `first_onset + 120 + k*60` seconds. Wall-clock silence does not finalize membership. Shared consumers remain ambiguous when two independent sibling roots occur; later stream prefixes cannot rename finalized groups.

Operational logs come from the explicitly owned local Compose project. Only known business-route completion fields are retained; concrete paths, nonbusiness logs and experiment controls do not become RCA input. Invalid business records quarantine their service snapshot. Trace IDs are selected from observed slow/5xx completion logs with equal per-entry budgets; native timing and matching completion evidence remain required. Receipt clocks are actual collection times. Optional commerce-PostgreSQL evidence uses read-only connection-capacity measurement, separate from HTTP features/IF. Out-of-window or absent sources remain missing; RCA weights and abstention thresholds are unchanged.

## Persistence, disconnect and lifecycle

[Migration 004](../infra/control/004-incident-consumer.sql) adds schema version 3: consumer identity, accepted steps, checkpoint, group jobs, episode recoveries, evidence, publication commands and receipts. All history is append-only; the runtime may advance only checkpoint fields. Apply existing-volume migration as the selected bootstrap owner after backup/restore and compatibility checks. Do not delete volumes to upgrade. Existing incident/health readers explicitly accept compatible version 3.

Each iteration uses a dedicated **nonpooled** PostgreSQL connection for its advisory lock and every journal/repository mutation. Source reads use a separate pool; no SQL transaction spans Docker/HTTP reads or raw-file verification. The dedicated connection is closed after the iteration. A pool proxy is rejected: disconnect can return its holder to the pool while an old iteration is still awaiting external input. Database uniqueness/CAS and immutable command/receipt identities protect retries.

The source step, pending state, finalized jobs/OPEN commands and matching recovery events commit atomically. An OPEN intent freezes the actual local `detected_at`; original SLO onset and evaluation clocks remain separate. RCA evidence is accepted once and recomputed from the frozen capture before projection. A crash after IER commit but before receipt reuses the same command/body/key/clock.

The consumer advances OPEN → RCA_READY → ESCALATED on abstention. All matching source episodes must have verified recovery decisions before natural closure. Current consumed UNKNOWN availability blocks closure even when older recovery decisions exist. Recovery references and the accepted current availability snapshot remain in the context; no action path is enabled. Restart/late backlog does not manufacture a historical alert-notification time, MTTD or causality.

## Run and inspect

Start the existing health producer first. Use an already configured loopback `CONTROL_DATABASE_URL` for the nonowner `control_writer` role; do not put credentials in arguments/logs. `COMMERCE_EVIDENCE_DATABASE_URL` optionally selects the explicitly owned loopback commerce database, not the control database. Both processes need the same original health source store.

```bash
.venv-ml/bin/python -m scripts.run_incident_worker \
  --health-stream <explicit-stream-id> \
  --source-store artifacts/live/local-health \
  --store artifacts/live/local-incidents \
  --project <owned-native-compose-project>
```

Use the same arguments plus `--status` for a read-only checkpoint lookup; status cannot initialize or capture/publish. `--max-ticks` bounds a run. SIGTERM/SIGINT request graceful shutdown; allow an active bounded capture to finish. Transport/database outages are reported without credentials and retried by the process. Integrity/configuration/role/schema failures stop the worker. Readiness/model availability, source availability, incident lifecycle and the process being alive are distinct.

### Optional operational metrics — software verified, native acceptance pending

Add `--metrics-otlp-endpoint http://127.0.0.1:4317 --metrics-instance incidents-0`
to export through a private provider. Concurrent replicas need distinct stable
slots `incidents-0` through `incidents-15`; reuse a slot only after its prior
process has stopped. Resource service name is `sentinelops-incident-worker`.
Only the fixed environment/slot, business-service and error-component labels
are used. Incident IDs, source references and paths are not labels. Read-only
`--status` cannot export metrics. Invalid exporter/slot configuration is rejected
before opening the database pool; a DB startup failure exports not-ready/error
when the configured exporter is available, then closes the private provider.

These are **process-local** counters, which restart at known zero:

| Metric | Meaning |
| --- | --- |
| `sentinelops_worker_committed_windows_total` | Number of `INGESTED` source steps observed by this process; a catch-up cycle can contain up to 64 |
| `sentinelops_worker_unknown_decisions_total` | UNKNOWN service observations across every accepted step, including intermediate backlog steps |
| `sentinelops_incident_groups_total` | New jobs in those observed INGESTED events; not a global durable incident count |
| `sentinelops_rca_abstentions_total` | Abstained results in newly observed ENRICHED events; retries already enriched are not new results |
| `sentinelops_worker_processing_duration_seconds` | Measured duration of valid PROGRESS cycles, including enrichment-only cycles; WAIT/BUSY do not add a sample |
| `sentinelops_worker_busy_polls_total` | Polls that did not acquire the shared consumer lock |

The loop refreshes heartbeat while a dependency retry remains not-ready. Export
collection cannot refresh it. `ready=1` indicates a successful process cycle,
including a follower's BUSY poll; it does not certify business health or leadership.
Source/end/age gauges remain unavailable until this process observes its own
accepted source step. Source age is event age, not online incident latency.

On slot reuse, the Collector can retain older source/histogram series. Guard
source/age/end queries with `sentinelops_worker_has_local_decision == 1`.
Guard latency with the separate `sentinelops_incident_has_local_progress == 1`:
an enrichment-only cycle can have a real duration without a new source decision.
Neither guard invents a zero-duration sample. For example:

```promql
sentinelops_worker_source_available{environment="local",worker_instance=~"incidents-[01]"}
  and on(job,environment,worker_instance) (sentinelops_worker_has_local_decision == 1)

sentinelops_worker_processing_duration_seconds_count{environment="local",worker_instance=~"incidents-[01]"}
  and on(job,environment,worker_instance) (sentinelops_incident_has_local_progress == 1)
```

Unit and captured-state counter checks cover backlog accounting, rejected-input
atomicity, progress/source separation and startup/retry cleanup. Real incident
OTLP/Collector/Prometheus acceptance with outages, crash/restart, follower and
journal/log comparison remains pending. Health-worker metrics acceptance and
general native incident CI do not substitute for this component's metrics proof;
full NFR-10 remains unaccepted.

## Verification and rollback

[The acceptance protocol](../configs/incident_worker_acceptance.json) retains the original S1 and both S5 development protocols. The extra S5 phase provides 180 seconds of explicitly declared healthy traffic at each entry after the original case; it observes the source's existing three-clear-bucket recovery rule without changing thresholds, active faults or the original case configuration. Final S5/RCAEval holdouts remain sealed.

The native S1 health checker accepts `--incident-consumer` to add a real consumer crash/restart, competing processes, bounded source RCA, complete lifecycle and transported journal/IER/audit/outbox snapshots. A PASS of the health producer alone does not prove this consumer. Live natural closure, multi-root ambiguity, failure/restart, package/regression and exact-head CI/artifact evidence are required before accepting the complete item. Source acquisition probes or a captured-source OPEN/RCA_READY/ESCALATED fixture do not replace them.

The S5 checker explicitly requires separate Inventory/Payment candidates and ambiguous Checkout, including its two structural candidates and RCA abstention. A conditional assertion that only inspects already-ambiguous labels is insufficient.

For a completed native run or safely extracted CI bundle, verify the source graph without a database or Docker. The optional expected checksums must come from the selected trusted capture/transport record; calculating them from an untrusted replacement does not authenticate it.

```bash
.venv-ml/bin/python -m scripts.replay_incident_worker \
  --run <completed-native-run> --output <new-replay-report.json> \
  --expected-health-snapshot-sha256 <trusted-health-export-sha256> \
  --expected-incident-snapshot-sha256 <trusted-incident-export-sha256>
.venv-ml/bin/python -m scripts.check_incident_replay_integrity \
  --run <completed-native-run> --output <new-negative-report.json>
```

The negative probe changes only isolated copies and checks original files afterward. Every JSON object is bounded to 16 MiB, artifact references are hash-verified, duplicate/nonfinite JSON is rejected, and optimized Python cannot silently disable acceptance. Replay preserves clocks and reconstructs raw RCA; it is not a new live measurement or accuracy trial. This verifier targets completed native S1/S5 acceptance captures with all four lifecycle revisions, not an actively growing/unclosed journal.

With the selected owned loopback `CONTROL_DATABASE_URL` already configured, verify actual API reads against one or more native runs:

```bash
.venv-ml/bin/python -m scripts.check_native_incident_api \
  --run <completed-native-run> --output <new-api-evidence-directory>
```

Repeat `--run` to select multiple captures in the same control database. The checker starts a temporary loopback API with an ephemeral environment-only token, checks current/history/auth/readiness/ETag/pagination against selected immutable snapshots, checks selected revision/outbox counts unchanged, then stops its API process. It does not write incidents or enable demo/model output. CI performs these native API and offline checks before uploading S1 and each S5 bundle.

Rollback normally stops the consumer while retaining the health producer, immutable objects, accepted journal and compatible schema-3 readers. Restore requires a rehearsed backup and accounting for later writes; code revert does not recover data. Keep project/container/DB identities and export evidence before cleaning only the task's disposable resources. No production/on-call/HA or host-power-loss durability claim is made by local integration.
