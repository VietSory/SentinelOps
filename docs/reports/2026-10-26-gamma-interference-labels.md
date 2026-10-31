# GAMMA native interference-label audit

This audit implements the [separate label-store semantics](../archive/guides/GAMMA_INTERFERENCE_LABELS.md) under the frozen GAMMA/RCAEval contract. It does not train a model or certify SLO/precursor targets.

## Acquisition and real execution

Three original VM `_phases` logs were acquired from the same approved GAMMA v1 experiment, `cpu_aug12_25min_200_0`. Their additional downloaded/expanded content totals **1,238 bytes**. All **139 previously locked files retained their exact size and SHA256**. The extended 142-file scope contains 141,632,879 expanded bytes. Its [full project lock](../../configs/gamma_v1_phase_files.json) was successfully replayed against publisher metadata and all cached bytes; no data-file transfer was needed on that replay. Project hashes freeze acquired evidence; they are not independently supplied publisher checksums.

Clean implementation commit `ea346ba8f20d349f8b1206d07f23f418d153c70b` rebuilt resource windows and joined independent native event logs from acquisition manifest `40c2257e2fd328e51719f51f9ca671a7b48c981fc07f5562ae6fdd549f2d492e`. The exact-lock replay later produced manifest `a3f4e22f4b1b099634b781be207f61bf5b6ea1c97b49e13dc472136d4fb62b47`. Acquisition manifests differ in acquisition provenance; source file bytes are identical.

Each of the three declared target VMs has three closed CPU interference intervals: **nine events**, with measures 0.75, 0.99 and 0.65. Actual event times use Unix seconds and fall within the raw capture clock bounds. They are observed start/end records, rather than times reconstructed from declared phase durations.

## Measured results

| Window evidence label | All windows | Feature-eligible windows |
| --- | ---: | ---: |
| Direct recorded interference on the entity's hosting VM | 152 | 92 |
| Interference elsewhere; own-host impact unknown | 399 | 322 |
| No recorded interference over full window and guard | 29 | 23 |
| Boundary uncertain | 0 | 0 |
| Total | 580 | 437 |

The **23 eligible reference rows represent only one shared cutoff**, `2023-08-14T23:51:00Z`, across 23 entities. They are not 23 independent healthy time windows, 23 experiments or evidence of recovery. This single capability experiment cannot establish independent training/calibration/holdout support. Other-host context remains separate rather than being mislabeled healthy. Ineligible resource windows retain their labels but cannot supply numeric training vectors.

Numeric resource identity remains **exactly unchanged** from the pre-label 139-file processing: `e00da06523d3a4dbce22ca6ef4c9f9130818aff8b66232715b07095bbc064cc5`. Resource frame SHA changes to `7b5f2b304760b9b1a5269ba57fb25c087e5b10eebdf1bde3866bf3d029a60249` because rows now reference the extended acquisition. Two independent label executions produce identical label bytes: SHA256 `4d4f4cb0a250c241f54e99436da510c121b3dee5aa71887641db73a42cccfab8`.

The [machine-readable report](evidence/2026-10-26-gamma-interference-labels.json) records raw phase/args/placement hashes, all native events, capture clock bounds, label counts, feature/label identities and clean code commit. The [schema sample](../../samples/research-interference-label.sample.json) is an actual emitted label referring to an actual resource row.

## Verification and next gate

**235 tests passed** in the ML environment; installed-wheel module/schema/API smoke also passed outside the checkout. Tests cover start/end pairing, VM identity, malformed/missing/reversed/ambiguous logs, clock-unit mismatch, union overlap, peer-host context, boundary guard, independent feature storage, row-hash tampering, target admission, cache corruption and phase-only lock extension. Contract validation includes the actual policy instance and measured sample. CI acquires the full 142-file scope and checks real replay, nine events and these exact label counts.

Continue with independent source session/cohort admission and grouped splits, measuring reference sufficiency before fitting IF/MAD. Do not relax warmup/coverage merely to increase counts. A log intervention is not a service anomaly, measured SLO breach, gradual precursor or recovery certificate. XGBoost retains its original target and remains not-ready until that independent evidence exists. Live HTTP parity, arrival times, transfer and G2/G3/G7 acceptance remain open; no source or model target was substituted.
