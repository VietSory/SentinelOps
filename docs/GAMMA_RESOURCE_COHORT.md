# Predeclared complete raw-resource cohort

The [scope configuration](../configs/gamma_resource_cohort_scope.json) selects **all 194 raw experiment directories containing `args.txt`** in the frozen GAMMA v1 archive catalogue. Selection was declared using member metadata before cohort extraction, independent of model scores and processed binary labels. Four directories have no processed table; they remain in acquisition rather than being silently removed. This does not assert that every acquired case is usable for training.

Within those directories, acquire the four previously registered raw counter/gauge metrics, native VM phase logs and per-experiment pod descriptions. Include the four global placement/RPC maps for provenance/comparison. Exclude processed request tables, request/trace graphs, application/node logs, unrelated metrics and provider scripts. The source pair, source version, target and live feature registry remain unchanged. No RCAEval data enters this training acquisition.

## Transfer and storage

The [bounded ZIP64 transport](GAMMA_ARCHIVE_ACQUISITION.md) validates the complete archive against its project lock. After the first canonical API redirect, it reuses the successful object-download URL privately in memory, avoiding one Kaggle API request per range. That ephemeral signed lease never becomes public source identity or persisted report/config data. Exact range/representation checks still apply to every object response. Selected physical member spans run from their local header to the next original member header, with explicit compressed-size/local-header bounds. Small nearby spans are coalesced with fixed maximum gap/block sizes. A preflight sum and thread-safe byte reservations bound all response bodies, including metadata and coalescing overhead. At most four requests are in flight; a failure leaves only that bounded batch outstanding rather than queuing the entire source.

Python's ZIP reader checks local headers, filenames, decompressed length and CRC before any member is admitted. Selected nonregular/encrypted data is refused. Source member bytes remain unchanged, including empty files; empty does not mean a valid zero-load series. Metadata/CRC are transport checks, not independently published SHA256.

To avoid tens of thousands of small writes, original member bytes are packaged in **deterministic project ZIP shards**, one per experiment plus one global-metadata shard. These are source-preserving storage containers, not the publisher's original archive. Fixed ZIP metadata and sorted original paths make independent packaging reproducible. No rows or systems are joined; original native paths keep the experiment boundary. Shards use stored members and immutable content-addressed paths.

A canonical source file tree hashes every selected member's native path, role, size and SHA256. A small project lock anchors this complete tree without committing a large raw file inventory. The raw manifest records every member, shard hash, source/policy/scope/catalogue identity and clean code commit. A changed tree prevents publication of a successful manifest. Previously published immutable shards may remain as incomplete evidence if a later acquisition step fails.

The manifest reader verifies itself, the exact predeclared catalogue selection, root-contained shard paths, shard/member hashes, original CRC/size metadata and full file-tree coverage. It refuses omitted/extra/duplicate data and corrupt shards. Processing reads a verified shard into memory rather than extracting arbitrary archive paths. The reader and downstream code do not execute original source files.

## Admission remains separate

Acquisition reports per-case role presence and missing-role lists; it does not label absent phase logs as healthy. Some experiments have multiple pod descriptions per entity, CPU/memory-specific phase filename prefixes or incomplete modalities. Per-experiment placement, clock bounds, native event parsing, source-reference admission, observed session grouping and grouped splits must be verified before windowing/fitting. The initial single-experiment phase audit is not silently reused as certification of all these variants.

Global placement is retained as provenance; it is not assumed to describe every later pod deployment. Numeric features continue to use the existing causal raw-resource implementation and its unchanged warmup/coverage policy. Current intervention labels are not future SLO/gradual precursor targets. Full acquisition does not close G2/G3/G7 or permit live activation.

```bash
.venv-ml/bin/python scripts/download_gamma_cohort.py \
  --result-file artifacts/research/gamma-resource-cohort-acquisition.json
```

The CLI refuses a dirty checkout before acquisition; commit code and scope first. The first measured acquisition establishes the file-tree lock; subsequent reproduction supplies `--file-tree-lock configs/gamma_resource_cohort_files.json`. Result files are exclusive, and raw manifests/shards remain ignored artifacts. This is real publisher acquisition; small synthetic transport fixtures are used only in unit tests.
