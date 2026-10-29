# Continuous local incident consumer

`scripts.run_incident_worker` consumes one explicit committed health stream and
reuses raw-lineage verification, the accepted episode reducer, native RCA support
derivation and the existing IER repository. No model is activated or action dispatched.

Apply additive `infra/control/004-incident-worker.sql` as the dedicated database
migration owner for existing schema-v2 volumes. Fresh control Compose databases
apply it automatically. Never remove a volume to upgrade. Health/API readers accept
schema 3; the consumer requires migrations 1, 2 and 3 and a nonowner runtime role.

```sh
python -m scripts.run_incident_worker --store artifacts/live/local-health \
  --stream-id HEALTH_STREAM_SHA256 --native-project EXPLICIT_LOCAL_PROJECT
```

Set `CONTROL_DATABASE_URL` in the environment to the dedicated loopback
`control_writer` database. The store must be the health stream's original root.
Omit `--native-project` for explicit SLO-only evidence. `--status` reads the cursor.
Source identity/policy/topology/evidence profile fix the consumer identity. A changed
configuration creates another consumer; it is not a checkpoint migration.

The 120-second onset cluster closes only after the accepted input watermark passes
the last possible two-bucket opening. This delays final grouping to prevent a late
independent sibling from being misattributed to an earlier cascade. Wall-clock
silence cannot finalize membership. Event onset, health evaluation, SQL receipt and
actual consumer processing clocks remain separate; IER detection uses the latter.

Per-consumer session advisory locks serialize replicas without a source-I/O SQL
transaction. A single subsequent transaction persists all computed OPEN/RCA_READY/
ESCALATED revisions, immutable audit/outbox events, accepted input batch and cursor.
Crashes before that commit leave no accepted IER/checkpoint; crashes after it resume
at the next bucket. Compare-and-set and stable incident/idempotency identities add
guards. Database transport errors retry with bounded exponential delay. Invalid
source/schema/filesystem/configuration fails closed with a structured error class.

Native snapshots use only business completions and matching Jaeger trace IDs,
record actual receipt clocks, bound records/bytes and retain unavailable traces.
No fault schedules, experiment labels or deployment invention enters RCA. The
existing RCA detail remains explicitly analytical even when invoked continuously;
this is not an online diagnostic accuracy result. Missing IF/XGBoost stay absent;
SLO-only incidents keep empty legacy service signals and explicit limitations.

Each group is escalated for operator review; this consumer does not claim automatic
recovery, action authority, or a closed incident when later health clears. Subsequent
episodes have new identities. Lifecycle/action verification remains a separate writer.
PostgreSQL history is append-only to the runtime role. Orphan content-addressed files
from aborted transactions are harmless and never treated as accepted decisions.

Run `pytest -q tests/test_incident_worker.py` for fixture coverage; set
`SENTINELOPS_TEST_DATABASE_URL` to an isolated local database to execute the real
PostgreSQL transaction/crash/competition test. `scripts.check_continuous_incidents`
runs actual workload, consumer SIGKILL/competition, source and DB outages, and
protected HTTP readback for about 21 minutes. It verifies container ownership and
ports, restores original faults, preserves artifacts and never removes volumes.
