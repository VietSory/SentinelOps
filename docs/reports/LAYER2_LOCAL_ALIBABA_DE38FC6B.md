# Layer 2 — local epoch `ald` for Alibaba model `de38fc6b` (2026-11-14)

This is the single local evaluation of the frozen ADR-019 bundle (`de38fc6b`, manifest `d52df584…`). The bundle
was pinned in the plan before capture. The local HTTP services did not contribute to fitting: `fitting_rows=0`,
no threshold changes, holdout not opened, activation not allowed. It is the official layer-2 result for this
bundle and **development evidence** (`split: development-validation`): Q-04…Q-07 final claims still come only
from the G8 heldout evaluation.

The evidence manifest records `G3_accepted: false` and `activation_allowed: false` by design: the evaluator never
grants G3. The owner's conditional G3 is a separate decision, [D-27](../architecture/CONSTRAINTS_AND_GATES.md).

| Item | Value |
| --- | --- |
| Report | `artifact://manifests/99fad36a…fe0f894.json` ([copy](evidence/layer2-alibaba-de38fc6b.json)); predictions `artifact://manifests/79f70d8a…10173ce.json` (store only) |
| Epoch | `artifact://manifests/4575b528…0062dc41f.json`, dataset `artifact://manifests/41345067…3388837.json` |
| Groups | 5 whole groups × 5 slots (healthy, payment/inventory abrupt, payment/inventory gradual), 25 verified runs: `ald-01`–`ald-05`. No group stopped and none was excluded |
| Code | capture and evaluation at `202650d` (clean checkout), system version `system-v3` |
| Wall clock | capture 2026-11-13 01:13 → 2026-11-14 00:00 local (scored windows 2026-11-12T18:43Z → 2026-11-13T16:55Z); evaluated once at 2026-11-14 00:04 local |
| Scoring | profile `prometheus-increase-60s-5s/v2`, features `http-relative-5m/v2`, event deadline 300 s, forecast horizon 300 s, clock `recorded-source-availability-counterfactual`, risk threshold `degrading` 0.665 from the bundle |

## Results

Latency events (IF vs bundled MAD control, same features, budget and persistence):

| Service | IF TP / FP / FN | IF F1 | MAD TP / FP / FN | MAD F1 | IF−MAD F1, 95 % group bootstrap |
| --- | --- | --- | --- | --- | --- |
| checkout | 6 / 0 / 0 | 1.000 | 6 / 0 / 0 | 1.000 | [0, 0] |
| inventory | 3 / 0 / 0 | 1.000 | 3 / 0 / 0 | 1.000 | [0, 0] |
| payment | 3 / 0 / 0 | 1.000 | 3 / 0 / 0 | 1.000 | [0, 0] |

IF detection delay 150 s on all 12 events. False incidents: 0 over 6.35 eligible healthy service-hours
(checkout 1.75, inventory 2.30, payment 2.30). The evaluator does not report UNKNOWN portions separately; no
labelled window lacked a prediction.

5xx events (objective SLO rule; Alibaba has no error field, so IF does not score errors): 8 / 0 / 0, delays
150 s (5 events) and 210 s (3 events).

Gradual latency (XGBoost exceedance risk vs the bundled slope control):

| Service | Prevalence | XGB AP | Slope AP | XGB − slope AP, 95 % group bootstrap | XGB precursor, lead ≥ 60 s | Slope precursor |
| --- | --- | --- | --- | --- | --- | --- |
| checkout | 0.431 | 0.950 | 0.835 | [+0.023, +0.219] | 10 / 10 | 10 / 10 |
| inventory | 0.163 | 0.947 | 0.808 | [+0.034, +0.202] | 5 / 5 | 5 / 5 |
| payment | 0.163 | 0.953 | 0.824 | [+0.040, +0.201] | 5 / 5 | 5 / 5 |

All 10 gradual runs breached the SLO (no-breach runs: 0 of 10). Over the 20 eligible service-run precursor events
(checkout 10, inventory 5, payment 5): missed 0, late 0; lead times minimum
269.96 s, all within 0.1 s of 270 s. That is close to the 300 s forecast horizon, so the lead is bounded by
the protocol and does not show how early the model could warn.

Actual alert rates per window, before persistence, from the predictions table. XGB uses its bundled `degrading`
threshold 0.665; the slope control uses its own fixed threshold 0.05:

| Service | XGB flag rate on negatives | Slope flag rate on negatives | XGB flags on healthy-run windows |
| --- | --- | --- | --- |
| checkout | 0.36 (106 windows) | 0.61 | 0 / 75 |
| inventory | 0.08 (213) | 0.16 | 0 / 75 |
| payment | 0.08 (213) | 0.32 | 0 / 75 |

Negatives include windows inside abrupt faults and early ramp windows more than 300 s before a breach, which is
why the rates are far above the 1 % budget calibrated on Alibaba. On healthy runs neither model warned.
The AP bootstrap and the flag rates were computed after the evaluation from its own predictions table
(1,000 group resamples, seed 42); they changed no threshold.

## Gate reading (development, targets from [CONSTRAINTS](../architecture/CONSTRAINTS_AND_GATES.md) §8)

| Gate | Development reading on `ald` |
| --- | --- |
| Q-04 recall ≥ 0.85, precision ≥ 0.80, ≤ 1 false incident / service-hour; scope S1–S4 | **Met for S1/S3**: 12/12, precision 1.0, 0 false incidents. S2/S4 not measured, so Q-04 is not claimed in full |
| Q-05 IF F1 not below MAD at the same budget | **Met by equality**: 1.0 vs 1.0 on every service, interval [0, 0]; IF is not better than MAD here |
| Q-06 MTTD p90 ≤ 300 s, arrival-aware/live | **Met in counterfactual replay**: IF p90 150 s (latency), SLO rule p90 210 s (5xx), 0 missed. Live/arrival-aware MTTD with source lag is not measured (G7/G8) |
| Q-07 XGB AP above prevalence and slope; precursor recall ≥ 0.70 with lead ≥ 60 s; heldout gradual runs | **Met on development runs**: AP 0.947–0.953 vs slope 0.808–0.835, interval above 0 on every service; precursor 20/20, 0 no-breach. Not heldout |
| Q-08 RCA | Not measured here; see [RCA development](RCA_DEVELOPMENT.md) |

The `badf9da0` epoch (`alb`) failed Q-04, Q-05 and Q-07 on the same reading
([report](LAYER2_LOCAL_ALIBABA_BADF9DA0.md)). The ADR-019 changes were derived from that failure and from
synthetic shapes, not from `ald`.

## Limitations

- Development groups on one host, not heldout. The group bootstrap is conditional; fresh contexts do not prove
  IID independence.
- Local faults are injected S1/S3 (abrupt and gradual latency, 5xx) on three services at about 180 requests/min.
  S2/S4 are not claimed.
- The `ald` numbers are close to the `alb` development check of the same design
  ([G3 packet](G3_CANDIDATE_ALIBABA_DE38FC6B.md) §3). That is expected (same fault recipe, same frozen bundle)
  and is not independent evidence of transfer to production traffic.
- On Alibaba's own evaluation services (layer 1), XGB is not significantly better than slope at K = 16
  (AP interval [−0.0018, +0.0037]): production breaches mostly come without a smooth precursor.
- Detection times use the recorded source-availability counterfactual clock, not live arrival.
- Risk targets were unavailable on 358 windows (no confirmed future), as designed.
