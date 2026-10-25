# Phase 4 local RED pipeline verification — 2026-10-26

Scope: local metric history → immutable source captures → quality-aware telemetry → five-minute features → deterministic replay. Supporting evidence includes real logs/traces/database spans and a separate read-only PostgreSQL observation. No trained model, RCA accuracy, AWS integration or release acceptance is claimed.

## Actual results

| Check | Measured result |
| --- | --- |
| Workload | 1260 successful checkouts, 360 expected cascading 502s, 0 unexpected statuses |
| Capture | 27 service/minute observations, source profile `prometheus-increase-60s-5s/v1` |
| Quality | 24 valid; 3 incomplete withheld from inference; coverage 88.9% for this short run |
| Feature windows | 15 eligible: 5 each for Checkout, Payment and Inventory |
| Payment fault visibility | Maximum histogram-estimated p95 1237.5ms for 1200ms injection; maximum 5xx ratio 1.0 |
| Raw replay | Exact same manifest/feature identities from saved captures without networking |
| Trace/log correlation | Complete business chain and same server span IDs in JSON logs |
| Database evidence | Two real DB CLIENT spans have the correct Payment/Inventory SERVER parents; SQL text/params omitted |
| Native DB snapshot | Read-only source measured 2 other client connections / 97 usable slots = 2.06185567%; local PostgreSQL, not AWS RDS |
| Source outage | Real Prometheus stop produced quarantine and zero fabricated observations |
| Restart/history | After source restart, the previous canonical historical payload was preserved |

The 95% coverage release target is not met by this short run; its incomplete interval remains visible. G1 local RED correctness/replay checks pass, while sustained availability/data-volume targets and expanded experiment coverage remain work for P5/G2 and release gates.

## Provenance and review limits

The live run began at `7a2b96b`. Additive infrastructure work was committed during the run; the legacy reporter recorded HEAD `211e4e7` at completion. The normal RED capture/feature path was unchanged, and saved captures replay under the subsequent hardening changes. New reports now record start and completion commit/dirty state separately; a clean CI run is the independently pinned execution.

The [committed summary](evidence/2026-10-26-phase4-local-summary.json) records counts and immutable manifest/evidence references. Raw source/log/trace objects remain in ignored `artifacts/step4/2026-10-26-live-5s`; outage evidence is in `artifacts/step4/source-outage`. These local objects are not committed as a replacement for an artifact store.

GitHub run `40218640416` on the earlier branch head reports foundation, installed-wheel, Step 2, Step 3 and Step 4 steps successful. Its overall integration job failed only when GitHub ArtifactService CreateArtifact timed out after five attempts. The revised workflow preserves checksum-backed summaries in job logs/summary and reports raw-upload transport failure explicitly as an archival warning. Full release evidence still requires durable raw archival; application test failures remain fatal.

The failed initial 15s scrape run is retained in [first-attempt report](2026-10-26-phase4-first-attempt.md). No failure was relabeled as PASS.
