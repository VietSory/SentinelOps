# G3 review packet — Alibaba bundle `badf9da0…` (ADR-016)

**Owner decision (2026-11-09, explicit answer): option 1 — G3 accepted with conditions**
for local integration H4 → G6 only, with the limitations below recorded (D-22).
The owner also delegated Claude as operator for the remaining nine local G6
trials with this bundle. Source reports keep `G3_accepted=false`; the release
approval record carries the decision.

- model version: `badf9da0b563f4900fa099b11ba9223ee864d414ef3aa9af82a534db962d85e3`
- bundle `sentinelops-model/v3`, origin `admitted-alibaba-v2022`, manifest SHA-256
  `2d1a5376eccdd691da0741b9dc88eecd223d75a85a23fa712e00c1dc53291e62`
- [model card](MODEL_CARD_ALIBABA_BADF9DA0.md); one service-agnostic bundle for all business routes; feature set `http-relative-5m/v1`
- frozen config `configs/alibaba_relative_features.json`, SHA-256 `3e29879a…`
- fit commit `a1820c7`; source: Alibaba `cluster-trace-microservices-v2022`
  MSRTMCR files 0–119 (trace hours 0–6), each archive checksummed at download

## G2 basis (data)

| Split (by `sha256(msname) mod 10`) | Services | Windows | Healthy | Breach positives |
| --- | --- | --- | --- | --- |
| train (0–5) | 6,184 | 2,116,659 | 1,687,465 | 18,130 |
| calibration (6–7) | 2,092 | 716,253 | 574,122 | 5,463 |
| evaluation (8–9) | 2,128 | 725,025 | 576,758 | 6,597 |

Thresholds come from the calibration split at the 1% false-alert budget
(IF 0.636, MAD 91.6).

## Layer 1 — unseen Alibaba microservices (frozen config, run once)

Controls report (`scripts/alibaba_controls.py`, commit `edd5fe9`) reuses the v1
protocol rules: persistence 2/3, risk threshold 0.8, slope control at the
calibration 99th percentile, and a 1,000-replicate paired bootstrap over whole services.

| Target | Result | Status |
| --- | --- | --- |
| IF events | P 0.256, R 0.347, F1 0.295; 0.33 false incidents per healthy service-hour | weak |
| MAD control | P 0.407, R 0.220, F1 0.286; 0.11 per healthy service-hour | — |
| Q-05 IF not below MAD | IF−MAD F1 95% CI [−0.022, +0.039] | point estimate passes; not significant |
| Q-07 AP > prevalence | AP 0.0695 vs 0.0113 | PASS |
| Q-07 AP > slope control | slope AP 0.0215; XGB−slope 95% CI [+0.034, +0.065] | PASS |
| Q-07 precursor recall, lead ≥ 60 s | 0.236 (slope control 0.054); alert rate on negatives 1.87% | FAIL (target ≥ 0.70) |

Q-04 is defined on local S1–S4 scenarios and is measured in layer 2, not here.

## Local development evidence — ADR-015 detector epoch (p95 fallback)

`scripts/evaluate_alibaba_legacy_p95.py` (commit `c2006ee`) scored det-01..05 with
p95 in place of mean latency, because profile v1 has no `_sum`.

| Service | IF TP/FP/FN | MAD TP/FP/FN | IF false incidents per healthy hour |
| --- | --- | --- | --- |
| checkout-service | 0/3/10 | 0/0/10 | 1.75 |
| inventory-service | 0/3/5 | 0/4/5 | 1.32 |
| payment-service | 0/1/5 | 0/4/5 | 0.44 |

The 0 TP is structural. Each fault starts five minutes after its run starts, and
a relative window needs five baseline minutes before its own five minutes. The
first scored window therefore lands at the 300-second event deadline. This
epoch cannot measure detection for this model; layer 2 provides ≥ 30 healthy
minutes before each fault. The healthy false-incident rates are informative.

## Source compatibility (profile v2 parity)

`scripts/check_alibaba_source_parity.py` (criteria `alibaba-parity-criteria/v1`,
fixed before measurement, commit `9158af5`) ran natively on an owned kind
cluster on 2026-11-09 (environment `local-wsl-shared-host`): **PASS, 0 failures**.

- P2: all 33 captures are profile v2 with the pinned `_sum`/`_count` queries.
- P4: Prometheus counts match the server ledger within at most 2.6%.
- P5: Prometheus mean latency matches the exact server mean within at most 4.3%
  (limit 10%).
- P6: the shared transform produces a final relative window for every service,
  with finite scores on healthy traffic (XGBoost 0.32–0.35, below the 0.5 watch
  threshold).

## Limitations

- Alibaba has no error field, so the models do not learn 5xx. Errors stay with
  the SLO rule and MAD (owner role split, ADR-016).
- Breach labels are a relative-latency rule on production minutes, not operator
  incidents. Low precision partly reflects label noise.
- Evaluation services are disjoint by identity but share one cluster and time span.
- The local epoch uses p95 instead of mean latency and was inspected before with
  `38bcc766`; it is development evidence only.

## Decision options for the owner

1. **Accept G3 for local integration** (H4 → G6 with this bundle), recording the
   Q-07 precursor FAIL and the non-significant Q-05. Final claims remain with
   layer 2 and G8.
2. **Reject** and require a successor under a new frozen config, evaluated on a
   fresh Alibaba slice. It must be disclosed as created after layer-1 results.
3. **Defer** until layer 2 runs.
