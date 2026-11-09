# Model card — SentinelOps latency detectors `badf9da0…` (ADR-016)

| Field | Value |
| --- | --- |
| Model version | `badf9da0b563f4900fa099b11ba9223ee864d414ef3aa9af82a534db962d85e3` |
| Bundle | `sentinelops-model/v3`, origin `admitted-alibaba-v2022`, manifest SHA-256 `2d1a5376eccdd691da0741b9dc88eecd223d75a85a23fa712e00c1dc53291e62` |
| Scope | One service-agnostic bundle for all business routes (Checkout, Payment, Inventory) |
| Fitting commit | `a1820c7` (clean checkout), seed 42 |
| Libraries | numpy 2.2.6, scikit-learn 1.7.2, joblib 1.6.0, xgboost-cpu 3.1.1 (checked at load) |
| Status | G3 accepted with conditions for local integration only (D-22); not a final or AWS claim |

## Intended use

- **Isolation Forest:** detect an abnormal service latency and call-rate pattern
  in the last five minutes, relative to the same service's own history.
- **XGBoost:** estimate the risk of a latency breach within the next 300 seconds.
- **Outputs:** both feed RCA (`anomaly_strength`) and the risk signal. They never
  dispatch an action by themselves; the H4 authority, the known-good ledger and
  the rollback safeguards still decide.

**Out of scope:** HTTP 5xx errors (the source has no error field; the SLO rule
and MAD cover them), resource saturation, and any service with fewer than five
minutes of history before a five-minute window.

## Training data

- **Source:** Alibaba `cluster-trace-microservices-v2022`, table MSRTMCR, from
  production. Files 0–119 cover trace hours 0–6; every archive is checksummed at
  download. The licence is for research and study use, and publications should
  notify the maintainers.
- **Per-minute inputs:** call-rate-weighted mean `http_rt` (ms) and summed
  `http_mcr` per microservice.
- **Splits** by `sha256(msname) mod 10`, so evaluation services were never fitted:

| Split | Services | Windows | Healthy | Breach positives |
| --- | --- | --- | --- | --- |
| train | 6,184 | 2,116,659 | 1,687,465 | 18,130 |
| calibration | 2,092 | 716,253 | 574,122 | 5,463 |
| evaluation | 2,128 | 725,025 | 576,758 | 6,597 |

## Features and labels (frozen before the fit)

**Feature set `http-relative-5m/v1`.** Each feature compares the last five
minutes with the service's trailing median: at most 60 and at least 5 minutes,
strictly before the window.

| Feature | Meaning |
| --- | --- |
| `rt_log_level_rel` | Latency level |
| `rt_slope_rel_per_min` | Latency slope |
| `rt_log_peak_rel` | Latency peak |
| `rate_log_level_rel` | Call-rate level |
| `rate_slope_rel_per_min` | Call-rate slope |

Training and live inference share one transform. The vectorised version is
tested equal to the single-window reference.

**Labels `relative-latency-breach/v1`.**

- A *breach minute* has latency greater than 2 × its baseline.
- An *onset* is two consecutive breach minutes.
- A *positive* is a clean window followed by an onset within 300 s.
- A window is *healthy* when neither it, its horizon nor a two-minute tail
  contains a breach minute.

## Models

| Model | Configuration | Threshold |
| --- | --- | --- |
| Isolation Forest | 100 trees, `contamination=auto`, train-only RobustScaler, fitted on healthy train windows | IF 0.636 and MAD 91.6, both at the 1% false-alert budget on healthy calibration windows |
| XGBoost | 50 trees, depth 3, learning rate 0.05, train-only class weighting, no early stopping | Watch 0.5 / degrading 0.8 |

## Evaluation

| Layer | Result |
| --- | --- |
| 1 — unseen Alibaba services (run once, [packet](G3_CANDIDATE_ALIBABA_BADF9DA0.md)) | IF P 0.256 / R 0.347 / F1 0.295, 0.33 false incidents per healthy service-hour. MAD F1 0.286; the IF−MAD 95% CI [−0.022, +0.039] is not significant. XGBoost AP 0.070 vs prevalence 0.011 and slope control 0.021, XGB−slope CI [+0.034, +0.065]. Precursor recall at lead ≥ 60 s is **0.24 (target 0.70, FAIL)** |
| ADR-015 epoch replay (p95 fallback) | 0 TP for IF and MAD. This is structural: faults start before five minutes of baseline exist. IF false incidents 0.44–1.75 per healthy hour |
| Source parity, profile v2 (kind) | PASS. Mean latency is within 4.3% of the server log; counts are within 2.6% |
| Native H4 (S2 Kubernetes) | PASS. IF `anomaly_strength` 0.989, Payment RCA score 0.797, `dispatch_authority=true` |
| 2 — local v2 epoch (D-21) | Pending. Frozen in advance, ≥ 30 healthy minutes before each fault |

## Limitations and risks

- **Label noise:** labels are a relative-latency rule on production minutes, not
  operator-confirmed incidents. Low precision partly reflects this.
- **Scenario shift:** Alibaba breaches are production regressions, while the
  local scenarios are injected faults. Layer 2 measures this transfer.
- **Weak early warning:** precursor recall misses the Q-07 target. Do not present
  XGBoost as a reliable early-warning system.
- **Cold start:** a new service or deployment has no signal until it has 10
  minutes of accepted v2 history. Signals are reported as unavailable, never as zero.
- **Shared span:** evaluation services share the training services' cluster and
  time span, so service independence is not IID proof.
