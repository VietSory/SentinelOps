# Incident API persistence — local acceptance

Status: **HTTP/storage acceptance PASS; G3/G4 not complete**. Tests use explicitly synthetic imported IERs and establish persistence/contract behavior, not RCA accuracy or action safety.

## Executed checks

- **153** unit/regression tests passed; **16 schemas / 12 samples** validated; isolated wheel build/install/runtime check passed, including nested schema references outside the checkout. Original seven wire schemas have no diff from baseline `20edcae`.
- Launched a real Uvicorn process and used HTTP over loopback with the actual nonowner PostgreSQL runtime role (`scripts/check_incident_api.py --restart-database`). No in-memory storage or ASGI-only substitute was used for this acceptance.
- Authentication, unavailable-model 503 and disabled-by-default demo paths behaved as documented.
- Created three persisted synthetic incidents. Creation/retry kept the exact legacy IER body; changed contents under a reused key conflicted. A revision update reached 2; a stale update conflicted; ETag conditional read returned 304.
- Historical context stayed revision-bounded. Timeline pagination, filtered keyset list pagination, cursor/filter mismatch and malformed/oversized limits were checked.
- A streamed request exceeding 1MB returned 413. Unsupported explanation/action/recovery writers were denied.
- Stopped the real control database: protected readiness returned 503 while liveness stayed 200. Restarted it while the API process remained running: pool/readiness recovered and the same payload was read back.
- Persisted snapshots are validated against nested IER/context semantics and their request fingerprint before reads. Corrupt context/identity/JSON/fingerprint tests fail closed; unexpected exception tests expose neither DSNs nor secret text in HTTP/logs and preserve request identity.

Local evidence: `artifacts/incidents/api-acceptance.json` and `api-server.log` (ignored); the script generates an ephemeral token and stops the API in `finally`. CI runs the real HTTP/database/restart path after repository acceptance. A later green CI result must be tied to its actual commit; local execution alone does not assert that CI already ran these changes.

## Migration and limits

See [the API migration guide](../runbooks/INCIDENT_API.md) for headers, status codes, supplemental schemas, local configuration and fixture opt-in. Existing clients must supply authentication/expected revision/idempotency for persistence. Detector fixtures no longer look like default inference.

Production identity, model bundles, correlation/RCA producers, verified explanation/action/recovery writers and AWS remain separate gates. Imported/manual assertions do not certify source truth or grant remediation authority. Database administrators retain privileged access; append-only role checks are not a claim against an owner/admin compromise.
