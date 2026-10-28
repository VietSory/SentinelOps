# Conservative correlation of objective episodes

This P7 producer consumes explicit SLO episode openings before RCA scoring. [correlation-result.schema.json](../contracts/correlation-result.schema.json) carries membership, dependency-path support, alternative structural candidates and immutable source/topology identities. It produces no root-cause score, IER write, action permission or model result.

## Fixed development rule

The configured edge direction is consumer→dependency: Checkout depends on Payment/Inventory; both depend on RDS. The DAG is validated before use. Sort openings by UTC episode onset/service/ID; form chronological clusters whose last-first onset span is at most 120 seconds. A repeated service episode starts a new cluster. Adjacent pairs cannot extend a cluster into an unbounded time chain.

Within a cluster, structural candidates are the active services with no further active dependency. A signal joins a dependency candidate only when exactly one candidate is reachable along the configured paths. Independent Payment/Inventory openings remain separate even when both affect Checkout or share configured RDS. A Checkout signal reachable from both candidates stays in its own ambiguous group, with both alternative paths and candidate-signal refs. An unobserved RDS incident is never invented from topology alone.

Each accepted opening belongs to exactly one group. Future decisions and non-opening/UNKNOWN/healthy observations are counted without creating episodes. Conflicting decisions for one service/onset, invalid topology, source/environment/policy mixing and unsupported components fail explicitly. Grouping is a development hypothesis supported by configured dependency paths and time proximity; contradictory trace/change evidence and independent faults along a dependency chain still require later RCA/ambiguity handling. This rule alone does not establish causality or localization accuracy.

Group IDs bind scope/membership/topology. Logical result IDs are stable under reordering and duplicate retries; redundant-row count is retained as a diagnostic outside that logical ID. Full stored report hashes still bind diagnostics. The strict validator recomputes grouping from episode facts/topology and verifies exact partition/support, so rehashing an invented grouping does not make it admissible.

## Single-scope replay consumer

```bash
.venv-ml/bin/python scripts/replay_slo_correlation.py \
  --health-store artifacts/incidents/slo-health/RUN \
  --report-ref artifact://manifests/HEALTH_REPORT_HASH.json \
  --output artifacts/incidents/correlation/RUN
```

Commit implementation first; output is exclusive and content-addressed. The consumer reads one [health replay](STEP7_SLO_HEALTH.md) scope, checks object hashes, policy/count/final-state consistency and every service's prior-ID/60-second/cutoff chain. It never concatenates independent runs. The health report hash is the explicit source scope in all groups. Upstream raw normalization parity belongs to the health producer; content hashes are integrity evidence, not producer authentication or automatic action authority.

`source_identity` contains environment, source profile and health-policy hash. The result includes the topology snapshot and opening facts needed to resolve dependency-support IDs. Missing-model values are not filled with anomaly scores or risk zero. The existing detection endpoint continues to fail closed without a compatible model.

Mode is explicitly `event-time-analytical`: opening/group history from replay clocks, not live emitted incidents. [The downstream RCA producer](STEP7_RCA.md) now derives bounded trace/log/connection support and persists computed analytical IERs with a distinct actual emission intent. Continuous worker/checkpoint concurrency, observed deployment support and RCAEval development/holdout evaluation remain separate deliverables. Docker CI consumes freshly captured Step 4 signals and checks a Payment/Checkout dependency-supported group; unit adversarial cases cover independent roots and ambiguous shared consumers. These checks do not close full G4 or the unchanged model/data gates.
