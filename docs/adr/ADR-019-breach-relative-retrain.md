# ADR-019 — Retrain IF/XGBoost on Alibaba with breach-relative risk and a pre-capture gate

**Status:** Accepted 2026-11-13 — explicit owner approval ("giờ tôi cho phép bạn làm theo kế hoạch chi tiết
đã vạch sẵn"), after the owner rejected ADR-018 and asked for trained models that detect.
**Supersedes:** ADR-016 items 2–4 (feature set, detector inputs, risk target and thresholds) and item 6 only as
stated in decision 5. Training source, splits, layers and every other ADR-016 rule stay.
**Related:** ADR-002, ADR-015, ADR-016, ADR-017, ADR-018 (rejected),
[layer-2 report](../reports/LAYER2_LOCAL_ALIBABA_BADF9DA0.md).

## Context

Layer 2 of bundle `badf9da0` failed Q-04/Q-05/Q-07. Measurements on the bundle itself, with synthetic inputs
and no local data, found four design faults:

1. **The risk label never reached the SLO.** Training labelled a breach as latency above 2× the service's own
   baseline. The local SLO (p95 > 750 ms) sits 30–150× above the local baselines; epoch-2 precursor windows had
   a median latency 68× their baseline. XGBoost never saw a labelled window above 2× and returned the same
   0.77–0.79 for windows that did and did not precede a breach.
2. **The Isolation Forest scored five features at once.** A latency-only fault moves two or three of them, so
   its score levels off at 0.52–0.63 below the 0.636 threshold for any fault size.
3. **The risk threshold 0.8 was fixed in the protocol,** not calibrated on Alibaba data.
4. **Nothing checked, before a 25-hour capture, that the models could respond to the injected fault shapes.**

## Decision

1. **Feature set `http-relative-5m/v2`.** The five v1 features are unchanged. A sixth,
   `rt_log_baseline_to_breach = log(baseline latency / breach latency)`, places the breach level relative to the
   window's baseline: `-log(K)` for a breach at K× baseline in training, `log(baseline / 750 ms)` in serving
   (the frozen SLO, `serving.breach_latency_ms`, tested equal to `slo_health_policy.json`).
2. **Isolation Forest** fits on the latency level and peak only (`detector.features`), on Alibaba train healthy
   windows, with its threshold at the 1 % budget on calibration healthy windows. The MAD control scores the
   same subset with the same budget.
3. **XGBoost exceedance risk.** One model learns "latency exceeds the breach level within 300 s" for breach
   multipliers K ∈ {2, 4, 8, 16, 32, 64}. Its inputs are the level, peak and slope expressed relative to the
   breach level, with increasing monotone constraints, so a window closer to (or rising faster toward) the
   breach can never score lower. Traffic features do not enter the risk model. Negatives are subsampled
   one in ten and reweighted.
4. **Calibrated thresholds.** `degrading` is the 1 % budget on calibration negatives at K = 16, `watch` at
   K = 64. They are stored in the bundle and used by serving, the evaluator and the controls; the protocol's
   0.8 no longer applies to ADR-019 bundles.
5. **Pre-capture gate and one development check.**
   - Every fit runs a frozen synthetic fault-shape gate (`fault_shape_gate` in the config): 180 deterministic
     sequences of healthy, abrupt and ramp faults through the real transform, persistence and event matching.
     A fit that fails it cannot become a bundle.
   - The owner allows the already-evaluated epoch `alb` (layer 2 of `badf9da0`) as **development data**:
     `scripts.score_development_epoch` scores a candidate on it read-only. No threshold, feature or weight is
     derived from it, every check is recorded with its count, and its numbers never count as acceptance.
6. **Confirmation.** A new epoch `ald` (`seed_base` 20261400, plan ADR-019, system version `system-v3`) is
   registered to the new bundle before capture and evaluated once. Its result is the layer-2 evidence.
7. **MAD and slope stay evaluation-only controls** for Q-05/Q-07. They never drive serving alerts.

## Owner decisions recorded with this ADR (2026-11-13)

- Retrain design above; `alb` as development data under decision 5.
- G7 no longer requires SageMaker training: the fit takes minutes on the operator host from checksummed
  Alibaba archives. G7 keeps AWS parity, S3-stored bundles and the AWS action path.
- MAD/slope remain evaluation controls only.

## Development evidence before capture (not acceptance)

| Check | `badf9da0` | ADR-019 development fit |
| --- | --- | --- |
| Synthetic gate: IF abrupt events | 0 / 60 | 60 / 60, 0 false, delay 150 s, margin +0.021 |
| Synthetic gate: risk AP (slope control) | — | 0.995 (0.72); precursor 60 / 60, lead ≥ 210 s, 0 false |
| `alb` IF latency events (1 check) | 1 / 12 | 12 / 12, 0 false incidents, delay 150 s |
| `alb` XGB AP vs slope / precursor ≥ 60 s | below slope / 0 / 20 | 0.97–0.98 vs 0.76–0.83 / 20 / 20 |

On Alibaba calibration the risk model's lift over prevalence is 2.7× (K = 2) to 13× (K = 64): production
breaches mostly come without a smooth precursor. Claims stay scoped to the local fault shapes and to latency.

## Consequences

- Bundles on `http-relative-5m/v1` (including `badf9da0`) no longer load with this code. Lane B keeps its
  current release until it receives the ADR-019 bundle and re-measures parity once.
- Features use mean latency while the SLO uses p95; a warning therefore tends to come later than the p95
  breach would allow. The `alb` check and the `ald` evaluation report lead times as measured.
- Q-04 scope remains S1/S3 on the local epoch; S2/S4 are not claimed.
