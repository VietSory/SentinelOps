# Foundation Milestone — Definition of Done

Foundation is complete only when all items below are true:

- [ ] `docs/PRODUCT_SPEC.md` is accepted as the implementation source of truth.
- [ ] All JSON Schemas pass JSON Schema validation.
- [ ] Contract-valid samples exist for telemetry, feature window, and Incident Evidence Record.
- [ ] `/health` returns HTTP 200.
- [ ] `/v1/detect` validates input and returns a contract-valid skeleton detection result.
- [ ] `/v1/incidents` accepts a contract-valid Incident Evidence Record.
- [ ] `scripts/replay.py` accepts an external telemetry JSONL scenario.
- [ ] CI runs schema validation and tests on every PR.
- [ ] Breaking-change policy is documented with ADRs.
- [ ] No AWS credentials, secrets, datasets, model binaries, or local artifacts are committed.

After this gate passes, the next milestone is **Experimental Microservices & Observability**.
