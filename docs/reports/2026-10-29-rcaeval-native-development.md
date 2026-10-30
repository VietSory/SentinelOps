# Native RCAEval development: measured source-to-policy execution

The [native adapter/evaluator](../RCAEVAL_NATIVE.md) executes all three previously
inspected development cases at frozen revision
`afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`. **Three evaluated, zero quarantined,
three abstentions, zero qualified predictions.** Conditional qualified accuracy
is unavailable; this is successful software execution, not a localization-quality
or operational G4 acceptance.

The protocol, code and tests were committed as
`6af0ae16b4430b126e4a03eec48a0a372bad75ab` before the first actual diagnostic run.
The [compact machine evidence](evidence/2026-10-29-rcaeval-native-development.json)
records source/file/profile/policy hashes, business/result digests, per-case
denominators and the separate raw-reference verification. No additional telemetry
case, validation/holdout data, source, model fitting or action permission was added.

## Actual source execution

The real CLI completed at 2026-10-29 16:33:59 UTC with exit 0. It ran source admission
→ bounded verified-byte decoding → native observations/links → fixed-weight scoring
→ frozen business result → evaluator label join twice, obtaining identical output.
The acquisition manifest remains
`565c5b7b3f43d05ef9ba2930e80a65714e3ce4bc1e562fbc91835eefe6a0a0c6`;
all 12 publisher files were verified again after execution. Raw bytes were unchanged.

| Development case | Metric rows | Log rows | Span rows | Window logs / finished spans | Maximum coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| `re1ob_adservice_cpu_1` | 4,201 | Absent | Absent | Absent | 0.00 |
| `re2ob_checkoutservice_cpu_1` | 1,441 | 171,322 | 391,997 | 35,016 / 80,295 | 0.40 |
| `re3ob_adservice_f3_1` | 1,441 | 70,558 | 165,302 | 14,576 / 34,532 | 0.40 |

The fixed trailing windows use only the last metric second, never injection time.
RE2/RE3 contain 67,134/22,540 backward adjacent span starts; these rows are retained.
Each has 12 observed parent-service/child-service edge types in the window, with
zero unresolved parent links there. These observations do not establish a complete
topology or causality. Native `frontendservice`/`frontend` identities remain distinct;
root labels do not create missing native trace entities.

The business result SHA256 is
`4d1ab2e228e559abc9e9a0d6bd66f9748573db05c20ebf68f4591bde09257101`.
The canonical profile SHA256 is
`4e9e5e2e57d01d0c59ac0de450612c90fb9d39d2a0f47370559a6568a991f3a1`;
the unchanged RCA policy SHA256 is
`62ee6f9ed87a1dddcef3ef30cf45e662da887ce0f532a2638c6f4d9d5eab052e`.
File hashes and canonical-object hashes are identified separately in the JSON.

## Verification scope

- **Focused software:** 84 tests covering native extraction, role/path admission
  and existing RCA arithmetic. The 29 new native tests include exact epoch precision,
  unsorted rows, missing/null/NaN values, unit pairs, duplicate IDs, byte/decode/identity
  limits, live/native method isolation, label mutations and all-quarantine failure.
- **ML/dev regression:** 563 passed. One existing Starlette/anyio deprecation warning;
  no tests were skipped or weakened to obtain this result.
- **Contracts:** 23 schemas, 17 samples and two existing configurations validated.
  The new native profile additionally passes its frozen canonical hash check.
- **Package:** installed-wheel imports and existing runtime/contract smoke passed
  outside the checkout. Native Parquet extraction itself was exercised by the
  separate real-cache CLI, not by the base-dependency wheel smoke.
- **Actual source:** all three cases evaluated twice identically; publisher file
  hashes rechecked. An alternative Arrow-to-Pandas implementation independently
  recomputed window masks, per-service log/span counts, cumulative span durations,
  unrenormalized scores and coverage directly from verified bytes, matching every case.
- **Self-review:** source admission, label separation, PII omission, resource bounds,
  error/quarantine behavior and live compatibility reviewed. This was one agent;
  the alternative numerical implementation is not an independent human review.

The initial native unit run exposed an incorrectly specified synthetic checksum
fixture; it was corrected to the existing publisher size/checksum contract. Review
also closed revision-symlink escape variants and added explicit entity/span-identity
memory bounds before measurement. No weights, thresholds or source selection changed
in response to diagnostic output. Raw-reference checks and repeat execution are
software/numeric evidence, not independent incident trials.

Exact-head remote CI and transported artifacts must be verified for the published
commit separately. The public-data job now runs this same native CLI on fresh pinned
acquisition and retains `rcaeval-native.json` in `public-data-source-audit`; older CI
runs do not certify the new consumer.

## Remaining gates and reproduction

Recorded span-work fractions and fixed exception-marker presence provide at most
0.40 coverage under the unchanged 0.75 floor. Approved source-compatible model,
objective SLO onsets, deployment evidence and source receipt clocks are absent.
RE1 has neither supported log nor trace evidence. All three correctly abstain;
abstention is not counted as a correct root cause. No healthy, HTTP RED, precursor,
historical online/MTTD, cross-system generalization or causal claim follows.

Use the [runbook command](../RCAEVAL_NATIVE.md#reproduce-and-recover) with the existing
manifest and a new exclusive output path. Keep the source pair, partitions, target
and policy fixed. Full G4, final P11/S5 quality, ML readiness/transfer and G5–G8 remain
open. Stop/revert the offline consumer if necessary; do not rewrite raw files or
previous measurements to repair a failed run.
