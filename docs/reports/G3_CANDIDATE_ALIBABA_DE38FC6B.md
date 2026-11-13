# G3 review packet — Alibaba candidate `de38fc6b` (ADR-019)

**Status:** G3 approved with conditions by the owner on 2026-11-13 ([D-27](../architecture/CONSTRAINTS_AND_GATES.md)). Layer 2 (epoch `ald`) is capturing and will be evaluated once; that result is the official quality evidence.
Nothing here activates a model, opens a holdout or claims AWS.

| Item | Value |
| --- | --- |
| model_version | `de38fc6bb89896856556e13e7c6142ba21c8d0fea88ebd082f95e96d4e5b0e57` |
| manifest SHA-256 | `d52df5846ac5027507d96b67c751f02f2c5519e3ea970fbdc3599207d18eb84e` |
| Fit / controls commit | `3198e70` (clean checkout); fit report `58a30c25…`, controls `50bf3d38…` |
| Feature set | `http-relative-5m/v2` ([ADR-019](../adr/ADR-019-breach-relative-retrain.md)) |
| Data | Alibaba v2022 MSRTMCR, 240 checksummed archives; splits by microservice: train 6,184 / calibration 2,092 / evaluation 2,128 services |
| Thresholds | IF 0.7131 and MAD 9.006 (1 % on calibration healthy); risk `watch` 0.485 (K = 64), `degrading` 0.665 (K = 16), 1 % on calibration negatives |
| Replaces | `badf9da0` (D-22), whose layer 2 failed Q-04/Q-05/Q-07 |

## 1. Synthetic fault-shape gate (pre-capture, frozen in the config)

180 deterministic sequences (healthy, abrupt +1200 ms, 20-minute ramp to +1200 ms; base latency 3/8/24 ms;
noise σ 0.04/0.10) through the real transform, persistence and event matching. No local capture is read.

| Check | Result |
| --- | --- |
| IF abrupt events | 60 / 60, 0 false alerts, delay 150 s; minimum margin above threshold +0.021 |
| MAD control | 60 / 60, 0 false alerts (IF F1 not below MAD) |
| Risk AP on ramps (slope control) | 0.995 (0.720) |
| Precursor warnings with lead ≥ 60 s | 60 / 60, minimum lead 210 s, 0 warnings on healthy minutes |

## 2. Layer 1 — unseen Alibaba microservices

| Metric | `de38fc6b` | `badf9da0` |
| --- | --- | --- |
| IF event precision / recall / F1 | 0.259 / 0.476 / **0.336** | 0.256 / 0.347 / 0.295 |
| IF false incidents per healthy service-hour | 0.447 | 0.332 |
| MAD control F1 | 0.300 | 0.286 |
| IF − MAD F1, 95 % paired service bootstrap | **[+0.028, +0.045]** | not significant |

Risk on the evaluation services, at each breach multiplier K (labels: future minutes above K × window baseline):

| K | AP | Prevalence | Recall at `degrading` | Alert rate on negatives |
| --- | --- | --- | --- | --- |
| 2 | 0.026 | 0.011 | 0.57 | 0.37 |
| 16 (calibrated) | 0.0079 | 0.0017 | 0.14 | 0.011 |
| 64 | 0.010 | 0.0005 | 0.15 | 0.004 |

Controls at K = 16: XGBoost AP 0.0079 vs slope 0.0063; XGB − slope AP 95 % interval **[−0.0018, +0.0037]**
(not significant); precursor recall with lead ≥ 60 s 0.43 vs 0.23 on 286 events, at alert rates 1.1 % vs 3.8 %.

Reading: production breaches in Alibaba mostly arrive without a smooth precursor, so forecasting them stays
weak for any model. The ADR-019 risk model is built for a latency trend toward an absolute SLO. Against the
`badf9da0` model it trades ranking of 2×-baseline production spikes (AP 0.026 vs 0.070) for a target that
reaches the SLO. This is reported, not tuned: no number above changed any threshold or weight.

## 3. Development checks on the evaluated epoch `alb` (not acceptance)

The owner allowed `alb` as development data (ADR-019 decision 5). It is read-only; nothing was derived from it.
Three checks were run, all on fits from the frozen design: the first development fit, the official fit at
`8784b08`, and this fit at `3198e70` after the review fixes. Results of the last one:

| Service | IF TP/FP/FN (latency) | XGB AP / slope AP | Precursor ≥ 60 s | 5xx by SLO rule |
| --- | --- | --- | --- | --- |
| checkout | 6 / 0 / 0 | 0.955 / 0.763 | 10 / 10 | 4 / 0 / 0 |
| payment | 3 / 0 / 0 | 0.946 / 0.825 | 5 / 5 | 2 / 0 / 0 |
| inventory | 3 / 0 / 0 | 0.947 / 0.785 | 5 / 5 | 2 / 0 / 0 |

Detection delay 150 s on every latency event; 0 false incidents. On `alb`, `badf9da0` scored IF 1/12 and
precursor 0/20.

## 4. Limitations

- Development evidence. Acceptance for Q-04–Q-07 comes from the single `ald` evaluation; `alb` numbers do not count.
- Local scope is S1/S3 (abrupt latency/error, gradual latency) on three services; S2/S4 are not claimed.
- Features use mean latency while the SLO uses p95; leads are measured, not assumed.
- 5xx detection is the objective SLO rule; Alibaba has no error field.
- Independent review: Codex reviewed `8784b08` (4 findings, fixed in `3198e70`/`202650d`) and re-reviews the fixes.

## 5. Decision requested

G3 with conditions for local integration and the AWS deployment path, as for D-22: lane B may build a release
from this bundle after its own parity PASS; the official quality result is the `ald` evaluation.
