# GAMMA grouped resource dataset — measured support

Status: source dataset construction and integrity checks **PASS**; model fitting, live G2/G3 acceptance and activation **NOT READY**. Executed on 2026-10-26 using clean committed code `65bafe8603cba298809a7cf140378d2863f129a8`. This is an offline source-resource benchmark, not a live HTTP detector or precursor evaluation.

## Inputs and execution

The [split protocol](../archive/guides/GAMMA_GROUPED_DATASET.md) and [whole-group assignments](../../configs/gamma_resource_splits.json) were committed before any cohort feature/label windows were generated. All 194 cases are assigned; only the 101 native-evidence-admitted cases can produce windows. The 93 quarantined cases remain in denominators. Test/calibration roles were not changed after observing support.

```bash
.venv-ml/bin/python scripts/build_gamma_dataset.py \
  --manifest data/raw/public/manifests/190c2cc83635d67f883e48392216ae18ef0fbb011bbf8dda8f320a6a86fb6497.json \
  --admission artifacts/research/gamma-native-admission/all-v1-corrected/fddede8fba9981197a13c498c298edc1f1edf592115be23d2694f65baea2fce2.json \
  --output artifacts/research/gamma-grouped-dataset/all-v1
```

Exit code 0. The unchanged six-feature resource transform produced **29,610 candidate windows**, **22,771 feature-eligible** and **6,839 excluded** across 29 native entities. Coverage exclusion affects all 6,839 excluded windows; 19 also have sample-gap reasons, so reason counts overlap. No threshold, warmup or missingness rule was relaxed.

| Split | Admitted cases | Candidate windows | Feature-eligible | Eligible direct interference | Eligible reference proxy |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train | 54 | 7,909 | 6,058 | 1,137 | 590 |
| Calibration | 20 | 11,434 | 8,776 | 1,602 | 553 |
| Development | 10 | 5,716 | 4,456 | 1,034 | 227 |
| Test | 17 | 4,551 | 3,481 | 483 | 161 |

Peer-host context and boundary uncertainty are retained separately. Neither is assigned healthy or direct-fault truth. Test support was counted to document feasibility; no model or threshold was selected and no holdout model scores were generated.

## Reference feasibility result

The reference proxy means no recorded VM intervention overlaps/guards the window. It does **not** establish traffic, operational health, no unrecorded fault or SLO compliance.

| Split | Eligible reference rows | Cases with reference | Conservative groups with reference | Distinct case/cutoff pairs | Reference rows per entity |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train | 590 | 14 | 4 | 28 | 8–26 |
| Calibration | 553 | 20 | 2 | 27 | 8–23 |
| Development | 227 | 10 | 1 | 10 | 2–10 |
| Test | 161 | 7 | 1 | 7 | 1–7 |

Rows across services and overlapping windows are not independent replications. Even treating every proxy as healthy would leave only 8–26 train and 8–23 calibration rows per native entity, below the existing 300/100 live per-service floors; the proxy semantics also fail verified-health admission. Public native entities are not SentinelOps Payment/Checkout/Inventory. This slice does not authorize IF/MAD fitting under the project's healthy-reference protocol. It does not provide XGBoost pre-SLO gradual truth or live HTTP feature parity. **No model was fitted or activated.**

The bounded development-log probe also found no complete request stream in the four selected Nginx files: current files contain error/Lua stack-trace entries, previous-container files contain kubectl errors. It cannot establish request denominators, duration distributions or HTTP status mapping. This conclusion is scoped to those files, not every GAMMA modality.

## Integrity and software verification

All raw shard/member hashes were verified by the cohort reader. Every row in all three stores was read back: exact file hashes match the report, feature-row SHA links match truth/index, all 29,610 window identities are unique, and store lengths agree. Numeric predictors contain no phase, target, group or split fields.

The initial capability case still has 580 candidate / 437 eligible windows and its original numeric identity `e00da06523d3a4dbce22ca6ef4c9f9130818aff8b66232715b07095bbc064cc5`. Sharing the cutoff helper and repackaging source shards did not change its feature values.

- 13 focused dataset/resource tests passed, including label-change numeric invariance, split violation before output and forged/rehash admission rejection.
- Full Python regression: 281 passed. Final installed-wheel module/schema/API smoke passed outside the checkout.
- CI now rebuilds the complete actual cohort, reruns native admission, applies frozen assignments and checks measured counts, numeric identity and capability parity. A declared workflow is not evidence that a later commit's CI passed; exact-head results are tracked separately in project status.

[Machine-readable evidence](evidence/2026-10-26-gamma-grouped-dataset.json) preserves hashes, counts and limitations. Local report identity: `aa4c81fa49d28f57aaeedd8de13807509a894e7eaf8b50998174e61aa3e64f48`; pure feature-value identity: `1b36a0aa46c8d1d34b437ffb976cf093610e00fcc096179f487a170e8b85f227`. Acquisition/report hashes can differ with path/code provenance; the source file tree, frozen split and pure feature-value identity remain checkable.

## Consequence

Keep GAMMA/RCAEval, the split, quality rules and XGBoost target unchanged. Report the reference/health/live-compatibility gate as not-ready; do not fit for appearance or use intervention labels as precursor truth. Proceed with independent correlation/RCA and protected incident processing software while unresolved model prerequisites remain explicit. RCAEval remains RCA validation/holdout only, never a source of ML training/calibration rows.
