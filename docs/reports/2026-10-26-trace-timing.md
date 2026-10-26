# Native trace timing — source retention check

Status: timing parser/source equality **PASS**; new capture receipt metadata requires its own fresh Docker CI verification. This check does not establish RCA accuracy, historical trace availability or online lead time.

Executed 2026-10-26 from clean committed code `fe38d34b3db9aa90adca72650d2fbb5c93bd967f`. Input is the immutable serving manifest `artifact://manifests/bc4c790e504db574892f0d0fb0ba50cb875e9d36961a7c41e6d6a00023202ee7.json` of the already inspected local development pilot. The input store and all original source bytes remain unchanged.

All **20 trace snapshots / 140 native spans** were checksum-read and decoded. For each trace, decoded span-ID coverage exactly matches the native OTLP spans. Each decoded start/end integer was compared with the original `startTimeUnixNano`/`endTimeUnixNano` fields, and each duration with their exact integer difference. No span or request was selected by fault outcome. The shared validator also checked the 60 business server spans and their client parentage, correlation and observed HTTP statuses with `require_timing=True`.

All 140 timing triples match the native values. The complete span set includes business SERVER spans, Checkout HTTP clients and Inventory/Payment database clients; checking only the five HTTP spans per trace would omit native evidence. Timing-value identity: `439c683f5a26a3b1126fd5904b1b87370b164a003d1b1ec602149a8db92c694a`.

The 20 historical snapshots have **zero receipt clocks**. No timestamp was inferred from event time, adjacent metrics, file modification time or experiment end. Their span event times/durations are available for appropriately labeled analytical work, while verified availability-aware online RCA remains unsupported for these captures. The [new receipt-clock behavior](../TRACE_TIMING.md) applies to subsequent capture paths only.

Verification: full regression **313 passed**, installed-wheel smoke PASS. Focused tests cover exact one-nanosecond precision, numeric/string encoding, zero-duration versus missing timing, malformed/partial/reversed/range errors, consumer readiness, and legacy/fresh capture-clock summaries. Step 3/4 real Docker consumers now require complete native span timing; new Step 4 CI also verifies receipt-clock metadata. Declared checks are not evidence that the later SHA passed; exact-head CI is tracked separately in project status.

[Machine-readable evidence](evidence/2026-10-26-trace-timing.json) records code/input/report identities, raw/decoded coverage and limitations. No model, RCA ranking, incident write or remediation action was produced. Next: use explicitly bounded timing and source-availability status in conservative correlation/evidence-linked RCA, retaining the separate model/data gates.
