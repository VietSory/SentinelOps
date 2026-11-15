# AWS baseline — described, validated, not applied

`infra/aws/` describes the target single-region environment from
[TECHNICAL_SPEC §14](../architecture/TECHNICAL_SPEC.md) and
[ADR-006](../adr/ADR-006-control-plane-and-storage.md): network, EKS, control
PostgreSQL, artifact S3, per-workload IAM roles and bounded observability.

**This directory is a description. It is not authorization to create anything.**
Nothing here has been applied, no AWS account has been touched, and no AWS
credential exists in this repository or in CI. G7 is not accepted. Reading or
validating this configuration does not grant region, quota, budget, model access
or apply permission — the owner confirms each of those first.

## What is validated, and what that proves

```sh
terraform -chdir=infra/aws init -backend=false -input=false
python -m scripts.check_aws_iac
terraform -chdir=infra/aws test -filter=tests/cloudwatch_kms.tftest.hcl
python -m pytest -q tests/test_aws_iac.py
```

`scripts/check_aws_iac.py` runs the real toolchain (`terraform validate`,
`terraform fmt -check`) and then asserts the invariants the architecture depends
on. It makes **zero AWS API calls** and needs no credentials.

| Invariant | Enforced by |
| --- | --- |
| EKS API server private; public access only through an explicit variable, and `0.0.0.0/0` rejected | `cluster_public_access*` validation + guard |
| Control database private, encrypted, and retains a final snapshot on teardown | `aws_db_instance.control` + guard |
| Artifact bucket blocks all public access, versioned, KMS-encrypted, TLS-only | `aws_s3_bucket_*` + bucket policy + guard |
| Image registry (`application`, `control-plane`) has immutable tags, scan on push, KMS encryption and no resource policy; untagged layers expire after 14 days | `aws_ecr_*` + guard |
| No subnet auto-assigns public addresses | `map_public_ip_on_launch = false` + guard |
| Every log group has bounded, encrypted retention; unlimited retention rejected | `log_retention_days` validation + guard |
| No role attaches an administrator or service-wide managed policy | `FORBIDDEN_POLICIES` guard |
| No workload policy grants IAM, EKS, cluster or destructive authority | `FORBIDDEN_ACTIONS` guard |
| Controller holds no AWS mutation authority at all | guard on the controller policy |
| Bedrock invoke bounded to named models in one region, and absent when none are named | `bedrock_model_ids` validation + guard |
| Each workload role trusts exactly one Kubernetes service account | IRSA trust policy + guard |
| No static AWS key, no inline database password | secret scan + `manage_master_user_password` |
| Configuration grants neither model activation nor remediation authority | outputs + guard |

`tests/test_aws_iac.py` proves each guard actually rejects a violation by
mutating a copy of the real configuration, so a guard cannot silently stop
guarding. These are configuration guarantees about a *description*. They are not
evidence that a working environment exists.

The specific `cloudwatch_kms.tftest.hcl` file mocks **both AWS and TLS providers**
and uses only `command = plan`. CI evaluates the real configuration graph and
decoded key/caller policy JSON against fixture account data. It checks the exact
regional Logs principal, the three log-group encryption contexts, read-only
reader permissions, flow-log scope, and a changed region/environment/prefix.
It does not test AWS authorization or encrypt/decrypt live data. Do not replace
this with an ordinary cloud plan or remove the mocks without apply-scope approval.

The ECR guard checks the exact private-repository and lifecycle resource set.
Expiry must bind to each declared repository and retain the complete
`sinceImagePushed` / `days` / `14` / `expire` rule for untagged images. Adding an
unreviewed private/public registry or an unrelated lifecycle policy is rejected;
checking the two expected repositories alone would leave those additions unguarded.
This review changes neither node image-pull IAM nor workload authority. Any
future pull-policy restriction needs the target's complete image inventory and
bootstrap verification during the separately authorized deployment phase.

## Permission model

One AWS environment, permissions per workload, no shared admin role
([EKS IAM guidance](https://docs.aws.amazon.com/eks/latest/best-practices/identity-and-access-management.html)).
Each role is assumed through IRSA by exactly one Kubernetes service account in
the `sentinelops` namespace; nothing inherits workload authority from the node
role.

| Workload | May | May not |
| --- | --- | --- |
| `telemetry-reader` | Read CloudWatch metrics/logs and X-Ray traces | Write anything |
| `telemetry-exporter` | Create/write `otel-metrics-*` and `otel-logs-*` streams in the workload group, send traces in the configured region, use the workload log key through Logs | Create log groups, change retention, read DB/S3, use reader authority, invoke Bedrock or remediate |
| `data-worker` | Read `models/`, write `features/` and `incidents/`, read the DB secret | Mutate Kubernetes, read `known-good/`, invoke Bedrock |
| `explanation` | Invoke only explicitly named Bedrock models in this region | Any remediation, any S3 write, any DB access |
| `controller` | Read `known-good/` evidence, image metadata, the DB secret | Any AWS mutation; rollback is Kubernetes RBAC only |
| `api` | Read the DB secret and `incidents/` evidence | Write incidents, invoke Bedrock, mutate Kubernetes |

The controller deliberately has **no AWS write permission**. Patching the
approved Deployment is granted by
[`infra/kubernetes/payment-rollback-rbac.yaml`](../../infra/kubernetes/payment-rollback-rbac.yaml),
so an AWS credential can never become remediation authority. Auto-action stays
blocked regardless: that gate is H4/G6, not IAM.

The explanation role starts with **no** `bedrock:InvokeModel` permission at all —
`bedrock_model_ids` defaults to empty and the policy is not created. Granting it
requires naming models explicitly; wildcards are rejected.

### CloudWatch encryption binding

All three declared log groups use the environment KMS key. Its explicit policy
preserves the default account administration/delegation statement and grants
the regional `logs.<region>.amazonaws.com` service only cryptographic operations,
conditioned on the **exact three log-group ARNs**. These context ARNs do not have
a `:*` log-stream suffix. Names and context ARNs come from the same local map;
the key policy does not depend on the log-group resources, avoiding a dependency
cycle when the groups reference the key.

The telemetry reader gets only `kms:Decrypt`/`kms:DescribeKey` on this key, through
regional Logs and those same encryption contexts. It remains read-only and is
not an exporter. The existing VPC flow-log delivery role gets cryptographic use
only through regional Logs for its own VPC log group. Flow-log creation depends
on both delivery policies. Neither caller receives direct KMS use or permission
to change retention, create a log group, invoke Bedrock or operate an application.
This follows the [CloudWatch Logs KMS permission model](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html).

The apply principal still needs permission to create/manage the key policy and
`kms:DescribeKey` when associating the key with log groups. These operator
permissions are external prerequisites, not grants made to workloads here.
The [offline collector binding](#offline-aws-collector) now describes the separate
application exporter role and CloudWatch/X-Ray configuration. Deployment,
delivery verification and H3 source capture remain outstanding.

## Prerequisites the owner confirms before any apply

Apply is blocked until **all** of these are explicit. None of them is granted by
this document.

1. **Region** — one region, matching `var.region`.
2. **Account** — `allowed_account_ids` must list the intended account; the
   provider refuses any other, even with valid ambient credentials.
3. **Quota** — EKS cluster, NAT gateways, Elastic IPs, RDS instance class and the
   node instance type must be available in that region.
4. **Budget** — `monthly_budget_usd` and notification addresses. The budget
   alarm notifies; it does not cap spend.
5. **Bedrock model access** — per-model, per-region, and separate from G5.
6. **Apply permission** — who may run `terraform apply`, and the state backend
   decision. There is deliberately no `backend` block: shared state is itself a
   provisioned resource and an owner decision.
7. **Registry apply rights** — the apply role needs `kms:CreateGrant`, `kms:RetireGrant`
   and `kms:DescribeKey` on the environment key so ECR can encrypt its repositories;
   ECR then uses its own grant and nodes need no direct `kms:Decrypt` to pull. The node
   role keeps `AmazonEC2ContainerRegistryReadOnly`: scoping pulls to the two
   repositories would also block the AWS-hosted EKS system images (VPC CNI, pause).


## Cost envelope

Indicative on-demand list prices for `eu-central-1`, for sizing the budget. These
are estimates from public pricing, **not** measured spend — nothing has been
applied, so no bill exists.

| Component | Baseline | Rough monthly |
| --- | --- | --- |
| EKS control plane | 1 cluster | ~$73 |
| Worker nodes | 2 × `t3.large` on demand | ~$150 |
| NAT gateway | 2 AZ, hourly + data | ~$75 + traffic |
| RDS PostgreSQL | `db.t4g.medium`, Multi-AZ, 50 GiB gp3 | ~$130 |
| S3, CloudWatch, X-Ray, KMS | Low volume at 30-day retention, 10% trace sampling | ~$20–40 |
| ECR | Two private repositories, a few GiB of images | ~$1 |
| **Indicative total** | | **~$450–470/month** |

The default `monthly_budget_usd = 300` is therefore **below** this baseline on
purpose: the alarm fires early so cost is noticed before it accumulates. Set the
budget to the owner-confirmed figure rather than silently raising it to fit.

Cost reducers that do not weaken any invariant: `availability_zone_count = 2`
with a single NAT is cheaper but loses AZ-independent egress; Multi-AZ RDS
follows `availability_zone_count`; `node_desired_size = 1` for an idle
environment; shorter `log_retention_days`; lower `xray_fixed_sampling_rate`.
Do not reduce `database_backup_retention_days` below 1 — that store holds IER
revisions and audit history.

## Teardown

```sh
# 1. Confirm what would be destroyed; never run destroy from memory.
terraform -chdir=infra/aws plan -destroy

# 2. Deletion protection is deliberate; drop it only for an intended teardown.
terraform -chdir=infra/aws apply -var 'database_deletion_protection=false'

# 3. Destroy.
terraform -chdir=infra/aws destroy
```

What survives on purpose, and must be handled explicitly:

- **Final RDS snapshot** `<prefix>-control-<env>-final` is always taken
  (`skip_final_snapshot = false`). It holds IER revisions and the audit trail.
  Deleting it discards audit history — a separate, deliberate decision.
- **Artifact S3 bucket** is versioned. `destroy` fails on a non-empty versioned
  bucket by design, so frozen datasets and model releases are not silently
  deleted. Export what a release needs to stay reproducible first, then empty it
  deliberately.
- **KMS key** enters a 30-day deletion window; data encrypted with it is
  unreadable once the key is gone.
- **CloudWatch log groups** are deleted with the stack. Snapshot any evidence a
  report cites before teardown: a report must not depend on expired references.

## Still outstanding

This slice delivers the described, validated baseline. It does not deliver:

- An applied workload bundle. The business-service slice can now be rendered
  offline as described below; the [API image/manifests](CONTROL_PLANE_IMAGE.md)
  are also prepared offline. The collector gateway can be rendered offline as
  described below. Business event OTLP export is opt-in; worker/source adapters,
  infrastructure/stdout log shipping and target delivery
  remain separate work; none of these artifacts is an applied environment.
- An applied environment, live CloudWatch/X-Ray source capture, or H3 handoff.
- A Grafana/operational dashboard.
- Model activation (H1/G3) or remediation acceptance (H4/G6).

G7 requires an applied environment with real adapters, source quality, action and
verify. None of that is claimed here.

## Offline business-service manifests

`scripts/render_business_manifests.py` renders Checkout, Payment and Inventory
from the existing shared application image, pinned by registry digest. It reuses
the existing service names, call paths, pod identity fields and container entry
points. The namespace is `sentinelops`, matching the declared IAM baseline; it
does not change the local `sentinelops-b-actions` remediation policy or adapter.

Supply a real published image digest and the explicit environment/collector
endpoint. Supply the **name** of an externally provisioned commerce Secret whose
`database-url` key contains a valid runtime commerce DSN. The control RDS database
and bootstrap credential are not commerce credentials. The renderer accepts no
password or DSN value and creates no Secret or database/init resource.

```sh
mkdir -p artifacts/deployment
python -m scripts.render_business_manifests \
  --application-image "$BUSINESS_IMAGE_DIGEST" \
  --environment aws-dev \
  --otlp-endpoint "$OTLP_ENDPOINT" \
  --commerce-secret commerce-runtime \
  --output artifacts/deployment/business.yaml
```

The nine objects are a Namespace, token-disabled business ServiceAccount, public
runtime ConfigMap, and three Deployments/ClusterIP Services. There is no RBAC,
IRSA annotation, ingress, LoadBalancer, model selection or action authority.
Fault injection is off. Startup/liveness use `/health`; readiness uses `/ready`.
Pods run as non-root, drop capabilities, have a read-only root filesystem and a
bounded temporary volume. Mutable images, invalid resource names and endpoint
credentials/query strings are rejected. Outputs are deterministic and exclusive:
an existing output file is never overwritten.

Each Deployment template includes a deterministic SHA-256 of the public runtime
ConfigMap. Changing the environment, OTLP endpoint or log-export flag changes all three templates
so applying the rendered bundle rolls the services onto the new configuration.
Identical inputs keep the templates stable. Secret values remain external and
unread: rotating the contents of the same commerce Secret needs an explicit
rollout, or a new Secret reference.

```sh
python -m pytest -q tests/test_business_manifests.py tests/test_kubernetes_testbed.py tests/test_aws_iac.py
kubeconform -strict -summary -kubernetes-version 1.34.0 artifacts/deployment/business.yaml
```

Schema validation was executed with kubeconform 0.7.0 and Kubernetes 1.34.0:
9 valid resources, no invalid/errors/skips; a second run used cached schemas
without network access. The checked image reference was explicitly a software
fixture, not a verified/published deployment image. No registry, cluster or AWS
API was called by rendering/validation. Image availability, external commerce
schema/Secret, collector reachability and target admission/RBAC remain deployment
prerequisites. This is configuration evidence, not a running AWS workload, H3
source parity, known-good approval or G7 acceptance.

### Business event log export

Add `--export-logs` to the business renderer when targeting a collector that
has an OTLP logs pipeline, such as the AWS collector below. It adds only
`SENTINELOPS_OTLP_LOGS_ENABLED=true` to public config and rolls all three pod
templates. Use matching `--environment dev` (or staging/prod) for business and
collector bundles. The emitter uses the same explicit `OTEL_EXPORTER_OTLP_ENDPOINT`
over gRPC, with TLS for `https`. No AWS SDK, token, role, host mount or extra
service account permission is added to business pods. Existing images must be
rebuilt/published through the approved registry workflow before selecting their
new digest; setting the flag on an older image does not add this feature.

Default is off. Local Compose and capture configurations are unchanged; the
local collector currently has no logs pipeline, so do not enable this flag for
that stack. The environment accepts only literal `true`/`false`; enabling without
an explicit credential-free HTTP(S) endpoint/port fails startup. Changes require
a restart/rollout; there is no live toggling or fallback to another destination.

Only structured calls to `services.common.runtime.log_event` are exported:
request start/completion and business/dependency events. The exact JSON string
remains on stdout and becomes the OTLP record body, including event time in ms,
correlation/request-instance IDs, trace/span IDs and available Downward API
identity. OTLP event time is that same timestamp in ns; resource attributes carry
service/version/environment/pod identity, and the active span supplies the OTLP
trace/span fields. These are observations, not authenticated provenance or H3.
The CloudWatch exporter retains its envelope (`raw_log=false`), so consumers must
decode the JSON body rather than assume a raw stdout record at the top level.
Both delivery paths honor the existing `LOG_LEVEL`: raising it above INFO
suppresses these INFO business events on stdout **and** OTLP.

Delivery is best effort: an SDK batch queue holds at most 512 records, batches
up to 64, schedules every second and bounds each export attempt to 2 seconds.
Network retry happens on the exporter thread. Slow/unavailable collectors do not
block the business call; stdout remains emitted. Queue overflow, retry exhaustion,
crash or forced shutdown can lose records; normal SDK shutdown attempts to drain.
Neither `force_flush`, HTTP success nor pod readiness acknowledges remote storage.
No disk spool, durable replay, exactly-once guarantee or delivery readiness is
claimed. Exporter diagnostics stay in Python/stdout logging and cannot recursively
enter the business log exporter. Do not also ship the same stdout events into the
same CloudWatch destination without a deliberate duplicate-handling strategy.

Uvicorn access/error output, arbitrary Python logs, PostgreSQL and infrastructure
stdout are **not** covered by this opt-in emitter; their target log collection
remains separate work. AWS IAM/STS/CNI/CloudWatch delivery and timestamp/body
mapping must still be verified on the authorized target before H3 acceptance.

```sh
python -m pytest -q tests/test_business_log_export.py tests/test_business_manifests.py
```

The transport tests use an isolated loopback OTLP/gRPC receiver with the real
pinned Python exporter, including outage/recovery and real request middleware.
They do not call AWS or claim cloud delivery. Batch/log APIs follow the pinned
SDK; see [OpenTelemetry Python logs](https://opentelemetry-python.readthedocs.io/en/latest/sdk/_logs.html).

## Offline AWS collector

`scripts/render_collector_manifests.py` renders an OTLP gateway with **six**
resources: Namespace, dedicated ServiceAccount, public ConfigMap, Deployment,
ClusterIP Service and ingress NetworkPolicy. It uses collector-contrib; the core
collector in the local Compose stack does not supply these AWS exporters.
The checked component set is contrib **0.162.0**, registry digest
`sha256:39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6`.
Use that image or validate an explicitly selected replacement before deployment.

```sh
mkdir -p artifacts/deployment
python -m scripts.render_collector_manifests \
  --collector-image "$COLLECTOR_IMAGE_DIGEST" \
  --region "$AWS_TARGET_REGION" --environment dev \
  --exporter-role-arn "$TELEMETRY_EXPORTER_ROLE_ARN" \
  --trace-sampling-percentage "$TRACE_SAMPLING_PERCENTAGE" \
  --output artifacts/deployment/collector.yaml
```

`region`, `environment`, `exporter_role_arn`, workload log group and sampling
percentage are exposed by the public Terraform `collector_bindings` output.
Use the owner-approved target values; rendering does not create those resources.
The environment must be `dev`, `staging` or `prod`, matching the IaC baseline.
The workload group is derived as `/sentinelops/<environment>/workloads` and must
already exist with its KMS/retention binding. The role ARN must name the declared
`<name-prefix>-telemetry-exporter` role. No static credential, token value,
arbitrary group, endpoint override or TLS bypass is accepted as input.

The pod explicitly projects a one-hour service-account token with audience
`sts.amazonaws.com` and supplies the AWS web-identity SDK environment. It does
not require a mutating webhook to inject an IRSA token. Automatic Kubernetes API
token mounting is disabled; no Kubernetes RBAC is granted. The trust policy pins
the cluster OIDC provider, exact `sentinelops:telemetry-exporter` service account
and STS audience. IMDS credential lookup is disabled. This still requires real
OIDC/STS access, correct IAM provisioning and node IMDS restrictions on the target;
the renderer cannot prove credential isolation against a compromised node. See
[AWS IRSA](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html).

| Signal | Destination and behavior |
| --- | --- |
| Business OTLP metrics | `awsemf` writes EMF to `otel-metrics-<collector-pod-uid>` in the existing workload group, namespace `SentinelOps/<environment>`. Only the three `sentinelops_http_*` instruments are declared, with service/route/status dimensions and no automatic dimension rollups. Metric publication uses Logs, not `cloudwatch:PutMetricData`. |
| OTLP logs | `awscloudwatchlogs` writes `otel-logs-<collector-pod-uid>`, retaining the OTLP record envelope (`raw_log=false`). It does **not** read container stdout or host files. Business events can opt into the emitter above; other stdout/infrastructure logs still need separate collection. |
| OTLP traces | `awsxray` writes segments in the configured region, links the workload group, skips EC2 metadata and leaves exporter telemetry disabled. A deterministic TraceID sampler uses the **explicit percentage**, with producer sampling-priority overrides removed. Metrics and logs are not sampled. |

The Terraform sampling fraction is multiplied by 100 for this renderer: `0.1`
becomes `10`, not `0.1` percent. The AWS X-Ray sampling rule alone does not make
the existing OTel SDK sample; this collector processor applies the selected rate.
Record it in H3 coverage. It is not a model/SLO threshold or an accuracy claim.
Exporter settings follow the pinned upstream [EMF](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.162.0/exporter/awsemfexporter/README.md),
[Logs](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.162.0/exporter/awscloudwatchlogsexporter/README.md)
and [X-Ray](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.162.0/exporter/awsxrayexporter/README.md) components.

The ingress policy admits OTLP only from pods labelled
`app.kubernetes.io/part-of=sentinelops` in the same namespace. The target CNI must
actually enforce NetworkPolicy. OTLP is internal plaintext; there is no public
listener, ingress or LoadBalancer. Network labels are access configuration, not
authenticated source provenance. Egress still requires the approved target's
DNS/STS/Logs/X-Ray routing and network controls.

The Deployment is non-root with a read-only filesystem, resource limits and
bounded processing/queues. A config checksum rolls the pod when destinations or
sampling change; changing the role ARN also changes the template. It uses one
replica with `Recreate` because EMF cumulative-to-delta conversion keeps local
state: load-balancing the same cumulative series over replicas would split it.
Rollout/restart can lose queued records and the initial cumulative baseline;
this is not a durable or highly available gateway. Health probes check the
collector process, **not successful AWS authentication or delivery**.

```sh
python -m pytest -q tests/test_collector_manifests.py tests/test_aws_iac.py
python -m scripts.check_collector_config --pull
terraform -chdir=infra/aws test -filter=tests/cloudwatch_kms.tftest.hcl
kubeconform -strict -summary -kubernetes-version 1.34.0 artifacts/deployment/collector.yaml
```

The config checker pulls only the pinned public collector image when `--pull`
is selected. It runs the real binary's **validate** subcommand in containers with
network `none`, no AWS credentials, no host mounts and no exporters started.
It checks the rendered config and rejects an intentionally invalid exporter key.
CI also evaluates the exact exporter trust/permission JSON with mocked AWS/TLS
providers, and validates all six Kubernetes resources. These are software/config
checks. They do not verify AWS delivery, projected-token rotation or CNI behavior.

Before H3, verify real metric/log/trace delivery and errors on the authorized
target, including time/unit mappings, histogram conversion and restart gaps.
Do not interpret EMF histogram statistics as validated p95 or feed this source
into the existing local model/worker policy. Source adapters/parity belong to
the A/B handoff; no worker manifest or remote ownership exception is added here.
