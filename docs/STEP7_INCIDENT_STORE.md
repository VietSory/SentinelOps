# Incident storage foundation

This is the storage/lifecycle work allowed to start before G3 in the delivery plan. It does not implement correlation/RCA, model inference, recovery verification or remediation authorization and does not close G4. The foundation HTTP incident endpoint remains an echo until its separately tested persistence migration.

## Isolation and local setup

`docker-compose.control.yml` provides a dedicated PostgreSQL 16 database on loopback port 5433, separate from the commerce database. Local bootstrap creates a nonlogin schema owner and a nonowner `control_writer` login without superuser, role creation, database creation or schema CREATE privileges. Bootstrap credentials are never the runtime DSN. Committed passwords are disposable local sandbox defaults; AWS provisioning must supply independent secrets/roles.

```bash
docker compose -p sentinelops-control -f docker-compose.control.yml up -d --wait
export CONTROL_DATABASE_URL=postgresql://control_writer:local-control-writer@127.0.0.1:5433/sentinelops_control
python scripts/check_incident_store.py
docker compose -p sentinelops-control -f docker-compose.control.yml restart control-postgres
docker compose -p sentinelops-control -f docker-compose.control.yml up -d --wait
python scripts/check_incident_store.py --verify-report artifacts/incidents/store-acceptance.json
```

Initialization SQL only runs on a fresh control volume. Do not erase an existing volume to apply a future migration; add and run a versioned owner migration. Runtime readiness checks the migration version. This local stack does not alter commerce roles, tables or traffic and is not an AWS security acceptance result.

## Revisions and transactions

Trusted producers construct an `IncidentWrite` with the exact existing IER, lifecycle stage, expected revision, stable idempotency key, actor/reason and supplemental provenance. New records require expected revision 0 and stage OPEN. Updates lock the current incident row and compare revisions. Current pointer, immutable version, audit event and outbox entry commit in one transaction. A failure in any write rolls all of them back.

An exact retry returns the original stored revision even after newer revisions exist. Reusing its idempotency key for different payload, expected revision, context, actor or reason conflicts. Concurrent writers using the same expected revision cannot both advance it. Detection time is immutable; CLOSED cannot be reopened under the same incident ID. Recurrence needs a new incident.

The runtime role has INSERT/SELECT but no UPDATE/DELETE/TRUNCATE on versions/events. It can update only the current pointer/stage/timestamp and outbox lease/completion columns. This protects historical records from ordinary runtime writes; database owners/administrators remain privileged and require separate production audit/access controls.

## State and provenance

Lifecycle stages follow Technical Spec §9.3; `IER.state` remains its frozen enum and is checked against the stage. Availability UNKNOWN lives outside IER and cannot project recovered. Recovery must name natural/action/manual provenance. OPEN/RCA_READY/EXPLAINED may enter VERIFYING directly only with explicit natural recovery; verification cannot be skipped and no fictional action is created. Triggers explicitly distinguish anomaly, risk, SLO and manual sources, with signal identity, first emission time and source reference. References/config identities and actor/reason are retained per revision.

These checks establish structural integrity for trusted producers. They do not certify that an external reference contains true data, that three consecutive recovery buckets passed, that an explanation was grounded or that a Kubernetes action was authorized. Those producers/verifiers must supply their measured evidence before advancing state; no runtime side effect is implemented here. No public write API is added by this storage-only change.

## Transactional outbox

Each revision creates one `incident.revised` outbox event. Workers claim bounded batches using `FOR UPDATE SKIP LOCKED`, receive opaque lease tokens and acknowledge only while their lease is current. Expired work can be reclaimed; old tokens cannot acknowledge a new claimant's work. Delivery is at least once. Consumers must deduplicate event IDs; this is not a claim of exactly-once external actions. Topic routing/fan-out, retry exhaustion and production scheduling are integration work.

## Acceptance scope

`scripts/check_incident_store.py` uses the actual nonowner runtime role against an exclusive local control sandbox. It inserts explicitly synthetic storage fixtures, tests eight concurrent identical creates, competing revision updates, changed-key conflicts, an injected post-version event constraint failure, append-only/DDL denials, disjoint concurrent outbox claims, expiry/reclaim and stale acknowledgement rejection. It then verifies stored payloads/history again after a real database restart when `--verify-report` is used. Synthetic IER contents are not RCA accuracy evidence.

The lock/isolation design follows PostgreSQL 16 [explicit locking](https://www.postgresql.org/docs/16/explicit-locking.html), [transaction isolation](https://www.postgresql.org/docs/16/transaction-iso.html) and [schema privileges](https://www.postgresql.org/docs/16/ddl-schemas.html). Tests check the repository's actual transaction scope and runtime privileges in addition to the database guarantees.
