# Bounded GAMMA archive inventory

The publisher's canonical GAMMA v1 archive supports HTTP byte ranges. Its ZIP64 central directory can supply the complete member inventory without transferring every large processed table or paginating through the entire raw file catalogue. This keeps acquisition within the frozen source policy; no replacement mirror, additional corpus or provider code is executed.

The seekable reader accepts exact **206** responses with matching `Content-Range`, stable total representation length and stable ETag presence/value across requests. It requests identity content encoding, rejects truncated/oversized responses, and closes an ignored-range **200** response before consuming its body. Per-request and cumulative response-body budgets apply before/while reading. Bounded cache reuse avoids repeated metadata ranges. Byte/body counts exclude HTTP headers and protocol overhead. These checks follow the range and representation semantics in [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-14).

Python's standard ZIP reader parses ZIP64 offsets and entry counts over this seekable transport. Catalogue admission rejects excessive entry counts, unsafe/noncanonical or duplicate paths, conflicting offsets, sizes outside the data region, encryption and unsupported compression. Listing a script path is metadata only; the inventory does not extract or execute members. Unit verification includes a real 65,536-entry ZIP64 archive, avoiding the classic 65,535-entry truncation.

The canonical complete catalogue records names, expanded/compressed sizes, CRC32, compression and physical offsets. It is stored as an immutable content-addressed ignored raw inventory. A small project lock freezes its SHA256, source/version/policy identity, archive length and entry count. Publisher metadata must remain unchanged before/after inventory. A changed locked catalogue fails rather than silently updating the source snapshot. ZIP CRC32 metadata and project hashes do not constitute publisher-supplied file SHA256 or complete-archive cryptographic verification.

```bash
.venv-ml/bin/python scripts/inventory_gamma_archive.py \
  --result-file artifacts/research/gamma-archive-inventory.json
```

The CLI requires committed code before freezing evidence. A later replay supplies `--file-lock configs/gamma_v1_archive_catalog.json` once the measured lock is committed. Output reports use exclusive creation. Signed redirects and pagination tokens are not persisted in catalogue/report files; only the canonical version-pinned API URL is recorded.

This is complete **member inventory**, not complete data verification, a frozen training split or a model result. The next acquisition slice must select raw counter/gauge series, native placement and independent interference evidence by an explicit scope; bound compressed/expanded content, check local headers and CRC during decompression, and freeze per-file SHA256 before downstream processing. Source experiments still require observed-session grouping, reference sufficiency and split admission. The live HTTP feature contract and pre-SLO gradual target remain unchanged.
