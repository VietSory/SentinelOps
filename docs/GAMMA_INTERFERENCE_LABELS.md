# Native GAMMA interference evidence

The frozen GAMMA/RCAEval decision remains unchanged. This slice admits three additional original VM `_phases` files for the existing capability experiment. The [142-file project lock](../configs/gamma_v1_phase_files.json) preserves every hash from the [139-file acquisition](../configs/gamma_v1_audit_files.json); it is an extension of evidence within GAMMA v1, not another dataset or a changed model target.

The source files explicitly record CPU interference start/end times and measures. The parser pairs case-insensitive kind/measure records, requires finite Unix-second times and positive closed intervals, rejects out-of-order/unmatched/duplicate active records, and checks intervals against observed raw resource capture bounds. It does not reconstruct event time from an increase in CPU, from the declared duration schedule, or from processed binary labels.

Placement identifies the publisher-native service's hosting VM. The acquired phase-file VM set must exactly equal the declared `args.txt` interference targets and refer to known placement nodes. Duplicate/missing targets and mismatched capture clocks fail admission. VM logs are self-reported intervention evidence, not independently measured service impact, clock synchronization, recovery or SLO truth.

## Separate label store

Each [supplemental label](../contracts/research-interference-label.schema.json) refers to one immutable resource JSONL row by SHA256. Numeric vectors remain unchanged in their separate store. The join validates acquisition bytes, resource report/frame identity, policy, profile, entity, experiment, unique window identity and row counts. The report records raw phase/args/placement hashes and an event catalog; event IDs include VM identity so identical log contents on different VMs do not collide.

Labels describe the complete five-minute window, using union overlap so simultaneous interventions cannot double-count seconds:

| Label | Meaning |
| --- | --- |
| `direct-recorded-interference` | Positive-duration overlap with a recorded intervention on this entity's own hosting VM; includes partially affected windows and reports overlap seconds |
| `other-host-interference-context` | No own-host overlap but some recorded intervention elsewhere; dependency effects are unknown, so this is not a nominal reference |
| `boundary-uncertain` | No positive-duration overlap, but an event occurs within the fixed one-second boundary guard |
| `no-recorded-interference-reference` | No recorded intervention anywhere over the full window plus boundary guard; this does not certify operational health or post-fault recovery |

The one-second guard expresses boundary uncertainty; it does not assert measured clock error. It uses event logs just outside a window for retrospective label admission, never as a predictor input or an online lead-time claim. Full five-minute overlap is inspected even when the numeric feature profile admits four valid buckets. Source warmup and feature quality rules remain unchanged. Invalid feature windows still receive an evidence label but are counted separately from feature-eligible windows.

No source-reference window is automatically admitted as healthy training data. Independent sessions, grouped development/calibration/holdout partitions and healthy-reference sufficiency remain dataset gates. Current intervention labels cannot replace the frozen **future pre-SLO gradual-degradation** XGBoost target. There is no HTTP error denominator, live parity, automatic activation or model fit in this audit.

## Reproduction

Commit code before producing evidence; both processing CLIs reject dirty checkouts. Acquisition can reuse existing files only after verifying their exact frozen size/SHA256. A corrupt cache fails instead of silently repairing immutable evidence. The optional first-time `--phase-extension` mode permits only additional native phase logs over a complete locked base; ordinary replay requires the exact full lock.

```bash
.venv-ml/bin/python scripts/download_gamma.py \
  --file-lock configs/gamma_v1_phase_files.json --include-phase-evidence \
  --result-file artifacts/research/gamma-phase-acquisition.json
.venv-ml/bin/python scripts/build_gamma_resource_windows.py \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --output artifacts/research/gamma-resource-windows/PHASE_RUN
.venv-ml/bin/python scripts/audit_gamma_phases.py \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --resources artifacts/research/gamma-resource-windows/PHASE_RUN \
  --output artifacts/research/gamma-interference-labels/LABEL_RUN
```

Output directories are exclusive and raw/evidence artifacts stay ignored. The measured report will record actual counts, exclusions and limitations; pipeline success alone does not close training or live gates.
