# Baseline audit — 2026-10-26

Source baseline: `20edcae`. Runtime: Python 3.11 virtualenv; Docker Engine 29.5.2; Compose 5.1.4. Local sandbox only. No AWS execution or model evaluation.

| Check | Actual result |
| --- | --- |
| Unit/API/contract/observability-validator suite | PASS: 25 tests, 1 upstream Starlette/AnyIO deprecation warning |
| JSON Schema validation | PASS: 7 schemas + 3 samples |
| Docker build/start | PASS: PostgreSQL, 3 services, Collector, Jaeger started |
| Step 2 real smoke | PASS: healthy checkout, ~1.21s latency propagation, cascading 502, recovery |
| Step 3 real smoke | FAIL: `/api/services` returns 404 on pinned Jaeger 2.21.0; `/api/v3/services` returns 200 |
| Installed-package API inside service image | FAIL: schema loader searches `/usr/local/lib/python3.11/contracts/`; schemas absent from wheel |

G0 engineering status: FAIL until runtime defects are fixed and regression checks pass. Editable-source unit tests did not expose the installed-package defect. Existing unit validators also did not prove live Jaeger endpoint compatibility.

Required fixes: bundle schemas as package resources and test installed wheel independently; migrate live trace querying to supported v3 API with strict response validation and negative cases. Subsequent reports must state the actual repaired commit/config and rerun results.

The initial sandbox-restricted pytest invocation stalled; the bounded run with required local socket/thread permissions completed in 3.10s. This was an execution-environment issue, not a test PASS.
