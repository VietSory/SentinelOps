# Continuous local health producer — 2026-10-28

The [worker/runbook](../runbooks/HEALTH_WORKER.md) implements a continuous local metric→feature→SLO producer with atomic PostgreSQL checkpoints. [Machine-readable evidence](2026-10-28-continuous-health-worker.json) is derived from actual native and database artifacts. This is integration evidence, separate from ML training, final holdout and continuous RCA/IER acceptance.

## Measured native result

The unchanged [protocol](../../configs/health_worker_acceptance.json), SHA256 `3fd268bd99ee8fe80e316059c09e2f661285110d923b97e0cd71778a4ec0c1be`, ran actual 3-RPS Checkout traffic, a 1,200-ms Payment latency intervention, recovery, worker SIGKILL/resume with two processes, a 95-second database outage and a 45-second Prometheus outage with continued business traffic. Recorded intervention times describe actual execution; nominal durations are not substituted for event clocks.

Native source commit: `084539129f16f8c023a8716ccacb87c9a5064f1c`. Run: `run-1839793efb7749b497ef34ed189312a1`. The native service-image Python files were hash-equal to the workspace; image digests are recorded. Host worker code was frozen before capture.

| Check | Actual result |
| --- | --- |
| Accepted ordered buckets / typed signals | 19 / 57, exactly three services per minute |
| Raw→feature→health replay | All accepted batches reproduce stored immutable references and signal payloads |
| Checkout and Payment episodes | One opening and one recovery each |
| UNKNOWN while an episode remains active | One decision per affected service; no false recovery |
| Inventory episode openings | Zero |
| Availability | 14 ELIGIBLE / 5 UNKNOWN decisions per service |
| Missed-deadline bucket | One, explicitly UNKNOWN without backdated re-query |
| Source outage | All three services recorded UNKNOWN; 539 extra Checkout requests returned 200 |
| Eligible current-window feature outputs | 33, not a healthy-training or independent-run count |
| Processing measurements | 19/19 accepted buckets have actual cycle timing |
| Maximum complete processing cycle | **527.763 ms**, below the unchanged 10,000-ms gate |

Artifacts: `artifacts/workers/live-v2/acceptance.json`, `post-review-verification.json`, `database-snapshot.json`, `offline-ledger-replay.json`, worker logs and immutable `worker/` objects. Exported accepted-ledger replay works without a running database; it is replay of captured real database records, not another database integration test. Decision time, source receipt and database recording time remain distinct.

## Failure retained and review fixes

The first native attempt at `fb8bd4430b1324c9c49c8957bb7375733d07627e` failed the ten-second processing gate: a window took **18,777.63 ms**. Its failure frame, logs, database snapshot and raw objects remain under `artifacts/workers/live-v1/`. It is not relabeled PASS. Other replay/lifecycle/source-outage assertions completed before that timing assertion failed.

Profiling found 46 duplicate temporary-file/fsync operations in a cached normalization replay. The immutable-store fast path now compares existing bytes and returns the same reference without rewriting them; corrupt cache remains an error, and concurrent first creation retains atomic exclusive publication. On the measured workspace replay, duplicate processing changed from 406.869 to 102.633 ms and 46 to zero temporary files; normalization references stayed identical. This microbenchmark does not establish the cause of every tail-latency spike or production p99. The corrected native run supplies the separate gate evidence above.

Review also replaced two independent status reads with one SQL snapshot. An actual v1→v2 sandbox, eight concurrent processing attempts and 100 status reads preserved pointer/batch/signal consistency while observed batch counts advanced from two to three. The raw-write/health methods are unchanged from the native source commit; this later status-only runtime fix was verified separately. The strengthened native verifier also requires timing coverage for every accepted batch; all 19 existing native timings pass that check. Final candidate CI must run the complete protocol on its own SHA.

## Software, storage and release scope

Final local regression: **444 tests pass**. All 23 schemas, 17 samples and two model-policy configurations validate. The installed wheel passes outside the checkout. The storage acceptance proves explicit migration from schema 1, idempotent migration, nonowner/history protection, rollback after the third signal fails, session lock/disconnect behavior and actual database outage/restart. It is explicitly fixture-only storage evidence. Existing incident-store acceptance also passes against schema 2.

The CI candidate adds a separate `health-worker-integration` job and `health-worker-evidence` artifact: real v1 migration/storage acceptance, the same native protocol, captured ledger and immutable objects. Published completion requires the exact candidate's six jobs and five required artifacts; an older green CI does not prove this candidate.

These are development executions on a controlled testbed, not proven independent trials or primary training data. No IF/XGBoost fit, live model activation, final RCA evaluation, continuous correlation/RCA/IER, explanation/UI, remediation, cloud operation or full G4 is accepted here. Prometheus-exported worker own-health/lag metrics and dashboard acceptance remain open; structured timing logs alone do not close NFR-10.
