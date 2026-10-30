# Three-phase published implementation verification

Published implementation/documentation candidate:
`1b87950f86eb081a2261a6be374345968f13249b` on `main`.
Its production source is unchanged from tested implementation
`b27a8c260f1408962dcdba8770df59da0eb8b5a7`.
[Exact-candidate CI](https://github.com/VietSory/SentinelOps/actions/runs/40990995622)
provides the job conclusions and downloadable runtime evidence for this SHA.
Later changes require their own CI; this record does not transfer earlier results
to changed source.

## Verified local scope

- Full supported Python 3.11/ML environment: **599 passed, zero skipped**, one
  existing Starlette/AnyIO deprecation warning. Contracts: 23 schemas / 17 samples /
  two configurations; installed wheel outside checkout PASS.
- Canonical real local S1: 18 source buckets / 54 signals, one Payment–Checkout
  incident, four immutable revisions through natural CLOSED; process/consumer
  SIGKILL, competing consumers, PostgreSQL and Prometheus outage recovery PASS.
- Captured raw → health → correlation → RCA → IER replay PASS with external
  snapshot checksums; ten isolated corruption probes rejected.
- Protected Uvicorn/real PostgreSQL API PASS, including current/history/context,
  authentication, ETag, pagination, four versions/four outbox records and no
  read-side writes. This was repeated under the published candidate.
- Canonical RCAEval: original three authorized development cases, three abstentions,
  zero qualified predictions. Trace/log ablations retain 100% abstention. Descriptive
  top-3 is 1/3 and MRR 1/6 with trace support; qualified accuracy is unavailable.
- G2/G3 remain BLOCKED. Approved-source certified healthy windows are 0/0 per
  live service versus 300/100 required; verified independent precursor runs zero.
  Real estimator primitives are fixture-tested; approved trained/activated models
  remain zero. Full G4, final holdout, AWS and remediation gates remain unaccepted.

Detailed commands, source attribution, failed attempts, limitations and actual
measurements are in [Phase 1](PHASE1_CONTINUOUS_PIPELINE.md),
[Phase 2](PHASE2_DATASET_AI_MODELS.md), [Phase 3](PHASE3_RCA_AI_INTEGRATION.md)
and the [source/commit inventory](PHASE_SOURCE_CHANGES.md).

## Transported public-data artifact verification

Artifact `public-data-source-audit`, ID `12949217922`, belongs to the exact published
SHA above. The downloaded ZIP SHA-256 matches GitHub's recorded digest:
`301d2ad09f65254d511e4fc244f1e6a0b20bab336a5287b0946faa4aa9e6c427`.
Only `rcaeval-native.json` and `rcaeval-native-development.json` were opened from
that archive. Other members, including grouped training/test material, were not
opened for this verification.

The canonical report binds the exact source commit and preserves business hash
`4d1ab2e228e559abc9e9a0d6bd66f9748573db05c20ebf68f4591bde09257101`.
Its denominators match the local three-case execution; the supplemental ablation
metrics also match exactly. The sealed final holdout stays closed. Artifact storage
retention is seven days, so the committed reports retain hashes and measured scope.

## Delivery workflow change

The owner requested PR delivery after the implementation had already been pushed
under the previous direct-main policy. A subsequent `git pull --ff-only origin main`
returned `Already up to date`; direct remote SHA comparison confirmed no local lag.
The follow-up verification/workflow change uses a topic branch and PR. Its code
diff is documentation; the implementation commits already on main remain linked
in the phase inventory. Existing history is preserved.
