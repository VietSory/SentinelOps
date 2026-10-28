# Continuous local SLO health worker

The worker runs separately from the API and reuses the [existing SLO policy](../configs/slo_health_policy.json), native Prometheus decoding and feature transformations. It persists an ordered three-service health stream in the control PostgreSQL database. This is a rule-source producer: it does not train/activate IF or XGBoost, perform continuous correlation/RCA, create IERs, dispatch actions or accept full G4.

## Source and decision clocks

The local source must expose the configured five-second OTel scrape profile. The worker selects closed UTC minute buckets after the thirty-second source watermark, preserves actual query receipt times, and evaluates health using the actual UTC decision clock. The existing sixty-second maximum lag is unchanged. A decision missed while the process/database was unavailable is recorded as UNKNOWN; historical re-query does not invent an on-time healthy decision.

All three business services receive one decision per accepted bucket. Two consecutive eligible violating buckets open an episode; three consecutive eligible clear buckets confirm recovery. Missing, malformed, insufficient-traffic/coverage, stale or future data cannot become healthy or clear an active incident. Quarantined source objects remain linked. Recent native captures reconstruct the five-minute feature history after restart; feature eligibility is not model availability or independently certified training health.

## Durable state and concurrency

[Migration 003](../infra/control/003-health-worker.sql) adds schema version 2:

| Table | Responsibility |
| --- | --- |
| `health_streams` | Immutable source identity, initial bucket and current checkpoint |
| `health_batches` | Append-only accepted bucket and immutable manifest reference |
| `health_signals` | Append-only typed per-service signal, payload/hash/reference |

The three signals, accepted batch and checkpoint advance share one SQL transaction. A scoped PostgreSQL session advisory lock serializes producers for the same stream without holding a SQL transaction during source HTTP requests. A competing process receives BUSY; disconnect releases its session lock. Compare-and-set protects the pointer. Content-addressed files created before a failed transaction may be orphan objects; they are not accepted decisions without the database ledger.

Stream identity binds environment, source/profile/policy, observed Prometheus configuration, endpoint and local artifact-root identity. Both processes must use the same root and settings to share a stream. Changing the root/config creates another stream; moving files is not a checkpoint migration. Read-back verifies typed payloads, object hashes, prior batch/service coverage and source identity.

The runtime role can append health history and update only checkpoint fields. It cannot update/delete history, change identity or create schema objects. Incident API readiness accepts schema 1 or 2; the worker requires the explicit complete 1→2 migration. Deployment/bootstrap credentials are separate from `control_writer`.

## Run and inspect

Use the repository environment, a credential-free loopback Prometheus endpoint, and an already configured `CONTROL_DATABASE_URL` for the dedicated local `control_writer` database. Do not put the database URL in process arguments or print it in logs.

```bash
.venv-ml/bin/python -m scripts.run_health_worker \
  --store artifacts/live/local-health --environment local

.venv-ml/bin/python -m scripts.run_health_worker \
  --store artifacts/live/local-health --environment local --status
```

`--max-buckets` bounds an execution when needed. SIGTERM/SIGINT request graceful shutdown. A transport/database outage is logged without credentials and retried by the live process; integrity, configuration, schema/role or filesystem errors fail closed. `--status` verifies the matching source configuration before reading the stream. The output includes stream/bucket identity and full cycle plus stage timings; source lag and processing time are distinct quantities.

New dedicated test volumes apply migration 003 through Compose initialization. Existing v1 volumes require an explicit migration as the bootstrap/migration owner. Do not delete volumes to upgrade. Apply only against the intended control project/database, verify schema/permissions/history and keep a tested recovery path.

For a worker rollback, stop its processes and retain the accepted checkpoint, immutable objects and schema-v2-compatible API readiness code. An older binary that requires exactly schema 1 will reject version 2; reverting all code alone is not a database rollback. Preserve the additive tables/history or use an explicitly planned backup/restore migration. Do not truncate history to make readiness green.

## Verification and limits

[The frozen acceptance protocol](../configs/health_worker_acceptance.json) separates storage fixtures from native runtime evidence. The real storage check upgrades an actual v1 sandbox, checks nonowner privileges, fails the third signal to prove transaction rollback, exercises concurrent/session-disconnect handling, and stops/restarts the exact database container.

The native check uses actual business traffic, a Payment latency intervention, worker SIGKILL/resume with two processes, a database outage, and an aligned Prometheus outage. It replays every accepted raw→feature→health batch using its recorded decision clock, checks episode/UNKNOWN behavior and the ten-second processing gate. The first measured development attempt failed that gate; the failure remains evidence. Later software checks do not erase a failed runtime attempt. The [measured local report](reports/2026-10-28-continuous-health-worker.md) retains both attempts and their version scopes. Accepted database-ledger snapshots are archived beside immutable objects so replay does not require keeping disposable database volumes.

This local producer does not establish historical alert emission, MTTD, causality, source-transfer/model quality, high availability or final heldout performance. Process/database restart tests do not prove host/power-loss durability, replication or AWS/S3 operation. Continuous correlation/RCA/IER consumers and their checkpoint/lifecycle handling remain separate work. Optional worker metrics are described below; all-module dashboards and full NFR-10 acceptance remain open. Default inference remains unavailable without an approved model.

Replay an archived accepted ledger and its original immutable worker objects without database/network access:

```bash
.venv-ml/bin/python -m scripts.replay_health_worker \
  --run artifacts/workers/live-v2 \
  --output artifacts/workers/offline-review.json
```

Use `--expected-snapshot-sha256` to bind the input to a trusted measured manifest. Output is exclusive. Replay checks recorded data; it does not establish a new live run or independently authenticate the source's honesty.

## Optional worker operational metrics

Enable explicitly against the local OTel/gRPC receiver; exporting is disabled by default and `--status` cannot start an exporter:

```bash
.venv-ml/bin/python -m scripts.run_health_worker \
  --store artifacts/live/local-health --environment local \
  --metrics-otlp-endpoint http://127.0.0.1:4317 --metrics-instance health-0
```

The private SDK provider exports every two seconds with a one-second exporter timeout through Collector→Prometheus. It does not set the global provider. Resource attributes use an explicit service/namespace/environment allowlist; ambient OTel resource attributes and credential headers are not forwarded. A fixed noncredential header avoids the pinned SDK's fallback from empty headers to environment tokens. The endpoint must be credential-free loopback HTTP, with gRPC HTTP proxying disabled. Final flush/shutdown receive one-/two-second budgets; exporter failure does not manufacture a business-health value.

Use a distinct stable `health-0`…`health-15` slot for each concurrent process and reuse its slot after restart. Approved metric environments are `local`, `test`, `staging`, `production`; enabling a production label does not deploy a production worker. Stream/run/incident IDs, file paths and PIDs are not metric attributes. The slot is operator assigned; the exporter does not provide distributed slot leasing. Two processes using the same slot can collide in the Collector; configure them distinctly. Cumulative counters explicitly initialize to known zero on process restart; use `rate`/`increase` for time-window totals and the durable ledger for exact accepted history. Histograms receive no fabricated zero-duration sample; use the local-decision guard to exclude retained earlier histograms before a new local commit.

| Prometheus series | Meaning |
| --- | --- |
| `sentinelops_worker_ready` | Initialization completed and the most recent loop database attempt succeeded; zero before initialization, after a database error and on shutdown. Not business-source health |
| `sentinelops_worker_heartbeat_unixtime_seconds` | Actual timestamp refreshed by the main loop; SDK exporter collections cannot advance it |
| `sentinelops_worker_bucket_age_seconds` | Current time minus this process's last committed bucket end; absent before its first commit or when the clock precedes that end. Normal watermark/cadence causes roughly 30–90 seconds, not zero |
| `sentinelops_worker_source_available{business_service}` | Latest decision committed by this process was ELIGIBLE=1 or UNKNOWN=0. An incident can be ELIGIBLE; this gauge is not healthy/unhealthy. Absent before a local commit |
| `sentinelops_worker_has_local_decision` | Zero until this process commits its first window; required to exclude source/age series retained by the Collector from an earlier process using the same slot |
| `sentinelops_worker_bucket_end_unixtime_seconds` | Last local accepted bucket end; use `time() -` this timestamp to recompute age and reject future buckets independently of cached age values |
| `sentinelops_worker_committed_windows_total` | Accepted windows committed by this process, three typed service decisions per window |
| `sentinelops_worker_busy_polls_total` | Another database session owns the stream lock |
| `sentinelops_worker_errors_total{component}` | Fixed component taxonomy: source, database, artifact, configuration, runtime |
| `sentinelops_worker_unknown_decisions_total{business_service}` | UNKNOWN decisions in this process's accepted windows |
| `sentinelops_worker_processing_duration_seconds_{bucket,count,sum}` | Full producer cycle duration including lock/pool return, converted from milliseconds. Explicit histogram boundary at ten seconds; excludes optional post-commit metrics observation |

Operational readiness must require a fresh nonfuture main-loop heartbeat, for example:

```promql
(sentinelops_worker_ready == 1)
and on(job, environment, worker_instance) (time() - sentinelops_worker_heartbeat_unixtime_seconds < 15)
and on(job, environment, worker_instance) (time() - sentinelops_worker_heartbeat_unixtime_seconds >= 0)
```

An empty result means unavailable/unknown; do not fill missing series with healthy zero/one. A SIGKILL can leave the Collector serving the last ready gauge, so readiness alone is insufficient. SDK source/age observations are absent before the first local commit, but the Collector can still retain an earlier process's series when a slot is reused. Require `has_local_decision == 1` before using those values. For example, source eligibility with local ownership and a nonfuture accepted-window age of at most ninety seconds is:

```promql
sentinelops_worker_source_available
and on(job, environment, worker_instance) (sentinelops_worker_has_local_decision == 1)
and on(job, environment, worker_instance) (time() - sentinelops_worker_bucket_end_unixtime_seconds >= 0)
and on(job, environment, worker_instance) (time() - sentinelops_worker_bucket_end_unixtime_seconds <= 90)
and on(job, environment, worker_instance) (sentinelops_worker_ready == 1)
and on(job, environment, worker_instance) (time() - sentinelops_worker_heartbeat_unixtime_seconds >= 0)
and on(job, environment, worker_instance) (time() - sentinelops_worker_heartbeat_unixtime_seconds < 15)
```

The ninety-second display bound follows the minute cadence plus thirty-second watermark; it does not change SLO eligibility or latency gates. Plot accepted-window age separately from processing latency. A busy replica may have no local source decisions. Neither gauge is an HTTP SLO denominator or model feature. Metrics and controls remain outside the worker's business evidence store.

The optional native acceptance flag `--metrics-otlp-endpoint http://127.0.0.1:4317` verifies the owned collector, actual Prometheus readiness during database outage/recovery, two stable replica slots, and final counters/histogram sums against resumed process logs. It preserves the frozen business/SLO/outage protocol and all existing ten-second gates. SDK tests alone are not transport proof; native results and exact-head CI/artifacts must be inspected before release acceptance. No Grafana dashboard is deployed by enabling this flag.
