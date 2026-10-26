# ADR-011 — Frozen GAMMA / RCAEval data contract

**Status:** Accepted

**Date:** 2026-10-26

**Authorization:** Owner approved the selected pair, requested a contract, and resumed the delivery goal.

**Supersedes:** ADR-010's source selection only. Its provenance, model roles and live-transfer requirements remain current.

## Decision

The normative configuration is [training_data_contract.json](../../configs/training_data_contract.json), governed by [training-data-policy.schema.json](../../contracts/training-data-policy.schema.json). CI validates this actual configuration, not a separate example.

- **GAMMA version 1**, Kaggle version ID **7638732**, is the primary training source. Publisher: `gagansomashekar/microservices-bottleneck-detection-dataset`; declared license Apache-2.0. Retain version, inventory, exact lengths and SHA256. Locally frozen hashes and ZIP CRC are not independent publisher-signed checksums.
- **RCAEval**, `phamquiluan/RCAEval` at Hugging Face revision `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`, is the second and final public source: RCA development/validation and separately frozen heldout evaluation. It does not fit ML models, preprocessors or thresholds. Cases used to tune RCA rules are not untouched final RCA tests.
- Do not add a third source, substitute sources after scoring, concatenate the two sources' rows or manufacture cross-system joins. Preserve native service identities. Cross-source ML evaluation requires actually equivalent features and is reported separately.
- SMD's existing acquisition, IF/MAD experiment and reports remain reproducible **historical research**, excluded from v1 training. Do not expand SMD or select it after seeing GAMMA results. Local captures remain integration/source-parity/transfer evidence, not a replacement primary training corpus.

## Processing and evaluation invariants

1. Acquire data and metadata only. Never execute provider scripts/notebooks or adopt preprocessing that leaks future observations. Raw data stays immutable and outside Git.
2. Reconstruct causal service/time observations. GAMMA's preview contains RPC latencies/start times, repeated resource counters and `*_label_RPC` / `label_trace`. A trace row is not an independent service/time sample. Cumulative CPU/network counters require reset-aware time deltas, not percentage renaming. Verify RPC/node/service mappings before feature construction.
3. Sort by observed time, not file order. Attached metrics are not available before their source observation. Preserve sampling uncertainty, gaps, empty logs, incomplete graphs and reset/alignment failures. No backward fill or missing-to-healthy conversion.
4. Declare session/experiment groups before windowing. All modalities, request types, graph variants, derived copies and overlapping captures from the same session share one split. Freeze a deterministic grouped development/validation/test partition and temporal purge where required. Report independent run counts, not only trace/window counts.
5. IF fits admitted healthy training windows only. Preprocessing is train-only; calibration uses designated validation. Compare IF/MAD under the same false-alert/persistence protocol. Final test never selects features, models or thresholds; no point adjustment.
6. Source-specific research features remain separately versioned. RPC latency is not automatically HTTP server p95; sampled traces are not a verified request denominator; gRPC status is not HTTP status. Keep current `http-red-5m/v1` features and the product target intact. Public models need verified compatible live adapters before activation.
7. IF remains anomaly detection; XGBoost remains **pre-SLO gradual-degradation risk**, not current bottleneck classification. Fault timing, args, phase schedules, filenames, case IDs and truth fields stay out of model inputs. Warmup and named phases do not alone prove gradual precursors.
8. XGBoost fitting requires independently measured SLO onset, admitted gradual pre-breach intervals, healthy/benign negatives and independent development runs. Keep no-breach/censored cases; freeze evidence-backed label/SLO definitions before comparison. Missing evidence means `not-ready`; do not invent labels, lower the target or replace a source.
9. Public artifacts initially have `activation_allowed=false`. Offline fitting does not pass live G2/G3/G7, approve remediation or activate `/v1/detect`.

## Change control

The pair and these boundaries are frozen for v1. Implementation fixes may correct code to satisfy this decision. Failed data/model gates are reported honestly, without source replacement, target changes or final-test tuning. Changing a frozen decision requires an explicit owner scope change and superseding ADR/contract version.

Selection is accepted; readiness and measured quality are separate evidence claims. This decision does not certify that the sources provide all five live HTTP features, enough gradual labels or a model meeting the targets. A failed gate cannot be hidden through capability removal or fabricated model success.

References: [GAMMA data](https://www.kaggle.com/datasets/gagansomashekar/microservices-bottleneck-detection-dataset), [GAMMA paper](https://www3.cs.stonybrook.edu/~anshul/www24_gamma.pdf), [RCAEval](https://huggingface.co/datasets/phamquiluan/RCAEval).
