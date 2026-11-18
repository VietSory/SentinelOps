# Payment image evidence Job (`review`)

Render offline from the same images/runtime inputs as `render_all`. A UID-only
JSON (such as `artifacts/aws-demo/uids.json`) also works with the demo defaults:
stream `6028fde726c16b94bd0d5df07e4ae5cc2baa69cc5aec763a08359750f01aecaf`,
CA `control-rds-ca`, PVCs `health-artifacts` and `review-artifacts`.
Optional `--uids` merges separately captured identities and rejects conflicts.
The renderer never contacts AWS or Kubernetes, reads passwords, applies resources,
or calls `approve_known_good`.

Prerequisites in namespace `sentinelops-b-actions`: the existing `payment-rollback`
SA/RBAC, `control-runtime` Secret key `password`, `control-rds-ca` ConfigMap key
`ca-bundle.crt`, the worker ConfigMap `sentinelops-workers-runtime` key
`SENTINELOPS_CONTROL_DATABASE` (credential-free RDS DSN with `sslmode=verify-full`),
and both PVCs. Health ingestion and healthy Payment traffic must continue while
review runs. Target UIDs and the selected stream must describe this exact cluster.

Capture the current health worker node, then its hostname label. This explicit
nodeAffinity is mandatory: the read-only health mount still uses the same RWO
volume as the health writer. The review PVC must also be attachable to this node.
If the health worker moves, capture again and render a fresh Job.

```sh
node=$(kubectl -n sentinelops-b-actions get pods -l app=sentinelops-health-worker \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].spec.nodeName}')
hostname=$(kubectl get node "$node" -o jsonpath='{.metadata.labels.kubernetes\.io/hostname}')
python -m scripts.aws_demo.review_job artifacts/aws-demo/images.json \
  artifacts/aws-demo/runtime-pass2.json --uids artifacts/aws-demo/uids.json \
  --health-node "$hostname" --output artifacts/aws-demo/review.yaml
kubeconform -strict -summary -kubernetes-version 1.34.0 artifacts/aws-demo/review.yaml
kubectl apply -f artifacts/aws-demo/review.yaml
kubectl -n sentinelops-b-actions logs -f job/review -c review
kubectl -n sentinelops-b-actions wait --for=condition=complete job/review --timeout=680s
```

The ConfigMap mounts the current `scripts/review_payment_image.py` and pinned
`target.json`; no control-plane rebuild is needed. The Job runs `collect`, which
calls `ImageEvidenceReview.open()` once and retries `complete()` against that same
reference every 15 seconds. It stops at `AWAITING_OPERATOR_REVIEW`, or fails after
at most 580 seconds. `backoffLimit: 0` and `restartPolicy: Never` prevent automatic
reopening. Logs contain the opening reference, progress and final evidence reference;
immutable evidence persists under `/artifacts/review` on `review-artifacts`. Job
completion means evidence is ready for human review, not that the image is approved.

The explicit `--in-cluster-worker` mode supports the requested existing worker
credentials. These credentials have broader permissions than the local review
reader: `control_writer` can write, and `payment-rollback` can patch Payment.
The collector verifies the expected DB/RBAC scope, opens DB sessions with
`default_transaction_read_only=on`, and gives `ImageEvidenceReview` an interface
with only observation and permission checks. Health replay also uses read-only
transactions. The original local mode retains its SELECT-only DB and no-mutation
credential checks. The Job never patches Payment or changes RBAC.

Both SA token/cluster CA and DB password are copied by the non-root init container
into private regular files on a memory volume, with umask `0077`, matching the
controller's projected-token pattern. The main process mounts those files read-only;
RDS TLS uses the separate public CA bundle. The health PVC is read-only at both
volume and mount; the review PVC is writable.

For separate manual stages, render with `--operation open`, record `review_ref`,
then render another manifest with `--operation complete --review-ref artifact://manifests/<sha256>.json`.
The opening review expires after ten minutes, so allow enough whole healthy buckets
before completing. To run another Job named `review`, save its logs and delete the
previous Job explicitly before applying the next manifest. The output file is
created exclusively, so choose a new output path on each render. No automatic
cleanup deletes the evidence PVC or previous Job logs.

Human approval via `scripts/approve_known_good.py` remains a separate step after
reviewing the evidence and physical workload binding. This Job produces
`known_good_approved=false`, `physical_binding_verified=false` and
`action_dispatch_authorized=false`; it supplies no approved ledger or dispatch.
