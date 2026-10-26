# SLO episode correlation — actual development replay

Status: conservative correlation software and captured-source integration **PASS**; RCA localization, real multi-fault grouping evaluation, continuous processing and durable computed IER integration remain open. Executed 2026-10-27 Asia/Bangkok from clean committed code `e00212ae1bc9f2a3968ceff87cb7dbf93d86138a`.

The [consumer](../STEP7_CORRELATION.md) read the immutable [S0/S3 health replay reports](2026-10-26-slo-health-replay.md), verified hashes, health policy/final states and per-service prior-ID/time-grid/cutoff chains. It did not read an experiment truth store, inject a target label or fit a model. Each report is an explicit separate source scope.

| Input scope | Signal snapshots | Opening episodes | Non-opening snapshots | Correlation groups |
| --- | ---: | ---: | ---: | ---: |
| S0 | 30 | 0 | 30 | 0 |
| S3 | 60 | 2 | 58 | 1 |

The S3 openings for Checkout and Payment have observed bucket onset `2026-10-26T03:29:00Z` and virtual confirmation decision `03:32:00Z`. The group contains exactly these two source signal IDs and the configured path `checkout-service → payment-service`; Inventory has no opening and is not added. Payment is a **structural candidate**, not a scored or confirmed root cause. No anomaly strength, risk score, RCA confidence or action approval was invented.

The fixed 120-second grouping rule/topology was implemented before this source execution. No rule/window or assignment was changed after observing the result. Both actual CLI executions were repeated with exclusive output stores; complete reports, logical result IDs and group IDs are byte-identical. Group snapshot `b3bfd77cd66be204656b4319309643a711bfc0c8e0e5f2607d9d7379807cb557` retains dependency support, source scope and topology identity.

```bash
.venv-ml/bin/python scripts/replay_slo_correlation.py \
  --health-store artifacts/incidents/slo-health/s3-v1 \
  --report-ref artifact://manifests/604fa0a5cb1276dc449acc955f2e7ab91a590b7754698b7bb716465fdd5de059.json \
  --output artifacts/incidents/correlation/s3-v1
```

All four real CLI executions exited 0. The [machine-readable summary](evidence/2026-10-27-slo-correlation.json) binds source code, health scopes, counts, facts, groups and repeat identities. S0 report: `37920d883127628d7d715ea968d16a22f9f665a4a7b83ecac8431807dbcb1c12`; S3: `f2a5d5898b2438ae6e88ca005d509cf0fa090032f07112ff6f1e4547c55b86c8`.

Software verification: 30 focused health/correlation tests passed; full regression **325 passed**; **21 schemas / 16 samples / actual frozen training-data policy** validate; installed-wheel module/schema/semantic/API smoke passed outside the checkout. Tests cover cascade, independent sibling roots/shared Checkout, time-chain bounds, future/non-opening accounting, scope changes, corrupted topology, conflicting episodes, rehashed forged grouping and broken upstream chains. The real independent-root/S5 case is not present in these two captures, so those adversarial fixtures are software evidence only.

Docker CI now processes a newly captured health scope into dependency-supported Payment/Checkout grouping. Its declared step is not an exact-head result; remote verification is tracked separately. Mode remains `event-time-analytical`, historical emissions unverified, `rca_evaluated=false`, `actions_allowed=false`. No IER or infrastructure mutation occurs. Next: implement bounded evidence extraction/scored RCA with missing-support abstention and protected incident processing; partition RCAEval before diagnostic tuning/evaluation. Model/data gates and frozen sources/target remain unchanged.
