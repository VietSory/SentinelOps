# Controlled Payment rollback — component status

[ADR-009](../adr/ADR-009-payment-rollback-runbook.md) remains the authority for
`payment-rollback-known-good/v1`. Internal policy/known-good verification,
Kubernetes adapter, PostgreSQL journal and bounded executor are implemented.
There is **no enabled production H4 provider, approved real known-good release,
public dispatch endpoint or closed-loop acceptance**. A resume-only worker and
authenticated read-only action observations/UI are available. No process here
creates new actions automatically. No AWS resources are required for this work.

## Executor and durable journal

`remediation/executor.py` coordinates server-configured dependencies; a `Request`
contains only incident ID/revision and idempotency key. Clients cannot choose an
image, target, policy, authority provider or approval flag. Both policy and kill
switch default disabled. `NativeH4Authority` actually reproduces accepted native
RCA evidence but returns no dispatch authority, including when mathematical RCA
qualification alone passes. An agreed H4 producer/consumer contract and physical
Kubernetes-to-health source binding are still required before live activation.
Only software tests inject a qualified fixture authority; no force/demo grant
exists in a serving route or command.

The local policy `payment-rollback/local-v1` binds exact cluster, namespace and
Deployment UIDs to `sentinelops-b-actions/payment-service`, container `payment`.
It permits one replica/existing pod, zero surge, no HPA or paused Deployment,
600-second verification/cooldown, 180-second evidence freshness and a matching
observed deployment change within 600 seconds before source incident onset.
Frozen RCA weights/score/coverage/margin remain unchanged. The adapter uses an
explicit loopback HTTPS server, CA and private ServiceAccount token; it never
reads ambient kubeconfig or AWS credentials. JSON Patch tests UID, unmodified
resourceVersion, generation, replicas, container name and previous image, then
replaces only that image with a distinct same-repository immutable digest.
Server-side `dryRun=All` must succeed before arming.

`KnownGoodRegistry` reads a separately operator-selected content-addressed
`approved-payment-images/v1` ledger under the strict new schema. It does not
generate approvals. The trusted approver/reference are server configuration;
checksums are not authentication of arbitrary remote writers. Ready Deployment
observations must surround a reproducible raw-verified healthy interval with
unchanged generation/pod UIDs/image, correct target/environment and approval
before incident onset/change. The registry replays accepted health instead of
trusting an uploaded success flag. This approves an image only, never dispatch.
Registry, authority, recovery source/health policy and target bindings must match;
the intent pins them for later reconciliation. Those identity checks do not
replace the outstanding H4/source-parity review of actual Kubernetes telemetry.
The diagnosed deployment change must match the current bad-image generation;
verification accepts exactly its next generation for the image-only patch.
An intervening external spec change escalates instead of being attributed to the rollback.

The explicit optional bootstrap migration
[`005-remediation.sql`](../../infra/control/005-remediation.sql) adds the separate
`sentinel_remediation` schema/version ledger and NOLOGIN group
`control_action_writer`. An operator provisions a dedicated login in that group;
no application password is committed. Existing core migrations remain `[1,2,3]`
and old API/health/incident roles gain no action access. The action role can read
core evidence, cannot write IERs or assume owner, and cannot update/delete/truncate
intent, event or dispatch history. Startup readiness rejects unsafe privileges.
The migration is not silently mounted/applied by Compose or worker startup.

Intent, target claim and initial event commit atomically. A separate transaction
arms one immutable dispatch budget per incident/target before HTTP. Hash-linked
events and a CAS state projection detect missing/rewritten/inconsistent history;
concurrent status reads use one statement snapshot. Duplicate requests return the
same action; different contents conflict. Target lock and cooldown are durable.
An audit failure rolls back dispatch/state/target-clock updates together.

After arming, kill switch, source qualification and incident revision are checked
again immediately before transport. A crash after arming is conservatively
ambiguous, even if no packet was sent: retry observes Kubernetes and never sends
a second automatic patch. A lost response after server commit therefore cannot
duplicate dispatch. Already sent actions reconcile/verify even after disabling
the runbook. Known-good image, new ready generation/pod identities and current
post-action SLO evidence must all qualify before action-journal `recovered`.
The actual database receipt also enforces the deadline. Failed/inconclusive or
ambiguous verification escalates and retains target quarantine for operator
review; there is no automatic known-bad compensation or automatic quarantine
release. This trades availability for safety and does not claim exactly-once HTTP.

The executor writes no IER lifecycle transitions. Existing natural-recovery
semantics are unchanged until H4/action integration is agreed. Structured logs
include action/revision/phase; the bounded OpenTelemetry counter
`sentinelops_remediation_state_observations_total` counts observations (retries
included), with phase/runbook labels and no incident/action ID metric labels.

## Post-action telemetry verifier

`sentinelops.remediation.verification.verify_post_action_health` is an internal,
read-only component for the future controller. It accepts a `HealthLedgerReader`,
an explicit action-effective UTC clock, decision cutoff and expected environment.
The controller must bind that reader's stream/policy to its durable action intent;
an arbitrary timestamp, fixture or manually supplied health observation is not
proof that an action occurred.

The verifier uses the existing raw-source verification path, including accepted
PostgreSQL history, immutable raw/normalization/signal artifacts and policy/source
identity. It reproduces the stream from its origin instead of accepting an
unchecked streak/checkpoint. A bounded history budget (128 batches by default)
returns `inconclusive` on exhaustion. Large histories need a separately verified
checkpoint design before controller integration; callers may explicitly increase
the bounded budget to at most 10,000. No table migration or worker state changes
are introduced.

- Exclude any 60-second bucket whose start precedes action effectiveness, even
  when its health decision occurs afterwards. Do not inherit a pre-action clear
  streak or count a `recovery_confirmed` event as three new buckets.
- Require three consecutive eligible Payment buckets with clear SLO observations.
  Existing health policy checks requests, coverage, latency/error limits and lag;
  no alternate recovery threshold is introduced here.
- UNKNOWN, insufficient traffic or a breach resets the verifier's own streak.
  Duplicate, skipped or reordered source buckets fail integrity validation.
- Bound available evidence by the explicit decision cutoff and a 600-second
  verification deadline. Late database receipt cannot repair an expired run.
  Database/artifact errors propagate; callers must handle them as unavailable,
  retain durable intent and escalate when their deadline expires.

Outputs are internal dictionaries, not a new public API or an IER lifecycle
transition. `telemetry_verified` only means these SLO observations qualify.
`action_dispatch_authorized`, `deployment_identity_verified` and
`recovery_accepted` remain **false** for every result. There is no permission to
dispatch, no Kubernetes readiness observation and no causal recovery claim.

The executor selects `require_current=True`, yielding
`post-action-current-slo/v1`: it scans through the cutoff, retains the last three
clear buckets and requires the latest due accepted bucket for the frozen
30-second-watermark profile. An early healthy prefix followed by UNKNOWN/breach,
or a stopped source, cannot certify current recovery. The historical default
remains backward compatible for bounded interval evidence. Completion crossing a
new bucket anchor/deadline must wait/escalate rather than backdate qualification.

Run the source-backed codec fixtures:

```sh
python -m pytest -q tests/test_remediation*.py tests/test_health_source.py
```

The verifier was also run twice against B's actual accepted local health ledger,
using the controlled Payment fault-reset clock `2026-10-31T16:15:33.015128Z` and
capture cutoff `16:20:33.448862Z`. It reproduced the same three eligible buckets
ending `16:17`, `16:18`, `16:19` UTC. This is retrospective validation of the
telemetry component on actual observations. The reference operation was a fault
reset, **not** an image rollback or an autonomous recovery. It does not satisfy
ADR-009 closed-loop acceptance. Machine-local output is retained under the owned
ignored runtime root, not committed as a PASS report.

## Reproducible storage and Kubernetes API checks

```sh
python -m scripts.check_remediation_store --output artifacts/lane-b/action-store-new.json
python -m scripts.check_rollback_kubernetes --kind /path/to/kind --output artifacts/lane-b/kubernetes-api-new
```

Outputs must be new. The first command requires local `postgres:16-alpine` already
available, pins its local image ID, creates a labeled private PostgreSQL container
with an explicit free loopback port and removes only that new container/anonymous
data. It tests real concurrent proposals/dispatch CAS, append-only ACLs, native
rejection of manual IER authority, cooldown/quarantine, injected audit rollback,
atomic status snapshots and actual DB stop/start with exact history preservation.
Incident/terminal action states in this storage test are explicitly fixtures.
The same test checks the dedicated SELECT-only API credential (including rejected
column-level writes and writer credentials), revision-scoped action pagination,
actual HTTP handlers against PostgreSQL, quarantine after outage and cancellation
of a prepared intent without a dispatch or IER write.

The Kubernetes check requires Docker, kubectl and kind v0.30.0, pins the official
v1.34.0 node digest, creates a unique local cluster/registry and uses an explicit
private kubeconfig only for bootstrap. The runtime adapter receives a limited
ServiceAccount token. The tiny HTTP/failed-start images are **API fixtures, not
SentinelOps Payment or SLO evidence**. The check actually executes TLS/RBAC,
server dry-run without image mutation, rejected stale resourceVersion, and one
image restoration followed by a new ready pod UID/generation. No H4 grant or
approved known-good ledger is manufactured; telemetry/G6 acceptance remain false.
Only newly owned cluster/registry resources are removed; credentials are removed
on cleanup and never included in uploaded evidence. Failed-run diagnostics are
retained as bounded pod/node status and fixture events. Earlier local failures
exposed TCP startup/ephemeral-port test-harness issues and containerd-2 registry
mapping; successful checks retained the original policy/readiness assertions.

CI's `remediation-integration` job repeats both actual database and Kubernetes
API checks. It verifies the official kind binary SHA-256 before executing it;
only bounded JSON verification/diagnostics files are uploaded, excluding token
and bootstrap kubeconfig. Native changes require this job in `ci-summary`.

API semantics and pinning references: [Kubernetes API/dry-run](https://kubernetes.io/docs/reference/using-api/api-concepts/),
[RBAC resourceNames](https://kubernetes.io/docs/reference/access-authn-authz/rbac/),
[kind v0.30.0 node digests](https://github.com/kubernetes-sigs/kind/releases/tag/v0.30.0),
[local registry](https://kind.sigs.k8s.io/docs/user/local-registry/),
[containerd registry hosts](https://github.com/containerd/containerd/blob/main/docs/hosts.md).

## Resume-only worker

`scripts/run_remediation_worker.py` loads one exact target and its original health
stream/store. It does not enumerate incidents, accept proposals, call `execute`,
grant H4 or resend a Kubernetes mutation. A pending `prepared` intent is blocked
with `prepared-intent-requires-explicit-revalidation` and its claim released;
it has not consumed a dispatch attempt. `dispatching`/`verifying` intents use the
existing observation reconciler. Escalated claims remain quarantined. An idle
worker does not create an intent or synthetic incident.

An operator explicitly applies `005-remediation.sql` and provisions a dedicated
login in `control_action_writer`; use its loopback DSN in
`SENTINELOPS_ACTION_DATABASE_URL`, **not** the API reader DSN or core writer role.
The script checks actual privileges, exact source identity/root/environment and
private TLS credentials. Do not select an unrelated capture or fabricate a stream
to make startup succeed. The original physical source/H4 activation review is
still required before a live producer can arm any intent.

```sh
python -m scripts.run_remediation_worker \
  --target /owned/config/payment-target.json \
  --health-stream VERIFIED_STREAM_SHA256 \
  --source-store /owned/original-health-store \
  --server https://127.0.0.1:LOCAL_KUBERNETES_PORT \
  --ca-file /owned/private/cluster-ca.pem \
  --token-file /owned/private/runtime-token \
  --poll-seconds 5
```

The target JSON contains exactly the `Target` fields shown in the RBAC/known-good
contract, with actual cluster/namespace/Deployment UIDs. The placeholders above
are not working credentials or an approved deployment. `--max-ticks N` bounds a
verification run. There is no enable/force/arbitrary-image/provider flag. SIGTERM
interrupts backoff; a tick is bounded to 30 seconds. Dependency outages retry with
capped exponential backoff, at most 12 consecutive failures, then exit for
operator intervention. Integrity/configuration errors stop immediately with a
sanitized error type. No database transaction spans a source/Kubernetes request.
Concurrent processes rely on the existing transactional CAS, never a second send.

## Read-only action HTTP and UI

Reapply the optional idempotent migration explicitly to add the new NOLOGIN
`control_action_reader` group and history index; schema history stays `[1]` and
core stays `[1,2,3]`. Provision a separate login in this reader group and configure
`SENTINELOPS_ACTION_READ_DATABASE_URL` for the API. No existing API/AI role gains
action permissions. Reader readiness rejects schema ownership/creation and any
table- or column-level DML in either control schema, including writer credentials.
The API does not auto-apply a migration or provision a login/password.

All routes use the existing local/test bearer authentication and no-store/errors:

| Route | Observation |
| --- | --- |
| `GET /ready/actions` | SELECT-only store readiness; `dispatch_enabled=false` describes this read API |
| `GET /v1/actions/{sha256}` | Strict [action-observation/v1](../../contracts/action-observation.schema.json), action ETag/`X-Action-Revision`, conditional 304 |
| `GET /v1/incidents/{id}/actions?revision=N&limit=10&cursor=…` | [action-list/v1](../../contracts/action-list.schema.json), only intents issued through pinned incident revision N, incident revision headers |

Pages allow 1–25 items ordered by immutable action creation time/ID; cursors bind
incident and pinned revision. Each action's at-most-100-event history is verified
against the original intent, hash chain and current state in a consistent read
snapshot. Responses project only allowlisted metadata/image identities/reasons
and audit cards, excluding raw facts, idempotency keys and credentials. Action
phases are not IER lifecycle transitions or a claim of qualified recovery.

The incident UI's Actions tab loads these observations on demand, pins the viewed
revision and rejects late responses after changing selection/logout. It supports
pagination/reload, shows missing storage separately from an empty returned page
and renders reasons as text. It never offers execute/approval controls. Existing
incident/evidence views work without action configuration. Missing action store
or invalid audit returns 503; production authentication remains unavailable.

## Remaining controller integration

Wire a new-intent/dispatch producer and authenticated action submission only after
the H4 boundary, real known-good approval and source binding are reviewed. The
resume-only worker and read API do not satisfy those prerequisites. Deliver an
operator quarantine-resolution procedure and action/IER
lifecycle integration without rewriting the analytical RCA contract. Exercise an
actual bad SentinelOps Payment image deployment with qualified independent RCA,
proper before-incident approved image and live Kubernetes telemetry. The current
API/storage fixtures and retrospective fault-reset check do not satisfy that
closed-loop product acceptance. AWS and production authentication remain separate.

Do not change the existing natural-recovery incident consumer to imply an action
ran. Its analytical RCA contract has `actions_allowed=false`; H4 producer and
consumer need an explicitly agreed supplemental boundary before action authority
can be integrated. H4 absence blocks dispatch, not independent executor work.
