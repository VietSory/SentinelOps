# ADR-001 — System responsibility boundaries

**Status:** Accepted  
**Decision:** SentinelOps is a staged AIOps pipeline, not an end-to-end autonomous agent.

The stable responsibility split is:

- telemetry/feature pipeline supplies normalized evidence;
- Isolation Forest detects anomalies;
- XGBoost scores degradation risk;
- RCA Engine ranks suspected root causes using topology, traces, temporal order and log evidence;
- Incident Evidence Record is authoritative;
- LLM explains verified facts only;
- remediation executes approved runbooks only and must verify the result.

Changing these responsibilities is a breaking architecture change.
