# Model signal replay clocks — 2026-10-31

## Result and scope

The continuous model-signal producer now records inference start and completion separately in `validated-window-model/v2`. Replay checks feature freshness at the recorded start and preserves actual completion as prediction availability. Both canonical and legacy replay use one validator. Existing v1 signals remain readable without inventing historical start timestamps. Strict detection/risk HTTP schemas, accepted source policy, model admission and RCA weights/thresholds are unchanged.

This is a software correctness fix. There are still zero approved models and no new model accuracy, lead-time or operational diagnostic-quality claim. Numerical boundary tests fit a genuine Isolation Forest on explicitly synthetic matrices; those artifacts are fixtures and cannot activate.

## Reproduced defect

Baseline at `4659321966194a69231cfe6966011d32bef91cfa`: **662 tests passed**, zero skips, one existing Starlette/AnyIO deprecation warning, 27.00s.

A focused regression constructed actual fixture-estimator inference beginning 179 seconds after feature-window end and completing at 181 seconds. Original verification used `inference.inferred_at` (completion) as the inference cutoff. It therefore returned a stale-feature result instead of reproducing the originally valid inference. The new regression failed against the original code with `ValueError: model signal does not reproduce with the pinned actual estimator`.

The source feature was eligible at start; its later completion does not retroactively alter that decision. Separately, the existing RCA support-age guard still runs at RCA decision time. Reproducing an inference is not permission to attach stale evidence or bypass abstention.

## Implementation

- `collect_model_signals` pins and passes the exact start clock to inference. It records the actual finish clock after execution, rejects backwards processing clocks and emits v2 with `inference_started_at` and `evaluated_at`. The nested `inferred_at` is prediction availability at completion.
- `verify_model_signal` validates the explicit version/fields, service/window, start/finish/source ordering, status and exact estimator result. It reconstructs numerics at the v2 start while retaining the recorded completion clock. v1 keeps its original completion-based behavior; already committed artifacts are not rewritten.
- Future/unavailable source features can still produce an honest UNAVAILABLE signal and replay it. A future source cannot support an AVAILABLE prediction. Unsupported versions, missing starts, false statuses and completion after evaluation are rejected.
- Canonical evidence verification and the legacy prototype replay share this implementation. No second registry, estimator, source mapping or fallback score was added.

V1 lacked an independently recorded start, so this fix cannot recover one for old boundary-crossing records. It does not silently retrofit them. Current real histories contain no validated model inference; the correction is demonstrated with software fixtures, not promoted model performance.

## Executed verification

Commands used `.venv/bin/python`, `OPENBLAS_NUM_THREADS=1` and the existing Nix GCC/zlib `LD_LIBRARY_PATH`:

```sh
python -m pytest -q
# Before fixing: this new regression failed as described above.
python -m pytest -q tests/test_model_signals.py::test_inference_crossing_freshness_boundary_replays_at_actual_start
# After fixing:
python -m pytest -q tests/test_model_signals.py
python -m pytest -q
python scripts/validate_contracts.py
python scripts/check_installed_package.py
```

Focused tests: **13 passed**, 5.03s. Full corrected regression: **672 passed, zero skips**, one existing warning, 29.53s. Contracts: **23 schemas + 17 samples + 2 configurations PASS**. Installed wheel outside the checkout: **PASS**. New cases cover genuine-estimator boundary replay, actual producer start/finish recording, legacy v1 compatibility, future source unavailability, unsupported/missing version fields and forged clocks/status.

The three transported actual captures from earlier exact-head CI run `41029709188` were replayed using current code and their published health/incident snapshot hashes: S1 **one incident**, S5 latency **three incidents**, S5 blocking errors **three incidents**; all reproduce exactly with 28 historical revisions retained. This is compatibility verification of earlier real captures, not a new live model/workload measurement. Original capture source is GitHub merge `3425771bcd36997b886b9e55e335a8676471ef18`, whose tree equals prior HEAD `4659321`. Machine replay output is retained locally at `artifacts/blockers/model-clock-history-replay.json`.

## Next research decision

[ADR-014](../adr/ADR-014-proposed-local-training-evidence.md) prepares a concrete prospective local HTTP source/oracle/label/partition proposal. Its status is **Proposed, owner review required**. It changes no accepted contract and authorizes no new training acquisition, fit, source substitution, holdout access or activation. G2/G3 remain BLOCKED; G4 diagnostic quality remains unaccepted.

The new published HEAD needs its own completed CI; the prior 8/8 run at `4659321` does not certify these changes.
