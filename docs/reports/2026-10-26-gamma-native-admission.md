# GAMMA native evidence admission and source-group constraints

This is measured semantic admission after complete raw-resource content verification. It moves the frozen GAMMA training source toward grouped dataset construction, without claiming model fit, healthy labels, split readiness or live acceptance.

## Actual execution and identity

Clean commit `cb4aa1f7e052e1530a9cbef55095e32623fc97d7` ran [native admission](../archive/guides/GAMMA_NATIVE_ADMISSION.md) over every case in cohort manifest `190c2cc83635d67f883e48392216ae18ef0fbb011bbf8dda8f320a6a86fb6497`. The verified source-file tree remains `ee11cd27de2451194fbe9df68365cbe6c67b0982d3b48f9dd0fb74b49374c3c6`. No raw source byte was repaired or rewritten.

The final [profile](../../configs/gamma_admission_profile.json) SHA256 is `daa277019b5dfc3b6149bc3a1bbbbf3d347a7950ce598e1413b98d2f00179d6e`. The development-inspection config SHA256 is `89567d8868c21d0f90125f90907d87ceb89843fc25dcba04a51ff2938c883d55`.

The full immutable report is `artifacts/research/gamma-native-admission/all-v1-corrected/fddede8fba9981197a13c498c298edc1f1edf592115be23d2694f65baea2fce2.json`; its filename is its SHA256. The [committed summary](evidence/2026-10-26-gamma-native-admission.json) retains provenance/counts. The [group snapshot](../../configs/gamma_source_groups.json) preserves complete membership and eligibility constraints for later dataset consumers; it does not assign splits.

## Measured cohort result

| Admission result | Cases |
| --- | ---: |
| Native evidence admitted | **101** |
| Quarantined | **93** |
| Total accounted for | **194** |

The 101 admitted cases contain **2,499 supported native entity records**, not independent windows or healthy samples. Their series still require the unchanged feature coverage/cutoff checks. Three admitted capture envelopes are shorter than 600 seconds; even the other 98 do not prove enough common coverage or reference windows after warmup.

Quarantine reasons include exact kind/VM phase-file mismatch (42 cases), unmatched/nonpositive native intervals (41), phase times outside observed resource clocks (4), empty phase logs (4), unparseable pod descriptions (3), absent valid resource clocks (2), and additional missing placement, warmup, unclosed phase or excessive-clock-span conditions. Reason counts can overlap; they are not additive unique-case counts. No missing/mismatched phase was interpreted as healthy.

Examples retained from previously inspected development data:

- `cpu_aug12_25min_200_0`: 29 supported entities, nine complete native events, admitted.
- `cpu_mem_sep29_10min_800_0`: 26 supported entities, twelve separately typed CPU/memory events, admitted.
- `mem_sep22_10min_800_0`: inconsistent memory/CPU pairing remains quarantined; no silent end-kind repair.
- `net_oct4_10min_800_0`: memory-prefixed logs do not match declared network targets; quarantined rather than reinterpreted from its filename.

Actual-source verification exposed a parser bug before publication: the monitor's legitimate Kubernetes node `userv17-jaeger` was initially checked with the narrower VM-target pattern. The correction validates DNS node identity separately while retaining restricted `uservN` interference targets. A regression ensures Jaeger stays excluded from model entities without quarantining valid business metadata. The unsuccessful earlier report remains in a separate ignored artifact directory, rather than being overwritten or counted as accepted evidence.

## Group and holdout constraints

Observed-clock days with 300-second purge and replicate-family coupling yield **19 conservative connected groups**. Exactly **seven groups** meet current candidate-final-holdout constraints: known capture clocks, at least one admitted case, and no previously inspected case anywhere in the group. One connected group has 60 cases, demonstrating why filenames/row count cannot be used as independent split units. Inspected-case exclusions propagate through whole groups.

These groups are **not proof of statistical independence** and not a frozen train/calibration/development/holdout assignment. Unsupported cases remain accounted for. Dataset construction must first assign whole groups, then build windows and measure reference sufficiency without changing quality rules to increase counts. Intervention truth and no-recorded-interference references are not independent health/SLO/precursor certificates.

## Verification and remaining scope

**277 full tests passed**, including 47 focused admission/resource/phase tests. Final installed-wheel module/schema/API smoke passed outside the checkout. Tests cover the observed Jaeger node, simultaneous typed interventions, missing/malformed/ambiguous source, invalid clocks, order-invariant grouping, midnight purge, unknown/empty group eligibility, inspected-group exclusion and verified-source execution. CI repeats admission on the fresh frozen cohort and asserts measured counts and exact committed group constraints.

Next: freeze explicit group assignments before windowing, retain all quarantine/coverage/reference denominators, then determine IF/MAD source-benchmark readiness. XGBoost's pre-SLO gradual target, independent SLO evidence, live HTTP parity, arrival times and G2/G3/G7 remain open. No model was fitted or activated by this slice.
