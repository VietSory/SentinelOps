# GAMMA client captures: bounded acquisition and SLO capability result

The 48 original client files were acquired and verified, then audited twice at
committed implementation `8b582b012506cd9eb518752a719d47271221a668`.
All 24 capture pairs execute successfully, and the two offline reports are byte
identical. **They do not establish eligible SLO buckets, certified healthy
training/calibration windows or independent pre-SLO precursor runs. G2/G3 remain
blocked.** This result covers these four quarantined groups, not every GAMMA modality.

[Machine evidence](evidence/2026-10-30-gamma-client-capability.json) records the
complete safe per-pair summaries, source/config hashes, acquisition bounds and
verification. [Protocol and reproduction](../GAMMA_CLIENT_CAPABILITY.md) describe
the fixed interpretation and source-access controls.

## Source identity and acquisition

- Original GAMMA v1, Kaggle version ID 7638732, under the unchanged training-data
  policy. Catalogue SHA256:
  `69934c78fb790eeaa12931f25669770b50e44c70b647fd98c8a6d63698b4caf8`.
- Four whole groups remain quarantine, zero admitted cases, ineligible for final
  holdout. Existing group/split bytes and the GAMMA/RCAEval source roles are unchanged.
- Exactly 48 selected members / **2,169,411 expanded bytes**. No empty selected
  files. Three native request types and measurement/warm-up-filename pairs are
  all accounted for. Large trace JSON and trace-ID members were not acquired.
- Original bounded acquisition: **194 range requests**, 22,471,154 archive response-body
  bytes, 196 metadata/range calls. Limits: 32,000,000 body bytes and 256 calls;
  response accounting excludes headers and protocol overhead.
- All selected ZIP lengths/CRC32 and project SHA256 values verified on acquisition
  and readback; all 48 hashes unchanged after both actual CLI executions. These
  are project-frozen hashes, not publisher-supplied per-file SHA256 attestations.

## Actual formats and denominators

Each of the 24 logs has a whole-run request/duration summary and recorded
HdrHistogram count. Each companion file has only unsigned integer source lines;
its unit, request timestamp/status, sampling and temporal-order semantics are
unverified. **All 24 integer-line counts differ from the printed request total.**
An independent raw-line/request-summary calculation matches every reported count.
A count difference is evidence against treating these files as a complete
one-row-per-reported-request export; it does not establish the sampling mechanism.

| Experiment | Native request type | Measurement requests / integer lines | Warm-up-filename requests / integer lines |
| --- | --- | ---: | ---: |
| `cpu_july27_60min_800_0` | compose | 96,093 / 6,097 | 23,902 / 1,508 |
| `cpu_july27_60min_800_0` | home | 576,223 / 36,343 | 23,902 / 1,506 |
| `cpu_july27_60min_800_0` | user | 288,644 / 18,062 | 23,901 / 1,447 |
| `cpu_july28_800_0` | compose | 92,971 / 5,739 | 23,893 / 1,482 |
| `cpu_july28_800_0` | home | 414,317 / 24,298 | 23,902 / 1,543 |
| `cpu_july28_800_0` | user | 252,302 / 16,954 | 23,900 / 1,512 |
| `cpu_july29_800_0` | compose | 192,362 / 12,289 | 23,899 / 1,463 |
| `cpu_july29_800_0` | home | 1,152,677 / 72,375 | 23,902 / 1,463 |
| `cpu_july29_800_0` | user | 576,222 / 36,077 | 23,900 / 1,439 |
| `cpu_test_july24_800_0` | compose | 96,091 / 5,934 | 23,903 / 1,484 |
| `cpu_test_july24_800_0` | home | 576,224 / 36,125 | 23,901 / 1,504 |
| `cpu_test_july24_800_0` | user | 288,653 / 18,111 | 23,895 / 1,560 |

The measurement `compose` log for `cpu_july27_60min_800_0`, for example, prints
96,093 requests, a recorded histogram count of 95,259 and 133 socket timeouts;
its companion has 6,097 integers. These are distinct observed quantities, with
no documented one-to-one alignment. The histogram/request counts are not
interchangeable denominators. An omitted socket/status line stays unavailable.
Printed non-2xx/3xx counts, where present, do not identify HTTP 5xx specifically.

No source request-time clock, 60-second count/latency window alignment, HTTP
5xx numerator, native-request-to-live-service mapping, or verified independent
healthy/precursor protocol is established by these files. Whole-run histograms
cannot identify breach onset or lead time. A warm-up filename does not certify
health. Integer scale is not a unit declaration. The upstream
[wrk2 documentation](https://github.com/giltene/wrk2) describes aggregate
HdrHistogram reporting and its emulated Lua statistics API; similar headings
do not identify this source's binary/exporter version or integer-file semantics.

## Verification and remaining gate

Focused acquisition/parser/integrity tests: **60 passed**. Full ML/dev regression:
**630 passed, no skips**, one existing Starlette/anyio deprecation warning.
Contracts: 23 schemas, 17 samples and two existing policy configurations.
Installed-wheel checks pass outside the checkout. Negative tests cover wrong
role/heldout/whole-group admission, self-declared snapshot hashes, hidden/omitted/
duplicate members, corrupt last files before semantic parsing, immutable cache,
symlink paths, corrupt ZIP payloads, invalid integer/summary formats and byte/line
budgets. No raw logs, request URLs or signed leases enter public evidence.

The existing committed grouped-resource readiness report was also re-audited
without opening any raw heldout data: every live business service still has
**0/0 certified healthy train/calibration windows**, versus 300/100 required,
and zero verified precursor runs. The new client audit provides no certificate
that changes those counts. No model was fitted or activated; no threshold,
source, split, feature definition, target or frozen gate changed.

This was self-review by one agent. The source audit is complete locally, while
publication requires its own exact-head CI and transported report verification.
Earlier RCA/continuous-worker releases retain their original source attribution.
