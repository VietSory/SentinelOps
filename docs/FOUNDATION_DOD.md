# Foundation Milestone — Definition of Done

Local engineering verification was completed on 2026-10-26; see [baseline evidence](reports/2026-10-26-baseline-repaired.md). These checks do not certify AWS, models or later phases. Foundation criteria:

- [x] `docs/PRODUCT_SPEC.md` is accepted as the implementation source of truth.
- [x] All JSON Schemas pass JSON Schema validation.
- [x] Contract-valid samples exist for telemetry, feature window, and Incident Evidence Record.
- [x] `/health` returns HTTP 200.
- [x] `/v1/detect` validates input and returns a contract-valid skeleton detection result.
- [x] `/v1/incidents` accepts a contract-valid Incident Evidence Record.
- [x] `scripts/replay.py` accepts an external telemetry JSONL scenario.
- [x] CI runs schema validation and tests on every PR.
- [x] Breaking-change policy is documented with ADRs.
- [x] No AWS credentials, secrets, datasets, model binaries, or local artifacts are committed.

After this gate passes, the next milestone is **Experimental Microservices & Observability**.
