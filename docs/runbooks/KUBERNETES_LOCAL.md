# Local Kubernetes business testbed — lane B

This is a B-owned development runtime for the actual Checkout, Payment and
Inventory implementations, commerce PostgreSQL, OTel Collector, Prometheus and
Jaeger. It extends the earlier tiny HTTP Kubernetes API fixtures. It does not
activate a model, approve an image, run remediation or accept H4/source parity/G6.
Control-plane incident persistence remains in the existing isolated local setup.

## Reproduce the source observations

Install the project's Python 3.11 dependencies, Docker, kubectl and kind v0.30.0.
Use a host without an active A corpus capture. The command refuses a detected or
unverifiable capture; the owner must still prevent a new capture from starting
mid-run. It needs capacity for a separate node capped at four CPUs (or fewer when
the host has fewer) and 4 GiB, plus its small registry. It never changes the B/A
Compose projects or stops unrelated containers.

```sh
python -m scripts.check_kubernetes_testbed \
  --kind /path/to/kind-v0.30.0 \
  --output artifacts/lane-b/business-kubernetes-new
```

The output must be new and stay inside the owned ignored B root. The script:

1. Creates a uniquely named private kind cluster/registry using the existing
   pinned v1.34.0 node and local Payment RBAC. No ambient kubeconfig is consumed.
2. Pulls dependencies and records their actual immutable registry digests. Builds
   the existing application Dockerfile against a pinned Python base, pushes the
   real service image to the private registry and deploys seven one-replica
   Deployments. Apps have no mounted API token, no host ports, no HPA and no fault
   injection. Payment keeps container `payment`, zero surge and exact UID scope.
3. Provisions random commerce credentials directly into the private API. Secret
   bytes/DSNs never enter manifests, artifacts or process arguments. PostgreSQL
   and Prometheus use disposable emptyDir storage, not production persistence.
4. Opens only loopback port-forwards. Sends successful real Checkout requests
   through Inventory/Payment and PostgreSQL until a whole UTC minute plus source
   watermark is available. It does not shorten the existing 60s/30s source clocks.
5. Calls the existing Prometheus source catalog/normalizer, retains the three
   service captures and compares each business aggregate with the per-pod
   request series. A selected actual distributed trace must pass the existing
   parentage/status/correlation/native-time validator, and its resource UID must
   match the observed ready pod. Structured request logs must resolve the same
   correlation/trace/pod. Before/after Deployment/pod inventory must stay stable.
6. Stops its own port-forwards and removes only its newly owned cluster/registry
   and private kubeconfig/tokens, including after failure. Does not prune images,
   volumes, networks or another project's resources. Bounded diagnostics remain.

One observed RED bucket is **not** a 300s feature window, a three-bucket healthy
certificate, an independent training run or known-good approval. Source/parity
and physical-binding acceptance remain false even when these bounded observations
are consistent. A failure is retained; no synthetic responses replace unavailable
metrics, logs, traces or requests.

## Identity semantics and handoff

Kubernetes passes `metadata.uid`, `metadata.name` and `metadata.namespace` via
Downward API to `SENTINELOPS_K8S_POD_UID`, `SENTINELOPS_K8S_POD_NAME` and
`SENTINELOPS_K8S_NAMESPACE`. Supplying only part of this bundle or malformed values
fails service startup. With none supplied, Compose telemetry keeps its existing
dimensions. In Kubernetes:

| Observation | Actual identity fields |
| --- | --- |
| OTel trace resource | `k8s.pod.uid`, `k8s.pod.name`, `k8s.namespace.name`; `service.instance.id` equals pod UID |
| Request/error/duration metric point | `k8s_pod_uid`, `k8s_namespace_name`; existing service/method/route/status and histogram boundaries stay unchanged |
| Structured business log | `k8s.pod.uid`, `k8s.pod.name`, `k8s.namespace.name`, existing trace/correlation/request IDs |
| Kubernetes API | Exact Deployment UID → controller ReplicaSet → ready pod UID/runtime imageID, observed before and after |

Environment attributes alone are untrusted application observations. The check
compares them to native API observations, but does not prove arbitrary historical
causality, source receipt timing or activation authority. API `observed_at` clocks
are client observation completion clocks; they are not invented deployment-change
event times. The trace's native span clocks remain distinct from retrieval time.
The original RED query catalog aggregates by service and business route; additional
pod labels do not themselves certify model/source compatibility. Lane A owns that
review and any future adapter/profile change. No A module/config/corpus is modified.

`verification.json` includes image digests, source hashes, native inventory and
artifact references. References resolve within its sibling `source/` content store.
CI uploads the verification, redacted manifests/image map and that store together;
download the whole artifact to reproduce raw normalization and inspect lineage.
Local registry addresses cease to serve after cleanup: their recorded image digests
identify the observed run, not a portable approved image release. Build/pin again
for a new runtime. `observer_commit` plus loaded source hashes/dirty-state identify
the verifier bytes; an older parent commit alone is not verification of new code.

## Render manifests for a separately managed local sandbox

`scripts/kubernetes_testbed.py` is the B-owned renderer. Supply exactly five
reachable immutable registry references in JSON (`application`, `postgres`,
`otel-collector`, `prometheus`, `jaeger`):

```sh
python -m scripts.kubernetes_testbed --images /owned/b/images.json \
  --output /owned/b/manifests.yaml
```

It emits namespace `sentinelops-b-actions`, ConfigMaps, ClusterIP Services and
Deployments only. Provision `commerce-credentials` separately with keys `password`
and `database-url` using a private bootstrap context before applying. Create the
reviewed namespace/RBAC in that private context first. The renderer never
accepts credentials or emits a Secret. Manual lifecycle/ownership is the operator's
responsibility; do not apply into an existing action/capture environment casually.
This transient sandbox does not replace the Compose control DB or provide durable
commerce storage, production authentication or an enabled action producer.
