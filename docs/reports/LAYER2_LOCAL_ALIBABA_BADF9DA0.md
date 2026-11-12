# Layer 2 — local v2 epoch for Alibaba model `badf9da0` (2026-11-12)

This is the one-time local evaluation of the frozen Alibaba bundle (`badf9da0`,
manifest `2d1a5376…`, D-22). The local HTTP services are not used for fitting:
`fitting_rows=0`, no threshold changes, holdout not opened, activation not allowed.
Development evidence, not a G8 claim.

| Item | Value |
| --- | --- |
| Report | `artifact://manifests/71904d41…2e73b63.json` ([copy](evidence/layer2-alibaba-badf9da0.json)) |
| Epoch | `artifact://manifests/31ce5992…4967e50.json`, dataset `artifact://manifests/5ea60b39…7c35ec.json` |
| Groups | 5 whole groups × 5 slots (healthy, payment/inventory abrupt, payment/inventory gradual), 25 verified runs: `alb-01`, `02`, `03`, `06`, `05` |
| Excluded | `alb-04`, whole group, by owner decision: the host slept 7.8 h during the inventory-abrupt warmup ([ADR-016](../adr/ADR-016-public-production-training-source.md#owner-exclusion-of-an-incident-group-2026-11-11)). The exclusion was recorded before any score existed. Replacement `alb-06` was scheduled per plan |
| Code | capture `3b17205` (groups 01–03) and descendant `8379412` (groups 05–06, exclusion code only); evaluation at `8379412` |
| Scoring | profile `prometheus-increase-60s-5s/v2`, features `http-relative-5m/v1`, event deadline 300 s, forecast horizon 300 s, recorded source-availability clock |

## Results

Latency events (IF vs bundled MAD control, same features and persistence):

| Service | Events | IF TP / FP | IF F1 | MAD TP / FP | MAD F1 | IF−MAD F1, 95% group bootstrap |
| --- | --- | --- | --- | --- | --- | --- |
| checkout | 6 | 0 / 0 | 0.00 | 6 / 6 (3.4 per healthy h) | 0.67 | [−0.67, −0.67] |
| inventory | 3 | 0 / 0 | 0.00 | 3 / 0 | 1.00 | [−1.00, −1.00] |
| payment | 3 | 1 / 0 | 0.50 | 3 / 0 | 1.00 | [−1.00, 0.00] |

HTTP 5xx events: the objective SLO rule and the online error MAD detect every event
(checkout 4/4, inventory 2/2, payment 2/2) with 0 false incidents. Mean delay is
180–210 s, inside the 300 s deadline.

Gradual risk (XGBoost vs fixed relative-slope control):

| Service | Prevalence | XGB AP | Slope AP | XGB precursor recall (lead ≥ 60 s) | Slope precursor recall |
| --- | --- | --- | --- | --- | --- |
| checkout | 0.435 | 0.503 | 0.763 | 0/10 | 10/10 |
| inventory | 0.163 | 0.531 | 0.785 | 0/5 | 5/5 |
| payment | 0.162 | 0.427 | 0.825 | 0/5 | 5/5 |

## Gate reading (development)

| Gate | Result |
| --- | --- |
| Q-04 IF event recall ≥ 0.85, precision ≥ 0.80, ≤ 1 false incident/service-hour | **FAIL**: IF recall 1/12; 0 false incidents |
| Q-05 IF F1 ≥ MAD at the same budget | **FAIL**: IF below MAD on every service; the interval excludes 0 for checkout and inventory |
| Q-06 MTTD p90 ≤ 300 s | Combined pipeline (SLO rule + MAD): every detected event within 210 s. IF alone: one detection, 150 s |
| Q-07 XGB AP above prevalence and slope control; precursor recall ≥ 0.70 | **FAIL**: AP is above prevalence but below the slope control; precursor recall 0 |
| Q-08 RCA | Not measured here; see [RCA development](RCA_DEVELOPMENT.md) |

## What this means

The Alibaba-trained detectors do not transfer to the local injected faults at
their frozen operating points. IF stays silent: 0 false incidents, but it misses
11 of 12 latency events. XGBoost ranks windows better than chance, but it never
crosses its watch threshold before a breach. The simple controls shipped in the
same bundle (MAD, slope) and the SLO rule catch every event. This matches the
layer-1 limitation "scenario shift" in the [model card](MODEL_CARD_ALIBABA_BADF9DA0.md).

## Why the detectors miss (diagnosis, 2026-11-12)

The diagnosis re-scores the recorded prediction table
(`artifact://manifests/24e6caae…`) with the frozen bundle. It is not used to change
this candidate.

- **IF saturates at its own threshold.** On healthy local windows, IF raw scores are
  0.32–0.42. On windows with a latency increase (relative level ≥ 0.5) they range
  0.52–0.65, and 605 of 665 sit on a plateau of 0.60–0.65. Isolation path length
  stops shrinking once a point lies outside the training range. The frozen threshold 0.636 (Alibaba calibration,
  1% budget) lies inside that plateau, so fault windows cross it only now and then.
  In the 12 runs that carry a latency event, IF flags at most one consecutive
  decision in 10 runs and none in 1. Persistence needs 2 (`entry_windows`), so all
  11 are misses. The one detected run had 3 consecutive flags. MAD flags 4–15
  consecutive decisions in every one of these runs. A probe from a healthy median
  window shows the ceiling: raising level and peak from 1 to 6.8 leaves the IF score
  at 0.526.
- **XGBoost is below its alert threshold on the scored windows.** On the 632
  windows with a gradual-risk label, scores range 0.29–0.79: positives 0.772–0.788,
  while negatives reach the same 0.779 at their 90th percentile. The frozen alert
  threshold is 0.8 (`risk_threshold` in `configs/local_model_evaluation.json`, the
  bundle's `degrading` level), so no labelled window alerts. Unlabelled windows reach
  0.88; 39 of them cross 0.8, but they are outside the Q-07 denominator. Its ranking is
  above prevalence but below the slope control on every service.
- **MAD and slope work because they are unbounded.** The MAD score is a maximum
  absolute z-score, and the slope control is a raw slope. Both keep growing with the
  fault, where the tree scores level off.

Direction for a successor, to be fixed on Alibaba data before any new local epoch:
reject a fit whose IF threshold falls on the score plateau of out-of-range inputs.
Prefer a detector score that stays monotone in relative latency, such as the bundled
MAD or a bounded-feature IF. Calibrate the risk alert threshold on the calibration
split instead of the fixed 0.8. Then evaluate on a fresh local epoch; this epoch's
groups are now development data.

These numbers must not be used to retune thresholds or retrain this candidate:
that would turn the local epoch into training data (ADR-016). A successor candidate
needs its own fit on Alibaba data and a fresh evaluation epoch.

## Limitations

- Five development groups. Group bootstrap is conditional; fresh contexts do not
  prove IID independence.
- Local faults are injected (S1/S3), with a fixed load of about 180 requests/min.
- The ADR-017 registry pins system-v1 to capture commit `3b17205`. Groups 05–06 and
  the evaluation ran at `8379412`. `python -m scripts.system_identity --commit 8379412…`
  differs from the registry only in five evaluator-closure blobs: `local_campaign`,
  `local_corpus`, `local_data`, `local_recovery` (exclusion and epoch-source checks) and
  the new `owner_exclusion`. Service, route, topology, profile, policy and workload
  identities are unchanged. The registry entry is hash-locked and is not edited. A later
  carry-forward decision must treat this run identity as `8379412`, not `3b17205`.
