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

## Permission model

One AWS environment, permissions per workload, no shared admin role
([EKS IAM guidance](https://docs.aws.amazon.com/eks/latest/best-practices/identity-and-access-management.html)).
Each role is assumed through IRSA by exactly one Kubernetes service account in
the `sentinelops` namespace; nothing inherits workload authority from the node
role.

| Workload | May | May not |
| --- | --- | --- |
| `telemetry-reader` | Read CloudWatch metrics/logs and X-Ray traces | Write anything |
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
  offline as described below; API/workers/observability manifests and their
  deployment bindings remain separate work.
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
