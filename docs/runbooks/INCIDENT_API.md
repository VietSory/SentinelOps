# Protected incident API persistence

## Read-only operator UI

The API serves the operator console at `/ui/` from packaged same-origin assets.
For lane B, follow [the isolated runtime](LOCAL_TESTBED.md#lane-b-isolated-application-runtime)
and open `http://127.0.0.1:18000/ui/`. Enter the local operator token from the private
runtime file, or an OIDC access token accepted by the configured API. Tokens stay in tab memory, are
cleared from the input after connecting and are never persisted in browser storage.
Disconnect clears incident state. No incident writes, fault controls or action
execution are exposed by this UI.

The visible tab checks both public `/ready` and protected `/ready/incidents`
on connect/refresh, every 30 seconds, and when the tab becomes visible again.
Public configuration readiness alone does not verify the bearer token. An observed
401 or 403 `insufficient_scope` disconnects and clears records, explanations,
actions and pending requests; a late denial from a prior connection cannot clear
a newer session. A public-probe network error cannot mask a protected denial.
The browser does not decode token claims or decide their authority itself.

A provider/JWKS outage is availability failure, not a known permission rejection:
the UI retains previously loaded records with an explicit readiness error and
unknown dependency status. When both readiness endpoints report a store outage,
the existing unavailable-store state remains visible. Neither case establishes
service health or grants action/model authority. A real provider deployment is
still separate from these software checks.

The feed uses the existing state/service/cursor API; loaded-row counts are not
global incident counts. Detail pins a revision of the IncidentEnvelope and its
exact IER. Evidence and timeline pages retain that revision; an explicit Latest
revision action selects a newer snapshot. References are displayed/copied as
opaque identifiers, not automatically fetched URLs. Recorded times are not inferred
latency measurements. There is no server-side time-range filter.

Store/model readiness are independent. Missing model signals stay unavailable,
UNKNOWN does not become healthy, and a store outage does not become an empty
successful feed. RCA entries are hypotheses/support; the current API does not
expose all structured qualification fields, so the console never presents ranking
as a confirmed cause. The Explanation tab reads the pinned revision through the
real grounded backend, shows provider/fallback and links each claim to this
revision's evidence. Switching revision/filter or disconnecting clears the old
result and cancels its request; late responses cannot restore it. Reload errors
remove the previous explanation while incident/evidence remain readable. Text,
including quoted logs, is rendered as text. Action execution remains unavailable.

Frontend tests need Node 20+ but the running UI has no Node/build service:

```sh
node tests/ui/workspace.test.mjs
python -m pytest -q tests/test_operator_ui.py tests/test_api.py tests/test_incident_api_contracts.py
python scripts/check_installed_package.py
```

Optional browser checks use `pip install -e '.[ui-test]'` and installed Chrome or
Playwright Chromium. `--browser-executable` selects an existing browser. Fixture
mode intercepts browser API reads and is explicitly software evidence only:

```sh
python -m scripts.check_operator_ui --fixtures --output artifacts/lane-b/ui-fixtures
python -m scripts.check_operator_ui --incident-id <actual-SLO-incident-id> \
  --output artifacts/lane-b/ui-native
```

Native mode reads an actual persisted SLO incident via protected HTTP and verifies
its revision/evidence/history on desktop/mobile; it never creates an IER. Outputs
are exclusive and never overwrite earlier verification. Tokens and browser request
traces are not written to artifacts. A screenshot of fixture records is not live
source or model/RCA quality evidence.
The checker compares the model badge with explicit API availability, so both
no-model and selected-model deployments are supported. This checks a loaded
dependency, not diagnostic quality, fresh inference or direct `/detect` support.

The HTTP incident endpoints now use the [real control repository](INCIDENT_STORE.md). The seven original wire schemas remain unchanged: IER responses have the exact legacy body; revisions and conditional-write metadata use headers or supplemental contracts. This is a deliberate migration from Foundation echo/mock behavior, not completion of RCA/model/recovery gates.

## Local setup

Start the dedicated control database as described in the storage guide. Export `CONTROL_DATABASE_URL` using the nonowner runtime role and set `SENTINELOPS_API_TOKEN` to a random local secret of at least 32 characters. Start `uvicorn sentinelops.main:app --host 127.0.0.1 --port 8000`. Environment variables are read from the process; the API does not automatically source `.env` files.

All incident read/write endpoints require `Authorization: Bearer TOKEN`. Static tokens are for local/test only: outside those environments the settings refuse them at startup, and the API fails closed (503 `authentication_adapter_unavailable`) unless OIDC is configured (see [OIDC](#oidc-for-non-local-environments)). The server supplies the actor identity; a request cannot choose an audit actor. Tokens/DSNs are not returned in errors or logged by request error handling.

Two distinct local credentials are supported:

| Environment variable | Allowed incident operations |
| --- | --- |
| `SENTINELOPS_OPERATOR_TOKEN` | Read incident list/detail/context/explanation and optional action observations/readiness |
| `SENTINELOPS_API_TOKEN` | Existing trusted producer: the same reads, plus IER import/revision writes under existing preconditions and lifecycle restrictions |

Each configured credential needs at least 32 characters; equal operator/producer
values are rejected. An operator-only API is supported: reads work, producer
writes remain forbidden. A valid operator attempting either incident write gets
403 `read_only_credential` before repository access; missing/wrong bearer identity
is 401. Caller-supplied role/actor headers cannot change scope. No credential
approves models, qualifies RCA or grants remediation authority. Stateless inference
and explicit demo routes retain their existing contracts and model/demo gates.

The B launcher creates separate private 0600 `artifacts/lane-b/operator-token`
and `api-token` files, preserving existing values. Use the operator file for UI;
the producer token retains its legacy read/write scope. Explicit environment
overrides select the respective credential. No token is included in bundles,
public status or browser storage. These remain static local credentials: rotate
them by restarting the API with the new configuration and reconnect the tab.
Managed releases require explicit stop/start to resnapshot credentials; a normal
restart reuses the pinned private session.

### OIDC for non-local environments

The API verifies signed JWT access tokens from any OIDC provider (Amazon Cognito,
Okta, Entra ID, Keycloak) against the provider's JWKS. Static tokens and OIDC cannot
be configured together.

| Environment variable | Meaning |
| --- | --- |
| `SENTINELOPS_OIDC_ISSUER` | Required. Exact `iss`, https |
| `SENTINELOPS_OIDC_AUDIENCE` | Required. Expected audience |
| `SENTINELOPS_OIDC_JWKS_URL` | Required. https JWKS document (no redirects, at most 64 KiB) |
| `SENTINELOPS_OIDC_AUDIENCE_CLAIM` | `aud` (default, resource-bound tokens per RFC 9068) or `client_id`: only for unbound Cognito access tokens, which must have `token_use=access`, no `aud` and a matching `client_id` |
| `SENTINELOPS_OIDC_PRODUCER_SCOPE` | Default `sentinelops/incidents.write`: producer role (reads + IER writes) |
| `SENTINELOPS_OIDC_OPERATOR_SCOPE` | Default `sentinelops/incidents.read`: read-only operator role |

Only RS256/ES256 keys marked for signing are used, and the token's `kid` and `alg` must match a
published key. `exp`, `iat`, `iss` and `sub` are required, with 60 s leeway. `token_use`, if present,
must be `access`. Scopes come from `scope` (space separated) or `scp` (list of strings); claims of the wrong JSON shape get 401, not a server error. A token with
the producer scope gets the producer role; with only the operator scope, the operator role. A valid token
with neither scope gets 403 `insufficient_scope`, and an invalid one gets 401. The audit actor is
`oidc-<role>:<sub>`. Keys are cached for 5 minutes. An unknown `kid` forces at most one refresh
per 30 s (key rotation), and a failed fetch backs off for 30 s. Old keys keep verifying for up
to four cache lifetimes without a successful refresh; after that, and before the first successful
fetch, requests get 503 `authentication_unavailable`. `/ready` reports
`authentication=true` once OIDC is configured; it does not contact the provider.

Provider setup (user pool, app client, resource server scopes) and its IaC belong
to the AWS phase and are not applied here.

`/health` is liveness. `/ready/incidents` checks authenticated storage readiness, schema version and runtime privileges. `/ready` remains 503 while the real model dependency is missing, even when incident storage is ready. A database outage returns 503 while liveness stays 200; the pool reconnects after restoration.

## Create and update

`POST /v1/incidents` accepts the **original IER body**, plus:

```text
Authorization: Bearer TOKEN
If-Match: "0"
Idempotency-Key: stable-operation-id
```

Success is 201 with the exact persisted IER, `Location`, `ETag: "1"` and `X-Incident-Revision: 1`. An exact retry returns that original revision. This endpoint imports a trusted producer's assertion with explicit `manual` trigger provenance and availability UNKNOWN; it does not recompute RCA or claim healthy telemetry. Submitted/detected time is the import's claimed source emission time; database `recorded_at` is server time. Server-computed anomaly/risk/SLO first-emission measurements remain separate producer work. A missing external evidence reference falls back to a content-addressed reference to the stored submitted IER, not invented external evidence.

`POST /v1/incidents/{id}/revisions` accepts [incident-write v1](../../contracts/incident-write.schema.json): `schema_version`, exact `payload`, supplemental `context`, `lifecycle_stage` and `reason`. Supply the quoted expected current revision in `If-Match` and a stable idempotency key. The actor comes from authentication. Path/payload IDs must agree. Stale updates or changed contents under a reused key return 409; missing expected revision is 428; invalid input/key is 422.

The import API currently permits OPEN/RCA_READY/ESCALATED writes only. Explanation/action/recovery stages return 503 until their actual validated writers are integrated. A token cannot manufacture a successful action or recovery verification by selecting a lifecycle stage. Imported rankings remain producer assertions, not measured RCA accuracy or authorization to execute a runbook.

Stored IDs are bounded URI-safe ASCII identifiers (`A–Z`, `a–z`, digits, `.`, `_`, `:`, `-`, at most 256 characters). Whitespace/control characters, slashes and unbounded IDs are rejected for storage. This persistence constraint is documented rather than changing the legacy schema silently.

## Reads and pagination

| Endpoint | Result |
| --- | --- |
| `GET /v1/incidents/{id}` | Exact current IER; optional positive `revision` reads history; ETag/revision headers |
| Same with `If-None-Match` matching ETag | 304 |
| `GET /v1/incidents/{id}/context` | [IncidentEnvelope](../../contracts/incident-envelope.schema.json), including exact IER, provenance, actor/reason and bounded audit timeline |
| Context `revision`, `after_revision`, `limit` | Revision-bounded history page; follow `timeline.next_after_revision` |
| `GET /v1/incidents/{id}/explanation?revision=N` | [ExplanationResponse v1](../../contracts/explanation-response.schema.json), with the unchanged GroundedExplanation nested in `payload`; provider/fallback and generation time outside IER |
| `GET /v1/incidents?state=…&service=…&limit=…&cursor=…` | [IncidentList](../../contracts/incident-list.schema.json) summaries; follow opaque `next_cursor` |

Limits are 1–100. Incident ordering uses immutable creation time plus ID; cursors bind filters and use keyset pagination. It is a live current-state view: concurrent updates can change which records match a filter; it is not a database snapshot across HTTP pages. Historical context never includes events from later revisions.

Explanation uses the same bearer authentication and repository as incident reads.
Omitting `revision` resolves current once and pins subsequent history/provider
work to that immutable revision. Response `X-Incident-Revision` identifies it.
Responses are `no-store`, without an IER ETag or 304: generation time and optional
provider selection can vary. The GET neither writes a revision/outbox event nor
transitions to EXPLAINED. Evidence refs must resolve in the selected IER.

The mounted default app calls A's [backend](EXPLANATIONS.md) without a provider:
`provider=deterministic-facts/v1`, `fallback=true`. It works while models are 503.
An explicitly supplied `ExplanationProvider` in `create_app(explanation_provider=...)`
can select bounded evidence; invalid/timeout selection falls back on the same
revision. No SDK, AWS credentials, provider environment switch or cloud request is
enabled automatically. The browser allows 15 seconds for the backend's bounded
ten-second provider deadline plus HTTP overhead. An absent revision is 404,
invalid query 422, unavailable DB or unverifiable output 503. A summary of source
facts is not independent verification, qualified RCA, action authority or G5.

Optional action observations use a **separate SELECT-only** credential via
`SENTINELOPS_ACTION_READ_DATABASE_URL`, explicit optional migration and
`control_action_reader` group. [Action HTTP/UI setup](CONTROLLED_REMEDIATION.md#read-only-action-http-and-ui)
documents `/ready/actions`, `/v1/actions/{id}` and
`/v1/incidents/{id}/actions?revision=N`. Action pages allow 1–25 items and preserve
the pinned incident revision; their action audit stays separate from strict IER
and incident lifecycle. The UI Actions tab reads these routes; missing storage is
unavailable, not proof that no action happened. There is no public dispatch API.

After optional action migration 2, `GET /v1/actions/{id}/quarantine` returns
[quarantine-status/v1](../../contracts/quarantine-status.schema.json), with a
content ETag independent of the unchanged action revision. It observes the claim
or explicit operator receipt for that action, never changes the incident lifecycle
or authorizes dispatch. Missing migration/data is 503. The Actions UI can view the
receipt but cannot release a lock. Only the separate operator CLI/credential may
perform [guarded manual review](CONTROLLED_REMEDIATION.md#explicit-operator-quarantine-review).

Requests are bounded to 1MB even for chunked bodies; excessive bodies return 413. Errors use [ApiError](../../contracts/api-error.schema.json), include a server request ID matching `X-Request-ID`, and hide database exceptions/credentials. Responses disable caching by default.

Supplemental schema `$ref`s resolve the original IER from installed package resources through a local-only registry. They do not duplicate its definition or fetch arbitrary network/file URLs. The registry follows the [jsonschema referencing API](https://python-jsonschema.readthedocs.io/en/stable/referencing/).

## Mock migration and validation

Default `POST /v1/detect` returns 503 when no validated model is active. With the ADR-016 Alibaba release, `POST /v1/detect` and `POST /v1/risk` return 409 `model_requires_stream_history`: the relative model needs accepted v2 stream history that one FeatureWindow cannot carry. Read the worker's model results as `service_signals` in the IER: list with `GET /v1/incidents?service=<route>` (summaries only), then fetch `GET /v1/incidents/{id}?revision={revision}`. `/ready` reports `direct_inference=false`; scores from healthy periods, which open no incident, are not exposed. Default `/v1/incidents/demo` is disabled. For local fixture consumers only, `SENTINELOPS_ENABLE_DEMO=true` in local/test enables the old skeleton output and nonpersisted demo IER; both responses carry `X-SentinelOps-Mode: demo`. Demo mode is rejected outside the sandbox. It cannot make readiness/model quality pass.

`python scripts/check_incident_api.py --restart-database` launches a real Uvicorn process, uses HTTP over loopback against real PostgreSQL, verifies persistence/idempotency/CAS/ETags, historical context, filtered pagination, streamed body limits and denied unintegrated stages, then stops/restarts the dedicated control database and verifies outage/reconnection. It generates its own ephemeral token and terminates the API in `finally`. All inserted IER contents are explicitly synthetic validation fixtures. The control-storage CI job runs this path after repository acceptance.
