# Installed control-plane image and offline API deployment

`docker/Dockerfile.control-plane` supplies the installed API/UI and the pinned
ML dependency set from `pyproject.toml`. It includes the two existing worker
CLIs and their policy/topology files. It contains **no model bundle, credentials,
dataset, source capture or approval**. The default command serves the API; no
worker or action process starts implicitly.

## Build and verify locally

Use the existing Docker engine and a clean committed checkout. Building pulls
the pinned Python base, OS runtime libraries and Python dependencies; it makes
no AWS API call and provisions nothing.

```sh
python -m scripts.check_control_plane_image --build \
  --output artifacts/control-plane-image/verification.json
```

The checker builds with the actual Git revision, resolves the result to an
immutable Docker image ID and retains the built image. Its temporary API
containers use network `none`, UID 10001, a read-only root filesystem, a bounded
temporary filesystem, dropped capabilities, no-new-privileges and bounded
CPU/memory/PIDs. Only those newly created containers are removed on exit.
It never mounts a host directory, Docker socket, database or existing model.

Two HTTP cases run inside the image: production without authentication
configuration fails closed, and a test-only operator/producer configuration
keeps store/model unavailability explicit and denies operator writes. Installed
UI assets, embedded contracts, actual NumPy/Pandas/PyArrow/sklearn/XGBoost imports
and both worker `--help` commands are checked. No prediction is substituted for a
missing model. Test credentials are private temporary files, not command-line
values or published artifacts.

An already built immutable image can be checked without rebuilding:

```sh
python -m scripts.check_control_plane_image --image "$CONTROL_PLANE_IMAGE_ID" \
  --output artifacts/control-plane-image/readback.json
```

Outputs are exclusive. This proves installed-image software behavior, not source
parity, real provider identity, DB integration, a fitted/promoted model or a
running continuous worker. The Git revision label is provenance, not authority.
The image ID used locally is not a published registry digest. Published release
images must be transferred through the approved registry and pinned by digest.
Keep standalone image proofs outside `artifacts/lane-b/`: that runtime root must
be registered by the existing launcher before anything else writes inside it.

The image-specific Docker ignore file allows only package sources/contracts,
build metadata and the two worker CLIs with three runtime config files. In
particular it excludes the separately delivered binary bundle under `docs/`,
all `artifacts/`, `.env`, `.aws`, training data and local dependency snapshots.

## Offline API manifests

The API can serve incident UI/history without an activated model. Supply an
actual published control-plane image digest, public OIDC configuration and the
**name** of an externally provisioned control DB Secret (`database-url` key):

```sh
python -m scripts.render_api_manifests \
  --image "$CONTROL_PLANE_IMAGE_DIGEST" \
  --environment aws-dev \
  --control-secret control-runtime \
  --issuer "$OIDC_ISSUER" --audience "$OIDC_AUDIENCE" --jwks-url "$OIDC_JWKS_URL" \
  --output artifacts/deployment/api.yaml
```

No static bearer token or DSN is accepted as a manifest input. OIDC URLs cannot
contain credentials, query strings or fragments. The existing OIDC verifier
checks signatures/scopes at runtime; rendering does not create an identity
provider. Cognito access-token deployments can explicitly use
`--audience-claim client_id`, with the existing verifier restrictions unchanged.

The pod template includes a deterministic SHA-256 of the public runtime ConfigMap.
Changing OIDC configuration or the model release reference therefore changes the
Deployment template and triggers a rollout when applied. Re-rendering identical
inputs leaves the template unchanged. The renderer never reads Secret values:
rotating the contents of an existing DB Secret requires an explicit rollout, or
selecting a new Secret name. Verify protected dependency readiness after rollout;
the checksum records configuration bytes, not provider/model approval.

Five resources are emitted: Namespace, token-disabled `api` ServiceAccount,
public ConfigMap, API Deployment and ClusterIP Service. No Secret/PVC, IRSA role,
RBAC, ingress, LoadBalancer, migration or action executor is created. The DB
Secret must hold the appropriate runtime DSN/privileges and TLS settings; it is
not the RDS bootstrap credential. Apply/deployment still needs owner-approved
account, region, quota, budget and target prerequisites.

To enable the existing read-only action/quarantine views, add
`--action-read-secret action-read-runtime`. Its `database-url` key must contain
credentials for a separately provisioned SELECT-only login in
`control_action_reader`; the Secret name must differ from `--control-secret`.
Apply the optional action schema migration explicitly before use, including the
quarantine migration when those views are needed; see
[read-only action HTTP/UI](CONTROLLED_REMEDIATION.md#read-only-action-http-and-ui).
The renderer only adds `SENTINELOPS_ACTION_READ_DATABASE_URL` as a Secret reference.
The selected Secret and `database-url` key are required; their absence prevents
pod startup. With a configured binding, the existing reader checks privileges
and rejects writer credentials, schema ownership and quarantine-release authority;
unreachable storage or rejected privileges keep the action store unavailable.
Rendering does not validate Secret contents or create a login, migration,
approval or action executor. Omit the flag to keep action storage unconfigured.
Selecting a new reader Secret name changes the pod template;
rotating its contents under the same name still requires an explicit rollout.

The optional `--model-claim` and `--model-release-ref` must be paired. They mount
an existing, pre-materialized model store at `/models` read-only. This is wiring,
not promotion: the existing registry rejects corrupted, unapproved or incompatible
content at startup. There is no model default, download, approval or demo fallback.
The pre-materialized files and parent directories must be readable/traversable by
UID 10001; the deployment does not chown an existing store or widen its permissions.

### Availability and dependency readiness

Docker health and pod startup/liveness/readiness probes use `/health` to check
the HTTP server. This allows UI/error states to remain reachable during a
DB/model/provider outage. **A ready pod is not proof that those dependencies or
business SLOs are healthy.** `/ready` continues to report dependency availability
and can return 503 while UI is served. Protected `/ready/incidents` and
`/ready/actions` keep their existing authentication and storage checks. UI uses
the protected readiness behavior; unavailable model signals never become zero.

## Worker deployment boundary

The packaged worker CLIs preserve their existing loopback control DB/source
checks, accepted-ledger lineage, source identity and checkpoints. Native incident
evidence also requires the owned local Docker source; Docker CLI/socket and
remote Kubernetes authority are deliberately absent from this image.

**No cloud worker manifest is emitted.** A remote Prometheus/RDS endpoint is not
made legitimate by spelling it as loopback or inserting an unreviewed proxy.
Worker adapters/source policy, persistent source/artifact storage, receipt clocks,
target parity and H3 must be implemented/reviewed before that deployment. Their
absence remains a blocker, not a reason to relax the local safety guards.

See [business manifests/AWS prerequisites](AWS_BASELINE.md),
[offline AWS collector and its delivery boundaries](AWS_BASELINE.md#offline-aws-collector),
[health worker](HEALTH_WORKER.md), [incident worker](INCIDENT_WORKER.md),
[model loading](MODEL_PIPELINE.md) and [host-bound runtime](HOST_PROCESSES.md).
