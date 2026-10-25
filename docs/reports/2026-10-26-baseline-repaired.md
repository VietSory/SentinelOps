# Baseline repaired — 2026-10-26

Parent architecture commit: `75a7975`; repaired source is the commit containing this report. Environment: Python 3.11 virtualenv, Docker Engine 29.5.2, Compose 5.1.4, pinned Collector 0.162.0 and Jaeger 2.21.0. Local sandbox; AWS/model quality not evaluated.

| Check | Result |
| --- | --- |
| Unit/API/contracts/observability regression suite | PASS: 35 tests; one upstream Starlette/AnyIO deprecation warning |
| Schema/sample validation | PASS: 7 schemas, 3 samples |
| Replay CLI with actual telemetry sample JSONL | PASS: 1 telemetry point accepted |
| Wheel build/install outside checkout | PASS: all 7 runtime schemas; valid/invalid detect; demo IER; import resolves to installed wheel |
| Rebuilt Docker stack | PASS: all services healthy |
| Step 2 live smoke on rebuilt images | PASS: persisted checkout, ~1.22s latency, cascading failure, recovery |
| Step 3 live smoke | PASS: four traces, correct SERVER→CLIENT→SERVER parents, 5xx error status, per-instance metric deltas, exact log/trace/span linkage |
| Installed control-plane API in rebuilt container | PASS: detect and demo incident return 200 |

Fixes: package root-authoritative schemas in wheels via the build hook and load through importlib.resources; use Jaeger v3 JSON/NDJSON instead of unavailable legacy UI API; compare metric progress per resource/instance and reject non-finite counters. Added regression coverage for malformed/incomplete/error v3 streams, stale/expired resource series and counter reset. CI now exercises the installed wheel as well as editable source.

Baseline runtime requirements for Foundation/Step 2/Step 3 now pass locally. This is not an AWS, trained-model, independent human-review or later-phase acceptance claim. Numerical targets and external project/account decisions remain provisional as recorded in the gate document.
