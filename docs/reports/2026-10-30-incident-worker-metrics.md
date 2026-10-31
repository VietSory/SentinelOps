# Continuous incident worker operational metrics — measured local evidence

The native S1 integration was measured at `07c57c7e1fdefc2e3d6b4612c5f91d384c3fc1a7`. Optional private OTLP metrics now account for every accepted backlog step and expose honest process liveness through the existing Collector and Prometheus. The SLO, correlation, RCA, data-source and model policies are unchanged. [Measurements](evidence/2026-10-30-incident-worker-metrics.json), [metric definitions and queries](../runbooks/INCIDENT_WORKER.md#optional-operational-metrics) and the [versioned acceptance profile](../../configs/incident_metrics_acceptance.json) identify the scope.

## Corrected behavior

A single consumer cycle may accept multiple source steps. UNKNOWN counters now include each step instead of just the last one; source availability and bucket-age gauges still describe only the last accepted step. The complete result and referenced artifacts are validated before counters change. Rejected batches cannot leave partially updated metrics.

A separate local-progress guard admits a real enrichment-only processing duration without inventing a local source decision. WAIT/BUSY polls do not add a duration sample. Startup validates exporter settings before opening a pool, owns cleanup, and reports not-ready/errors when export is available. Dependency retries refresh only the actual main-loop heartbeat. Startup/invalid-input cleanup is software-tested; the actual native outage evidence below covers an already running worker.

## Actual native measurements

- Accepted producer ledger: **23 buckets / 69 signals**, all timed and raw-replayed; maximum producer-cycle duration **570.373 ms**. This is producer processing time, not incident detection latency.
- One incident reaches all **4 natural lifecycle revisions** with matching immutable events/outbox, even across worker crash/restart and database/source outages. RCA abstains; metrics do not change that diagnosis.
- Before primary SIGKILL, actual Prometheus export shows **1 created group and 1 RCA abstention** matching completed PROGRESS logs. A durable IER alone is insufficient proof of this metric branch.
- Both restarted processes begin with zero local-decision guards; guarded source/age/end queries exclude retained Collector series. The reused slot and a distinct competing slot are observed separately.
- After pausing consumers for the predeclared 130 seconds, health buckets advance **16 → 18** while the incident journal remains at **16 steps**. With the Collector stopped for 75 seconds, journal steps advance **16 → 19**; consumers remain alive and export recovers afterward.
- A real catch-up cycle accepts **2 source steps**. Independent recomputation accounts for **21 UNKNOWN service observations** across restarted processes, including intermediate backlog decisions.
- Current-process commits `{"incidents-0": 7, "incidents-1": 5}`, BUSY polls `{"incidents-0": 46, "incidents-1": 44}`, valid-cycle histogram counts/sums, group/abstention counters and latest-source gauges match process logs and the durable accepted journal. These counters reset after restart and are not global database totals.
- Database outage/recovery snapshots show not-ready/errors then ready with fresh main-loop heartbeats. Shutdown readiness is zero. Nine copied metric corruptions and ten raw/journal corruptions are rejected; original evidence files remain unchanged. Actual protected HTTP reproduces current/history data, ETag/pagination and authorization without side effects; default model readiness remains 503.

Software verification separately passes **36 focused / 649 regression tests with no skips**, **23 schemas / 17 samples / two policy configurations**, and installed-wheel checks outside the checkout. All 237 frozen tested file hashes and 114 native source hashes match the measured source. A Docker context check retains ordinary source while excluding private checkout instructions and operational files.

## Retained coverage gap

The earlier measured run at `0056433` passed its covered native/replay/HTTP paths, but the primary was killed before completed positive group/abstention export. It therefore did not accept this full metrics component. The new `incident-metrics-acceptance/v2` profile requires both positive observations before the crash; business thresholds and 60-second/300-second SLO windows remain unchanged. Earlier captures and their scope remain preserved.

## Scope and release boundary

This is development integration on one local testbed. It is not an independent trial, training data, historical alert-time/MTTD/causal evidence, full NFR-10/G4, Grafana deployment or production acceptance. GAMMA training/RCAEval evaluation roles, sealed holdouts, pre-SLO XGBoost objective and zero approved models remain unchanged. Exact published-SHA CI and transported S1 plus both S5 raw/metrics bundles must pass separately before release closure; local PASS does not certify CI. Metric changes do not authorize model or action activation.
