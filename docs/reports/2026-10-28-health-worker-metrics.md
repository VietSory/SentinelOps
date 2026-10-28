# Continuous health worker operational metrics — measured local evidence

This development integration was captured at `8dc8f3c14faf8f121f827582536e5d96940eb6c2`. It adds optional worker telemetry through the existing local OTLP Collector and Prometheus. The existing SLO/checkpoint algorithm, frozen native protocol and ten-second processing gate are unchanged. [Machine-readable measurements](2026-10-28-health-worker-metrics.json) and [operator definitions/queries](../STEP7_HEALTH_WORKER.md#optional-worker-operational-metrics) describe the version and scope.

## Measured behavior

- Native accepted ledger: **19 buckets / 57 signals**, **19 timed buckets**, maximum producer-cycle time **385.756 ms**. Every accepted raw→feature→health result reproduces at its recorded decision clock.
- Actual primary SIGKILL/resume with two fixed replica slots; missing database decisions and source outage remain UNKNOWN. Checkout/Payment episode opening/recovery and unrelated Inventory behavior retain the original assertions.
- Real Collector→Prometheus snapshots show both replica readiness gauges zero during the explicit database outage and one after recovery, with fresh main-loop heartbeats. Both are zero after graceful shutdown.
- Resumed committed-window counters **{"health-0": 9, "health-1": 7}** and histogram counts/sums match each process's accepted commit logs. There is no fabricated zero-duration histogram observation.
- On restart before local decisions, both new slot guards are zero and the guarded source query returns no series. A separate actual SDK/OTLP transport regression re-exported archived typed data, then restarted the provider: retained ELIGIBLE series were excluded by the new guard and the process counter reset to zero. That probe is not a new business decision.
- Software checks: **453 regression tests**, **23 schemas / 17 samples / two policy configurations**; installed-wheel checks exercise the metric provider outside the checkout. These checks are distinct from native runtime evidence.

## Retained failure and correction

An earlier attempt at `3944a4a` was deliberately stopped after a read-only Prometheus query demonstrated an operational bug. After the primary crash, a fresh process heartbeat and ready=1 coexisted with the earlier process's cached source-availability, bucket-age and counter values before any new local commit. Six accepted buckets/eighteen signals, raw artifacts, logs and the observation were retained; that attempt has no native PASS result.

The fix adds an always-observed local-decision guard, explicit known-zero process counters and a last-accepted bucket timestamp. Source/age and retained histograms require the local-decision guard; source views additionally require nonfuture bounded age and a fresh ready heartbeat. Labels remain bounded environment/replica/service/component values. No PID, stream or incident IDs are introduced as metric attributes. Private providers, explicit credential-free local endpoints, header/resource allowlists and disabled gRPC proxying keep metrics separate from the business evidence path.

## Scope and remaining gates

This proves the local health worker metric path within the measured envelope. It does not deploy Grafana, implement every module's own metrics, accept full NFR-10/G4, train/activate a model or authorize actions. Repeated captures on one testbed are not independent trials. The frozen GAMMA/RCAEval source roles, pre-SLO target, heldout boundaries and default unavailable inference behavior are unchanged. Exact-head remote CI plus transported native/metrics artifacts must be verified separately before closing the release; local results do not stand in for CI.
