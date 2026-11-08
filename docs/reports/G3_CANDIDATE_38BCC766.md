# G3 review packet — candidate `38bcc7668851…`

Owner decision required. This packet accepts nothing; `G3_accepted=false` in every source report.

- candidate ID: `38bcc7668851249c7b2246c24d0a2eb4fbeacb6b0fe306703ae91386d17956bd`
- v1 report: `artifact://manifests/da6d6462f661b93ad2c451206b9bb83f3abc958ee461abf577db113d1677a4a0.json` (`local-model-development/v1`)
- v2 report: `artifact://manifests/62f409783561c13354a2131d9f0c338b0f82b2950ac712472a0c911179745dc6.json` (`local-model-development/v2`), detector epoch `artifact://manifests/be5c00898a396931c998990a733d3e67dc89be84ada5e78757cb628ec9a5f6bd.json`
- dataset: `artifact://manifests/a5b628bec29f169e3957324f29a0c86e23ff5c4bc39c540f9039be7633388857.json`; source profile `prometheus-increase-60s-5s/v1`

## Per-route evidence

| Service | XGBoost AP / precursor recall ≥60 s (v1 gradual) | IF abrupt events (v2) | MAD abrupt events (v2) | IF−MAD F1 95% CI (abrupt) | IF gradual v1 | IF gradual precursor-separated |
| --- | --- | --- | --- | --- | --- | --- |
| checkout-service | 0.973 / 1.000 | TP 7 / FN 3 / FP 0; F1 0.824; false/healthy-h 0.00 | TP 10 / FN 0 / FP 0; F1 1.000; false/healthy-h 0.00 | [-0.538, 0.000] (1000 valid) | TP 0 / FN 10 / FP 15; F1 0.000; false/healthy-h 3.60 | TP 0 / FN 10 / FP 15; F1 0.000; false/healthy-h 4.50; precursor episodes 0 |
| inventory-service | 0.930 / 1.000 | TP 3 / FN 2 / FP 3; F1 0.545; false/healthy-h 1.00 | TP 5 / FN 0 / FP 0; F1 1.000; false/healthy-h 0.00 | [-0.714, -0.333] (1000 valid) | TP 0 / FN 5 / FP 5; F1 0.000; false/healthy-h 1.02 | TP 0 / FN 5 / FP 5; F1 0.000; false/healthy-h 1.12; precursor episodes 0 |
| payment-service | 1.000 / 1.000 | TP 3 / FN 2 / FP 3; F1 0.545; false/healthy-h 0.99 | TP 5 / FN 0 / FP 1; F1 0.909; false/healthy-h 0.33 | [-0.659, -0.182] (1000 valid) | TP 0 / FN 5 / FP 13; F1 0.000; false/healthy-h 2.67 | TP 0 / FN 5 / FP 13; F1 0.000; false/healthy-h 2.92; precursor episodes 0 |

## Source diagnostic: training range versus certified healthy development windows

| Service | train p95 centre / IQR (ms) | dev healthy p95 median / IQR (ms) | dev healthy IF flag rate |
| --- | --- | --- | --- |
| checkout-service | 24.36 / 0.105 | 28.79 / 6.239 | 0.67 |
| inventory-service | 4.77 / 0.018 | 4.78 / 0.017 | 0.00 |
| payment-service | 4.89 / 0.230 | 6.04 / 1.025 | 0.09 |

## Limitations carried from the reports

- Development only; no final holdout or production generalization claim.
- Abrupt events are prospective evaluation-only groups captured after this protocol was committed.
- Gradual results reuse previously inspected development groups; precursor-separated accounting on them is not independent evidence.
- Event metrics use recorded feature cutoffs with instantaneous counterfactual inference, not historical alert emission.
- Risk/XGBoost evidence remains the v1 gradual protocol; abrupt runs supply no precursor labels.
- Live inference timing, parity and all remaining gates require separate evidence.
- Training healthy p95 occupies a very narrow range; IF isolates any value outside it regardless of scaling. More independent healthy training variance is required, not a preprocessing change.

## Decision options for the owner

1. Accept G3 for this candidate with the stated limitations, then build the release (`scripts.build_model_release`) after B's parity record.
2. Reject G3 and require a successor candidate, frozen before it sees any further development data.
3. Defer pending additional independent evidence.

## Owner decision — 2026-11-08

**G3 approved with conditions** for candidate `38bcc7668851…`, by explicit owner
answer, after the independent review and the v2 results above.

- **Scope:** local integration only (H1 activation on lane B, H4 assessment, G6
  local trial). No production, AWS or final-quality claim.
- **Recorded limitations:**
  - IF ignores both HTTP error-rate features: the training error rate is always
    0, so no tree splits on them. It detects 12/12 abrupt latency events but
    only 1/8 abrupt error events.
  - IF event F1 is below MAD on all routes for abrupt events, so Q-05 FAILS at
    development.
  - The narrow training healthy p95 range raises Checkout drift flags.
- **Follow-up:** a successor candidate with independent healthy training groups
  that include sparse, SLO-compliant 5xx is prepared in parallel and frozen
  before it sees new development data. G8 claims require it, or an equivalent
  fix.
- **Gate dependency:** the release still requires a PASS
  `model-source-parity/v1` record. The first local run FAILED C5 v1 on one
  Checkout bucket at the 25 ms edge. Per a separate owner decision, C5 was
  versioned to v2 (same or adjacent bucket) with a fresh measurement; the v1
  FAIL record is kept.
