# Protected incident API persistence

The HTTP incident endpoints now use the [real control repository](STEP7_INCIDENT_STORE.md). The seven original wire schemas remain unchanged: IER responses have the exact legacy body; revisions and conditional-write metadata use headers or supplemental contracts. This is a deliberate migration from Foundation echo/mock behavior, not completion of RCA/model/recovery gates.

## Local setup

Start the dedicated control database as described in the storage guide. Export `CONTROL_DATABASE_URL` using the nonowner runtime role and set `SENTINELOPS_API_TOKEN` to a random local secret of at least 32 characters. Start `uvicorn sentinelops.main:app --host 127.0.0.1 --port 8000`. Environment variables are read from the process; the API does not automatically source `.env` files.

All incident read/write endpoints require `Authorization: Bearer TOKEN`. This adapter is for local/test only. Other environments fail closed until a production identity adapter is implemented; a static sandbox token is not an AWS authentication acceptance. The server supplies the actor identity; a request cannot choose an audit actor. Tokens/DSNs are not returned in errors or logged by request error handling.

`/health` is liveness. `/ready/incidents` checks authenticated storage readiness, schema version and runtime privileges. `/ready` remains 503 while the real model dependency is missing, even when incident storage is ready. A database outage returns 503 while liveness stays 200; the pool reconnects after restoration.

## Create and update

`POST /v1/incidents` accepts the **original IER body**, plus:

```text
Authorization: Bearer TOKEN
If-Match: "0"
Idempotency-Key: stable-operation-id
```

Success is 201 with the exact persisted IER, `Location`, `ETag: "1"` and `X-Incident-Revision: 1`. An exact retry returns that original revision. This endpoint imports a trusted producer's assertion with explicit `manual` trigger provenance and availability UNKNOWN; it does not recompute RCA or claim healthy telemetry. Submitted/detected time is the import's claimed source emission time; database `recorded_at` is server time. Server-computed anomaly/risk/SLO first-emission measurements remain separate producer work. A missing external evidence reference falls back to a content-addressed reference to the stored submitted IER, not invented external evidence.

`POST /v1/incidents/{id}/revisions` accepts [incident-write v1](../contracts/incident-write.schema.json): `schema_version`, exact `payload`, supplemental `context`, `lifecycle_stage` and `reason`. Supply the quoted expected current revision in `If-Match` and a stable idempotency key. The actor comes from authentication. Path/payload IDs must agree. Stale updates or changed contents under a reused key return 409; missing expected revision is 428; invalid input/key is 422.

The import API currently permits OPEN/RCA_READY/ESCALATED writes only. Explanation/action/recovery stages return 503 until their actual validated writers are integrated. A token cannot manufacture a successful action or recovery verification by selecting a lifecycle stage. Imported rankings remain producer assertions, not measured RCA accuracy or authorization to execute a runbook.

Stored IDs are bounded URI-safe ASCII identifiers (`A–Z`, `a–z`, digits, `.`, `_`, `:`, `-`, at most 256 characters). Whitespace/control characters, slashes and unbounded IDs are rejected for storage. This persistence constraint is documented rather than changing the legacy schema silently.

## Reads and pagination

| Endpoint | Result |
| --- | --- |
| `GET /v1/incidents/{id}` | Exact current IER; optional positive `revision` reads history; ETag/revision headers |
| Same with `If-None-Match` matching ETag | 304 |
| `GET /v1/incidents/{id}/context` | [IncidentEnvelope](../contracts/incident-envelope.schema.json), including exact IER, provenance, actor/reason and bounded audit timeline |
| Context `revision`, `after_revision`, `limit` | Revision-bounded history page; follow `timeline.next_after_revision` |
| `GET /v1/incidents?state=…&service=…&limit=…&cursor=…` | [IncidentList](../contracts/incident-list.schema.json) summaries; follow opaque `next_cursor` |

Limits are 1–100. Incident ordering uses immutable creation time plus ID; cursors bind filters and use keyset pagination. It is a live current-state view: concurrent updates can change which records match a filter; it is not a database snapshot across HTTP pages. Historical context never includes events from later revisions.

Requests are bounded to 1MB even for chunked bodies; excessive bodies return 413. Errors use [ApiError](../contracts/api-error.schema.json), include a server request ID matching `X-Request-ID`, and hide database exceptions/credentials. Responses disable caching by default.

Supplemental schema `$ref`s resolve the original IER from installed package resources through a local-only registry. They do not duplicate its definition or fetch arbitrary network/file URLs. The registry follows the [jsonschema referencing API](https://python-jsonschema.readthedocs.io/en/stable/referencing/).

## Mock migration and validation

Default `POST /v1/detect` returns 503 when no validated model is active. Default `/v1/incidents/demo` is disabled. For local fixture consumers only, `SENTINELOPS_ENABLE_DEMO=true` in local/test enables the old skeleton output and nonpersisted demo IER; both responses carry `X-SentinelOps-Mode: demo`. Demo mode is rejected outside the sandbox. It cannot make readiness/model quality pass.

`python scripts/check_incident_api.py --restart-database` launches a real Uvicorn process, uses HTTP over loopback against real PostgreSQL, verifies persistence/idempotency/CAS/ETags, historical context, filtered pagination, streamed body limits and denied unintegrated stages, then stops/restarts the dedicated control database and verifies outage/reconnection. It generates its own ephemeral token and terminates the API in `finally`. All inserted IER contents are explicitly synthetic validation fixtures. The control-storage CI job runs this path after repository acceptance.
