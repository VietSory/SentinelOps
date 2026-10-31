> Archived guide: retain its original scope for reproduction. For current work,
> use the [documentation index](../../README.md) and active runbooks.

# GAMMA client-capture capability audit

The frozen GAMMA v1 catalogue also contains small client outputs outside the
previous raw-resource cohort. This audit checks precisely 48 original files:
three native request types (`compose`, `home`, `user`), four capture suffixes
(`.log`, `.warm_up`, `_latencies.txt`, `_latencies.warm_up`) and four experiments:
`cpu_july27_60min_800_0`, `cpu_july28_800_0`, `cpu_july29_800_0` and
`cpu_test_july24_800_0`. This is a source capability investigation under the
[existing source contract](../../../configs/training_data_contract.json).

All four whole groups are already **quarantine**, contain zero admitted cases
and are ineligible for final holdout. Their frozen group and split snapshots
are checked before reading client bytes or making a source request. This audit
cannot admit a case, promote a group, fit a model or change the live HTTP feature
registry, SLO thresholds or pre-SLO gradual XGBoost target. Larger trace JSON and
trace-ID files, other experiments and heldout telemetry are outside this scope.

## Integrity and resource bounds

[gamma_client_capture_scope.json](../../../configs/gamma_client_capture_scope.json)
records the complete exact catalogue metadata and project-frozen SHA256 for
every selected file. These SHA256 values bind bytes observed from the original
archive; they are not publisher-provided per-file SHA256 attestations. The
existing catalogue hash, source version, publisher metadata and license are
verified before downloading any selected member. ZIP local headers and CRC32,
expanded length and expected project SHA256 are checked before immutable
publication and again on readback. A corrupt existing cache fails without
repair. Symlink cache paths are refused.

Limits are 1 MiB per expanded file, 8 MiB total, 200,000 decoded lines per file,
32,000,000 archive response-body bytes and 256 source calls. Only bounded HTTP
206 ranges are accepted; ignored full-download responses fail. No publisher
script or binary is executed. Numeric summaries and source paths are emitted;
raw log text, request URLs, identifiers and signed download leases are excluded.

## Interpretation fixed before final measurement

One printed request total and its explicitly suffixed elapsed duration are
parsed per log. Recorded histogram totals, socket-error categories and printed
non-2xx/3xx totals remain distinct. Missing error lines stay unavailable; socket
errors and non-2xx/3xx responses are not an HTTP 5xx numerator. Whole-run
histograms are not 60-second bucket histograms.

The companion integer stream is checked as one unsigned integer per source
line. Its sample count is compared with the printed request total. No unit,
request timestamp, sampling scheme, status, temporal ordering, percentile
meaning or exporter version is inferred from the filename or numeric scale.
The filename `warm_up` does not certify operational health. Native request
types are not aliases for Checkout, Payment or Inventory.

The output accounts for all 24 capture pairs and reports `NOT_READY` for G2/G3
when the required event-time, per-bucket denominator, HTTP status, source/live
mapping and independent healthy/precursor evidence is unavailable. Existing
300/100 healthy train/calibration minima and independent-run requirements stay
in force. This four-group capability result is not a statement about every
possible GAMMA modality.

## Reproduction

Use a clean committed checkout with the ML/dev environment. Obtain the frozen
catalogue first if it is absent:

```bash
.venv-ml/bin/python scripts/inventory_gamma_archive.py \
  --file-lock configs/gamma_v1_archive_catalog.json \
  --result-file artifacts/research/gamma-client-catalog.json
.venv-ml/bin/python scripts/audit_gamma_clients.py --download \
  --output artifacts/research/gamma-clients/run-1.json
.venv-ml/bin/python scripts/audit_gamma_clients.py \
  --output artifacts/research/gamma-clients/run-2.json
```

Output paths are exclusive. Without `--download`, the audit is offline and
requires all 48 verified cache files. Repeated offline outputs at the same
source commit and context must be byte identical. A download run additionally
records transport/cache accounting. CI acquires the exact source members and
uploads the capability report with the existing public-data artifact.

The upstream [wrk2 documentation](https://github.com/giltene/wrk2) explains its
aggregate HdrHistogram output and emulated Lua statistics API. Similar printed
headings provide format context; they do not identify the GAMMA exporter
binary, custom scripts or the meaning of these separate integer files.
