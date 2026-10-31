# GAMMA complete raw-resource cohort acquisition

This is completed acquisition/content verification of the [predeclared raw-resource scope](../archive/guides/GAMMA_RESOURCE_COHORT.md), not complete acquisition of every GAMMA modality and not a training/holdout acceptance report. The frozen GAMMA v1 / pinned RCAEval policy and original pre-SLO gradual target remain unchanged.

## Actual source acquisition

Clean commit `03cc854eebf5e31afbe966fa9e84bf178f1c3e05` acquired **all 194 raw directories containing args**, including four without processed tables. Selection was declared before extracting the cohort and used no model outcomes. The first high-volume attempt stopped on a non-206 response and produced no success manifest. The final transport resolves the canonical redirect once and reuses the successful signed object lease only in memory, preserving exact Range/representation validation and preventing a canonical API request for every member range.

| Original role | Native files | Expanded bytes |
| --- | ---: | ---: |
| Four raw resource metrics | 23,048 | 68,196,044 |
| Per-experiment pod descriptions | 6,484 | 18,334,720 |
| Native phase logs | 634 | 194,033 |
| Experiment args | 194 | 105,144 |
| Global placement/RPC metadata | 4 | 3,787 |
| Total | **30,364** | **86,833,728** |

The successful acquisition used **653 archive range responses and 66,794,361 response-body bytes**, including complete archive metadata and coalescing overhead. These counts describe the successful execution, exclude the earlier unsuccessful attempt and HTTP protocol overhead, and are not a billing estimate.

Original member bytes are stored in **195 deterministic project ZIP shards**, totaling 95,043,574 stored bytes. Shard packaging is new; source member contents and paths are preserved exactly. Every member passed local-header/filename, expanded-length and original ZIP CRC verification before its SHA256 entered the source tree. Global metadata remains provenance, not an assumed placement for every later deployment.

- Acquisition manifest: `data/raw/public/manifests/190c2cc83635d67f883e48392216ae18ef0fbb011bbf8dda8f320a6a86fb6497.json`.
- Scope SHA256: `82caad8e694015112e14173777da292d780f5136264de2719af0940c03221b42`.
- Complete source-file-tree SHA256: `ee11cd27de2451194fbe9df68365cbe6c67b0982d3b48f9dd0fb74b49374c3c6`.
- The [small committed file-tree lock](../../configs/gamma_resource_cohort_files.json) anchors all 30,364 path/role/size/SHA256 records; the [measured evidence](evidence/2026-10-26-gamma-resource-cohort-acquisition.json) records counts and limitations.

An independent manifest reader re-opened and verified **every shard and member**, exact catalogue/scope coverage and source-file-tree identity. All **128 files shared with the earlier 142-file phase audit retained their exact source size/SHA256**, providing cross-transport consistency with the individually acquired publisher files. Locally frozen hashes and ZIP CRC are not publisher-supplied cryptographic checksums.

## Known admission work

Acquisition preserves incomplete cases. Two directories have no selected resource series, one has no pod-description file and twelve have no native phase file. These are file-presence counts, not semantic validity counts; empty/invalid files may require additional exclusions. An absent phase log never implies a healthy experiment.

Four cases were inspected for native format development and recorded in [holdout exclusions](../../configs/gamma_development_inspections.json). Their entire connected session groups must stay outside the final untouched holdout. No model was fitted or scored during this inspection.

Observed examples require careful semantic handling:

- The inspected `mem_sep22_10min_800_0` logs start intervals with kind `memory` but end them with kind `CPU`. The initial strict phase parser rejects that mismatch. Source bytes are preserved; no silent kind substitution or fabricated event has been applied.
- That case's pod start times are dated September 25 despite its `sep22` directory name. Source filenames are not authoritative capture dates or proof of independent sessions.
- The inspected `net_oct4_10min_800_0` includes memory-prefixed phase logs. Resolve intervention targets/types from verified native records and declared configuration; do not infer them solely from its directory prefix.
- Mixed CPU/memory experiments declare separate target lists and phase files. Per-experiment pod labels/hosting nodes must be verified; multiple pod descriptions cannot be silently collapsed into the global placement map.

Next: verify per-case raw series, native placement, event-log consistency and observed capture/session grouping; freeze independent splits before windowing/model comparison. Preserve source-reference uncertainty and original warmup/coverage rules. Current intervention labels still do not supply independently measured future SLO/gradual-degradation truth, live HTTP denominators, online arrival times or automatic activation permission. G2/G3/G7 remain open.

## Verification

The final full suite passed **256 tests**, including transport concurrency reservations, deterministic source-preserving shards, frozen-tree replay, incomplete-case retention, corrupted bundle/manifest detection, summary/readiness integrity and exact scope enforcement. The final installed-wheel cohort/module/schema/API smoke passed outside the checkout. The tightened reader independently verified all 195 real shards again against the committed tree values. CI acquires this entire cohort against the project tree lock, independently reads every shard, checks the 128 earlier hashes, and archives the full member manifest alongside the capability feature/label replay. Successful content verification does not certify usable labels or model readiness.
