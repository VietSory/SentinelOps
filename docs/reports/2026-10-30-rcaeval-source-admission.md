# RCAEval native source admission: trusted byte inventory

The native manifest reader now rejects replacement bytes even when a caller
supplies a checksum-consistent manifest under the frozen case/revision names.
The [trusted inventory](../../src/sentinelops/public_data/rcaeval_native_inventory.py)
locks the exact 12 files from the original publisher-verified acquisition, including
paths, sizes and SHA256. The complete selected inventory is checked before the
first cache-file read; missing modalities and extra files cannot silently change
the source scope.

This corrects a reproduced source-identity gap in the first
[native development implementation](2026-10-29-rcaeval-native-development.md).
An isolated synthetic Parquet file with a self-declared matching checksum could
previously pass the reader under a pinned case name. That fixture was not publisher
data. The actual publisher cache was unchanged; the legitimate acquisition and
first exact-head CI had passed. Integrity against a caller-declared checksum did
not by itself establish identity with the frozen publisher bytes.

## Measured correction

The admission fix was committed as
`5febb31dbcf74a22efbb21eecf0b303d4811d3ee` before a fresh diagnostic measurement.
[Machine evidence](evidence/2026-10-30-rcaeval-source-admission.json) records code,
inventory, profile, policy and report hashes. The full repeated three-case CLI
completed with exit 0 at 2026-10-30 01:47:24 UTC.

- **Focused software:** 88 passed, including four new negative inventory cases:
  checksum drift, omitted modality, added file and publisher-size drift. Each test
  rejects before the first cache byte; an earlier valid file cannot be opened
  before a later invalid record is admitted.
- **ML/dev regression:** 567 passed, with one existing Starlette/anyio deprecation
  warning. Contracts: 23 schemas, 17 samples and two existing configurations pass.
  The unchanged native profile retains its separate canonical-hash validation.
- **Package:** the wheel installs outside the checkout, imports the new inventory
  module and passes existing runtime/contract smoke checks. Native Parquet execution
  is separately covered by the actual source CLI.
- **Real source:** all 12 files admitted against the trusted inventory; publisher
  bytes verified again after execution. Both runs are identical, and every case's
  observations, scores, coverage, ranking and abstention exactly match the earlier
  implementation on legitimate publisher data.

The canonical business-output SHA256 remains
`4d1ab2e228e559abc9e9a0d6bd66f9748573db05c20ebf68f4591bde09257101`.
The numerical profile and RCA policy hashes remain unchanged. No source was added,
no extra telemetry case was opened, no thresholds/weights/features were tuned and
no raw input was rewritten.

This was self-review by one agent. The earlier isolated candidate test harness
initially omitted the unchanged contracts directory; restoring that directory
allowed its 33 focused native tests to execute. The subsequent checks above ran
in the actual checkout. No production assertion or acceptance gate was weakened.

## Result and release boundary

| Selected | Evaluated | Quarantined | Abstained | Qualified | Conditional qualified accuracy |
| ---: | ---: | ---: | ---: | ---: | --- |
| 3 | 3 | 0 | 3 | 0 | Unavailable |

The fix strengthens source identity; it does not improve localization quality or
accept G4. Native support still cannot meet the unchanged coverage gate; model,
SLO-onset, deployment and receipt-clock evidence remain unavailable. Holdout,
training-source roles, raw immutability and action restrictions remain unchanged.

The prior `3437dab` release has its own successful eight-job CI, seven artifacts,
transported native RCAEval result and three recomputed S1/S5 incident bundles.
Those results are historical evidence for that SHA. This correction requires its
own published-head CI and artifact verification before its release closes.

Reproduce with the [existing native CLI](../runbooks/RCAEVAL_NATIVE.md#reproduce-and-recover),
the same acquisition manifest and a new exclusive output path. Incomplete or
replacement manifests fail before diagnostic execution. Stop/revert the offline
consumer on failure; preserve the publisher cache and previous measurements.
