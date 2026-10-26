# Dataset admission — raw replay and evidence lineage

The first loader checked immutable run/log/feature objects and independently recomputed labels, but did not reconstruct features from all raw captures. A caller-authored feature could therefore have a valid hash without proving it came from the recorded normalization source.

The loader now reads/checksums the normalization manifest and all referenced captures/observations/features/quarantine records, reruns raw→feature processing and requires the exact original manifest reference. Aggregate source fields must match that normalization revision. Serving evidence cannot contain experiment control/ground-truth fields. Log/infrastructure links must agree with run truth; infrastructure semantics/environment are checked.

Every retained trace must link to its business completion logs, with matching correlation IDs, status and server span IDs. Shared trace validation checks Checkout SERVER→CLIENT→downstream SERVER parentage; readiness probes cannot substitute for all three business routes. The same parentage/status validator is reused by the Step 3 smoke, without changing its existing behavior.

## Executed evidence

- **158 tests passed**. Negative file-backed tests reject missing raw data, independently hash-valid fabricated features, injected control truth, probe-only traces and disconnected parentage. Existing independent-log label-tampering checks still fail as intended.
- Actual S0 `run-18d10ead1fd549298306b837dc1266a3`: raw replay and full retained trace/log lineage **PASS**, 30 feature objects.
- Actual S3 `run-94c146e96e3f4ae0a0c5e98755db3550`: same checks **PASS**, 60 feature objects.
- Existing immutable pilot dataset IDs/objects are preserved. This admission fix does not add data, change labels, lower counts or close G2.

Raw source bodies are trusted capture artifacts; checksums/replay establish integrity and derivation, not cryptographic proof that an external source was honest. Quality coverage, independent-run counts and model/heldout metrics remain separate acceptance requirements.
