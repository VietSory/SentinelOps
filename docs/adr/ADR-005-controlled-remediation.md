# ADR-005 — Controlled remediation

**Status:** Accepted

Automated remediation is allowed only for explicitly approved runbooks.

Every automated execution follows:

`Policy Check -> Dry Run / Safety Check -> Execute -> Verify by telemetry -> Recovered OR Rollback/Escalate -> Audit`

Mandatory safety properties:

- allow-list;
- least privilege;
- explicit blast-radius limits;
- idempotency/cooldown;
- verification from post-action telemetry;
- rollback/escalation path;
- complete audit record.

Kubernetes native restart/HPA behavior by itself is not considered SentinelOps closed-loop remediation.
