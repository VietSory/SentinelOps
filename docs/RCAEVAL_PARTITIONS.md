# RCAEval frozen partitions and source access

[ADR-011](adr/ADR-011-frozen-gamma-rcaeval-contract.md) remains authoritative: RCAEval is RCA development/validation/heldout evaluation only, never ML/preprocessor/threshold fitting. [The committed inventory](../configs/rcaeval_partitions.json) and [semantic validator](../src/sentinelops/public_data/rcaeval_partitions.py) now freeze all 735 cases before additional diagnostic development. [The measured report](reports/2026-10-28-rcaeval-partitions.md) records the actual pinned-index audit; no new case telemetry was acquired for this step.

## Group before assigning a role

The pinned publisher index contains 115 cross-system overlapping capture-interval pairs. Splitting just by system would separate overlapping captures. The v1 conservative grouping joins:

1. Every pair of reported capture intervals that overlap or are separated by at most 600 seconds, across all three systems. This is a conservative buffer covering a 300-second history plus a 300-second evidence window; it is not a measured availability or lead-time claim.
2. All repetitions and suites sharing the exact native `(system, root_cause_service, fault)` family. Root/fault metadata is used only by this evaluator partition builder, never passed to diagnostic scoring.
3. The transitive closure of both relations. Every modality, derived copy and window of a case inherits its group and role.

Group ID is SHA256 of the canonical sorted case-ID list. The three previously audited cases and their connected closure stay development. Remaining groups are ordered by SHA256 of canonical `{"seed":42,"group_id":...}`; first two become sealed holdout, next two validation, others development. This ordering was frozen without localization scores or model comparison. No row-random split or rebalancing after seeing scores is permitted.

| Role | Groups | Cases | Native systems |
| --- | ---: | ---: | --- |
| Development | 12 | 716 | OB 226, SS 245, TT 245 |
| Validation | 2 | 8 | OB only |
| Sealed holdout | 2 | 11 | OB only |

The resulting imbalance is retained. Validation/holdout support is small and Online-Boutique-only; it cannot support broad cross-system generalization. These are conservative metadata groups, **not proven independent sessions/trials**. Publisher metadata does not provide the capture/session identities needed for that claim. Lack of interval overlap does not establish independence. If later provenance connects groups across roles, stop evaluation and record contamination; do not quietly reassign cases after seeing outcomes.

Two development cases have injection timestamps outside their reported capture range: `re1ob_currencyservice_loss_1` and `re1ob_productcatalogservice_cpu_3`. They remain in the inventory and grouping, explicitly quarantined for injection-time-dependent evaluation. Do not repair timestamps by guessing, derive SLO/precursor labels from injection, or remove them silently from denominators.

## Access and reproduction

`download_rcaeval` checks the frozen source and role before publisher requests or raw-cache access. The existing manifest reader also rejects mixed/heldout selections before reading any raw file. Default acquisition of the three previously audited development cases still works. Unknown cases, changed assignments/seed/open flags and mixed development/holdout requests fail closed. Acquisition manifests record partition identity and selected roles.

This is an application safeguard, not an operating-system access boundary against arbitrary external downloads or code. Every new raw consumer must use the same admission check. There is no holdout-unlock flag; final evaluation needs its separately frozen P11 protocol and authorization. Do not tune RCA rules on the final holdout. Existing validation is evaluation-only, not permission for model or threshold fitting.

Reproduce from an existing publisher-verified acquisition manifest containing the index:

```bash
.venv-ml/bin/python -m scripts.audit_rcaeval_partitions \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --output artifacts/evaluations/rcaeval-partitions-new/report.json
```

The CLI requires committed code/config, verifies the immutable manifest and exact publisher-verified index bytes, recomputes the complete assignments, and creates an exclusive output. It reads index metadata only; no telemetry file, root-cause text or injection-time file is opened. The full index, including grouping labels, was inspected; only per-case telemetry remains unopened outside the original three-case audit.

The public-data CI job repeats this audit against fresh pinned acquisition and retains `rcaeval-partitions.json` in `public-data-source-audit`. Local/software tests and remote CI remain separate evidence. The separate [native development protocol](RCAEVAL_NATIVE.md) now establishes exact metric/log/trace clocks, observed native parent links and bounded research-only support extraction for those same three cases; [actual execution](reports/2026-10-29-rcaeval-native-development.md) yields three abstentions and no qualified predictions. Receipt-clock availability, operational source-compatible RCA, localization quality, ablations and final P11 evaluation remain open. Missing modalities remain missing. This freeze does not pass full G4, G3/G7 or model activation.
