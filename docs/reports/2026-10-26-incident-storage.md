# Incident storage foundation — local acceptance

Date: 2026-10-26. This verifies the storage prerequisite that the delivery plan permits before G3. G2/G3/G4, RCA accuracy and closed-loop remediation are not claimed.

## Executed evidence

- Full Python suite: **120 passed** (one upstream Starlette/AnyIO deprecation warning).
- Dedicated Docker PostgreSQL 16 control database initialized separately from commerce.
- Real `control_writer` role has no superuser/role/database creation privileges. UPDATE/DELETE of historical versions/events and schema CREATE were rejected by PostgreSQL.
- Runtime readiness rejected an actual connection using the privileged bootstrap DSN.
- Eight concurrent identical creates produced revision 1 with identical replay responses.
- Two different updates with expected revision 1 produced exactly one revision 2 and one conflict.
- An exact creation retry after revision 2 returned its original revision 1; changed contents under that key conflicted.
- A real event UUID constraint failure after pending version/current-pointer writes rolled back the full transaction; current revision/history/version count remained 2.
- Concurrent outbox claims returned disjoint events. A 1s expired lease was reclaimed with attempt 2; its old token could not acknowledge the new lease.
- After a **real control PostgreSQL container restart**, `--verify-report` read back the same current payload, revision and audit history.

Commands and role boundaries are in [the storage guide](../STEP7_INCIDENT_STORE.md). Local acceptance output is `artifacts/incidents/store-acceptance.json` (ignored runtime artifact). Its IER data are explicitly synthetic storage fixtures; their evidence text/URLs are not measured RCA facts. CI runs the actual database acceptance and restart path in a separate job.

## Limits

The repository enforces structure, optimistic concurrency and immutable history for trusted producers. It neither validates external evidence truth nor authorizes actions. Outbox delivery is at least once; production consumers, fan-out and exhaustion policy still require integration. No API behavior changes in this PR; Foundation writes still echo. Database owners remain privileged and need separate production controls. The local bootstrap is not an AWS deployment or least-privilege IAM acceptance.
