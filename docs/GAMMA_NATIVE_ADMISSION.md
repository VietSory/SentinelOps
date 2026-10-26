# GAMMA native admission and conservative source groups

This is the semantic admission slice after complete [raw-resource acquisition](GAMMA_RESOURCE_COHORT.md). It prepares inputs for source-specific dataset/split construction under the frozen GAMMA/RCAEval policy. It does not fit a model, assign final splits or change the HTTP feature registry, resource window profile or XGBoost target.

## Native identity and intervention evidence

The versioned [admission profile](../configs/gamma_admission_profile.json) is fixed before whole-cohort execution. Read every case from the verified source-file-tree manifest and immutable project shards. Global placement and processed RPC/resource columns are not substituted for per-experiment observations.

- Parse one complete pod description, its `social-network` namespace, unique `app-name` label, DNS-compatible Kubernetes `Node`, status and timezone-aware start time. Node identity is distinct from the interference tool's restricted `uservN` target IDs: the observed excluded Jaeger node is named `userv17-jaeger`. Do not derive service identity from the pod filename or remove guessed deployment/hash suffixes. Unparseable pod descriptions quarantine the case; source files remain unchanged.
- An entity needs four nonempty, finite, strictly time-increasing Unix-second series. Every observed pod used for that entity must be Running and agree on its VM; at least one observed pod must have started by the resource interval. Multiple observed VMs, missing metrics/placement and monitoring-only Jaeger are excluded with reasons. Snapshot observations still do not prove continuous placement or telemetry arrival time.
- Flat args use `bottlenecked_nodes` and unprefixed native VM phase filenames. Mixed args use separate CPU/memory/network/I/O VM lists and corresponding typed filenames. Optional null lists mean no declared target for that kind, not healthy samples. Mixed per-kind lists determine expected files instead of a global default `bottleneck_type` value.
- Actual phase file names must exactly cover declared VM/kind targets. The existing strict parser pairs the recorded kind and measure, requires complete positive intervals and rejects contradictions/unclosed records. No silent `CPU`→`memory` end-record repair, renamed network/memory source, imputed event or target inferred from the experiment prefix is applied.
- Complete events must lie within observed raw capture bounds with a one-second boundary guard. Raw capture spans beyond the fixed two-hour sanity bound are untrusted/quarantined, not truncated. That bound is a corruption check, not a new feature/SLO duration.

All cases are accounted for, including incomplete ones. A case is `native-evidence-admitted` only when its case-level checks pass and at least one native entity is supported. Per-entity exclusions remain visible. Quarantined cases emit no partial event list that could turn an unknown interval into a no-recorded-interference reference. Native declared warmup is recorded; values below 300 seconds warn but **do not lower the unchanged resource profile's 300-second warmup**.

Admission checks structural/identity/event evidence. It does not certify operational health, feature-window quality, recovery, an SLO violation, a gradual precursor or an independent experimental replication.

## Group constraints before splitting

Conservative grouping unions cases linked by either:

1. Any common **observed UTC day** after expanding their capture bounds by a fixed 300-second purge on both sides, including midnight crossings.
2. A common directory replicate-family prefix (remove only a final underscore/numeric replicate index). This adds a conservative coupling constraint; it does not infer dates, event kind or statistical independence from the filename. An unindexed case is not guessed into another family.

Union is transitive and order independent. A case with missing/untrusted clock can still couple to its replicate family, but prevents that group's eligibility for final holdout. Previously [development-inspected cases](../configs/gamma_development_inspections.json) exclude their **entire connected group** from final holdout. Groups with no admitted cases are not counted as candidate final-holdout groups. IDs include rules, sorted members, observed clocks and source-shard identities; data/source changes cannot silently reuse that identity.

These are constraints for a subsequent explicit train/calibration/development/holdout freeze. They do not themselves assign any split or establish independence. Feature/reference sufficiency must be measured before fitting, and all exclusions/denominators must remain visible in later evaluation.

## Execution and evidence

Commit code/rules before producing source evidence; the CLI rejects dirty checkouts. Full source/manifest/member integrity is verified before semantic parsing. Outputs are exclusive and content addressed:

```bash
.venv-ml/bin/python scripts/audit_gamma_cohort.py \
  --manifest data/raw/public/manifests/COHORT_HASH.json \
  --output artifacts/research/gamma-native-admission/RUN_ID
```

The report records clean source commit, source manifest/file-tree/policy identity, admission profile and inspection-record hashes; per-case reasons/entities/events; conservative groups and holdout exclusions. `training_ready=false`, `activation_allowed=false` and `grouping_is_independence_proof=false` remain explicit. Tests cover native label identity, invalid/missing/contradictory source, simultaneous typed events, capture clocks, ambiguous hosting, grouping/purge/order invariance, inspected-group exclusion and immutable verified-manifest execution. CI repeats admission on the complete freshly downloaded frozen cohort.
