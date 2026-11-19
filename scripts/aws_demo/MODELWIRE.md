# modelwire — nạp de38fc6b vào EKS

`modelwire.py` render ConfigMap + Job copy một lần vào PVC. Render không gọi AWS
hay kubectl; Job không train, tạo approval, bật policy hay sửa registry. Dùng
**cùng control-plane image digest** với worker/controller; image này đã cài
`sentinelops[ml]`. Script mount từ ConfigMap. **Cần rebuild control-plane image
chứa bản sửa packaging modelwire này**: image trước đó thiếu frozen feature
config ở đường dẫn mà installed registry đọc. Bản sửa đóng gói đúng bytes
`configs/alibaba_relative_features.json` trong wheel và thêm fallback loader;
không thay frozen config, threshold hay approval. Những lần copy sau không cần
rebuild chỉ để đổi modelwire ConfigMap.

## File, layout và identity

Pin từ `docs/reports/LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md`:

| Identity | Giá trị |
| --- | --- |
| Model version | `de38fc6bb89896856556e13e7c6142ba21c8d0fea88ebd082f95e96d4e5b0e57` |
| SHA-256 bytes manifest.json | `d52df5846ac5027507d96b67c751f02f2c5519e3ea970fbdc3599207d18eb84e` |
| Release ref đã dùng trên B local | `artifact://manifests/092290cedcea07578f664aa670eabf236f66ce7db27ebf159c2c386c3ee5216b.json` |
| Archive SHA-256 | Teammate giao qua kênh tin cậy; chưa có archive trong checkout |

Bốn identity này khác nhau. `de38fc6b` chỉ là tên rút gọn; S3 URI/VersionId không
phải release ref. Release local trên là thông tin truy vết, không tự chứng minh
parity EKS. Owner cần chấp nhận phạm vi release cho EKS hoặc cung cấp release mới
từ parity/quyết định hợp lệ. Registry hiện không so sánh environment runtime với
environment parity; đừng coi loader PASS là phép đo parity EKS.

Raw bundle có **4 file**. Để registry available phải giao **trusted release store**
của `scripts.build_alibaba_release`, thường **10 file**, root PVC như sau:

```text
/models/                              # không có thư mục bao ngoài
  alibaba-v2022/
    manifest.json                     # sentinelops-model/v3, features/hashes/versions
    detector.joblib                   # Detector: scaler, IF, calibration/thresholds
    risk.json                         # XGBoost
    report.json                       # frozen fit report
  manifests/
    <release-sha256>.json              # validated-model-release/v1
    <approval-sha256>.json             # release.models[0].validation_ref
    <fit-report-sha256>.json           # approval.evaluation_refs[0]
    <controls-sha256>.json             # approval.evaluation_refs[1]
    <parity-record-sha256>.json         # approval.evaluation_refs[2]
    <parity-verification-sha256>.json   # approval.evaluation_refs[3]
```

Giữ nguyên bytes/tên file; hash JSON canonical trong ArtifactStore có thể khác
hash raw `report.json` có whitespace. Không pretty-print lại JSON. Modelwire nhận
tar/tar.gz có `alibaba-v2022/` và `manifests/` ở root, chỉ các file thuộc graph đã
chọn; không thêm SHA256SUMS/dữ liệu training/thư mục bao ngoài vào archive.

Release trỏ `bundle_directory=alibaba-v2022`, đúng manifest SHA. Approval gắn đúng
model, `G2/G3/source_parity=PASS`, `activation_allowed=true`, source profile v2,
report/config hashes và evaluation refs. Bundle manifest vẫn giữ
`activation_allowed=false`. Loader kiểm frozen config hiện tại, checksum report/
weights, library versions **khớp chính xác** manifest, estimator metadata và
calibration. Checksum chứng minh byte identity; quyền tin cậy joblib đến từ owner.

**Lấy model-release-ref** từ field `release_ref` trong stdout/receipt của
`python -m scripts.build_alibaba_release ... --output NEW_STORE`, hoặc ref owner
đã xác nhận cho store được giao. Đây là
`artifact://manifests/<SHA256 release ledger>.json`, không chọn ngẫu nhiên JSON.
Chỉ có 4 file raw là thiếu release/approval/evidence: owner phải build release
theo MODEL_PIPELINE.md với controls, parity-record, parity-verification,
approved-by, decision-note và confirm-owner-decision. Không tự viết PASS flags.

Teammate đóng gói **store đã build**, giữ root:

```bash
tar -C "$APPROVED_STORE" -czf de38fc6b-release.tar.gz alibaba-v2022 manifests
sha256sum de38fc6b-release.tar.gz
```

Giao archive, archive SHA độc lập, release ref/receipt và phạm vi approval. Có thể
gom hai pin vào `model-handoff.json`:

```json
{"sha256":"<64 hex archive SHA đã giao>","release_ref":"artifact://manifests/<64 hex release ledger>.json"}
```

## PVC và AWS identity

- PVC riêng cùng namespace `sentinelops-b-actions`. Worker và controller mount
  cùng claim **readOnly `/models`**; Job ghi bằng UID/GID/fsGroup `10001`.
  Khuyến nghị PVC riêng cho mỗi release, RWX để các pod khác node cùng đọc.
- Dùng StorageClass RWX đã provision/review, ví dụ Amazon EFS CSI access point có
  POSIX owner `10001:10001` và thư mục writable. Terraform hiện chưa provision
  EFS/StorageClass. `--storage-class` chỉ render PVC RWX 4Gi; với claim sẵn có thì
  bỏ option. EBS RWO đòi hỏi bảo đảm các pod cùng node/topology phù hợp; mount
  readOnly không biến RWO thành RWX.
- Bật encryption at rest lúc tạo EFS/EBS và TLS cho EFS CSI mounts. NFS SG chỉ mở
  2049 từ node SG cần thiết; giới hạn principal/access point bằng IAM khi dùng.
- Artifacts bucket Terraform có versioning, SSE-KMS, TLS-only. Exact download cần
  `s3:GetObjectVersion` cho object dưới `models/`, `kms:Decrypt` cho artifact key;
  không cần PutObject/ListBucket. Dùng IRSA/Pod Identity credentials ngắn hạn.
  Scope principal/resource và thêm source account/ARN conditions khi service
  integration có cung cấp các context key đó.
- **Role data-worker hiện không dùng thẳng ở B được**: `infra/aws/iam.tf` trust
  `system:serviceaccount:sentinelops:data-worker`, không phải namespace B. Job S3
  cần SA đã provision đúng trust namespace/name và webhook token/env injection.
  Modelwire không tạo IAM/SA. Nếu chưa có, tải exact version bằng AWS CLI trên máy
  operator rồi dùng đường local; không cấp credentials AWS cho worker/controller.

## Lệnh Claude chạy khi nhận bundle (Bash/WSL)

Từ root repo, Python 3.11 có `sentinelops[ml]` và PyYAML; máy operator có `jq`,
`kubectl`, AWS CLI nếu dùng S3. Inputs thực `images.json`,
`terraform-outputs.json`, `runtime.json` cùng cấu trúc pipeline render hiện có.
Các placeholder bên dưới phải thay bằng handoff/tài nguyên thật.

```bash
export PYTHONPATH="$PWD/src:$PWD${PYTHONPATH:+:$PYTHONPATH}"
NS=sentinelops-b-actions
CLAIM=model-de38fc6b
JOB=modelwire-de38fc6b
IMAGE=$(jq -er '.["control-plane"]' images.json)
BUNDLE_SHA=$(jq -er '.sha256' model-handoff.json)  # pin teammate, không tự thay pin khi mismatch
REF=$(jq -er '.release_ref' model-handoff.json)
BUNDLE="$PWD/de38fc6b-release.tar.gz"
export MODEL_STORAGE_CLASS='<StorageClass RWX thật đã review>'
# Preflight installed runtime: image cũ sẽ fail ở đây trước khi đụng PVC.
docker run --rm --entrypoint python "$IMAGE" -c \
  'from sentinelops.ml.alibaba_training import load_config; assert load_config()["feature_set_version"] == "http-relative-5m/v2"; print("frozen config available")'
```

Nếu preflight fail vì thiếu config, build bản sửa này rồi push theo quy trình ECR
hiện có và cập nhật `images.json` bằng digest mới trước khi render Job/workers:

```bash
docker build --file docker/Dockerfile.control-plane \
  --build-arg "SENTINELOPS_SOURCE_REVISION=$(git rev-parse HEAD)" \
  --tag sentinelops/control-plane:modelwire .
# Push/digest receipt: scripts/push_images_ecr.py; IMAGE phải lấy lại từ images.json đã cập nhật.
```

### A. Local archive → Job → PVC

Dev 833925774773 không có EFS/RWX StorageClass và mọi PVC là gp2 trên một node
(AZ 1b). Dùng `--storage-class gp2 --access-mode ReadWriteOnce --colocate-with
remediation-controller`: Job bị ghim cùng node với controller, nên EBS mới tạo đúng
AZ và incident worker/controller mount read-only trên cùng node. Đường local không
cần IRSA/S3 cho pod; operator tải archive về máy rồi đẩy qua `kubectl exec -i`.

```bash
printf '%s  %s\n' "$BUNDLE_SHA" "$BUNDLE" | sha256sum --check --strict
python -m scripts.aws_demo.modelwire render --source local \
  --image "$IMAGE" --expected-sha256 "$BUNDLE_SHA" --release-ref "$REF" \
  --claim "$CLAIM" --name "$JOB" --storage-class "$MODEL_STORAGE_CLASS" \
  --access-mode ReadWriteOnce --colocate-with remediation-controller \
  --output artifacts/aws-demo/modelwire-local.yaml
# Bỏ hai cờ RWO chỉ khi MODEL_STORAGE_CLASS là EFS/RWX đã review; dev 833 dùng gp2.
kubectl -n "$NS" apply -f artifacts/aws-demo/modelwire-local.yaml
kubectl -n "$NS" get pods -l "job-name=$JOB"
# Khi Job controller đã tạo pod, lấy tên và chờ Ready:
POD=$(kubectl -n "$NS" get pods -l "job-name=$JOB" -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" wait --for=condition=Ready "pod/$POD" --timeout=300s
kubectl -n "$NS" exec -i "$POD" -c modelwire -- \
  python /wire/modelwire.py receive --bundle /work/bundle.tar.gz < "$BUNDLE"
kubectl -n "$NS" wait --for=condition=Complete "job/$JOB" --timeout=900s
kubectl -n "$NS" logs "job/$JOB" -c modelwire
```

Job cho 600s để chuyển file vào. Dùng Bash/WSL để redirect binary; không dùng
PowerShell text pipeline. Không cần `kubectl cp`/tar executable trong runtime.

### B. S3 exact version → Job → PVC

Nếu cần upload trước, script hiện có tạo key
`models/de38fc6b/<archive-sha256>/<archive-basename>`:

```bash
BUCKET=$(jq -er '.artifact_bucket | if type=="object" then .value else . end' terraform-outputs.json)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
python scripts/aws_demo/upload_model.py --bundle "$BUNDLE" --expected-sha256 "$BUNDLE_SHA" \
  --release de38fc6b --bucket "$BUCKET" --account-id "$ACCOUNT_ID" \
  --region ap-southeast-1 --execute > model-upload.json
```

Hoặc nhận `model-upload.json` từ teammate. Upload script chuyển **một file**,
không build release/extract PVC/cấu hình worker. Dùng receipt bucket/key/VersionId
thật; không tải latest theo prefix:

```bash
test "$(jq -er '.status' model-upload.json)" = UPLOAD_AND_READBACK_PASS
test "$(jq -er '.sha256' model-upload.json)" = "$BUNDLE_SHA"
BUCKET=$(jq -er '.bucket' model-upload.json)
KEY=$(jq -er '.key' model-upload.json)
VERSION_ID=$(jq -er '.version_id' model-upload.json)
export ARTIFACT_OWNER_ACCOUNT='<12 digits đã xác nhận>'
export MODEL_READER_SA='<SA có trust đúng namespace B>'
export AWS_CLI_IMAGE='<AWS CLI image@sha256:64hex đã review>'
python -m scripts.aws_demo.modelwire render --source s3 \
  --image "$IMAGE" --aws-cli-image "$AWS_CLI_IMAGE" \
  --expected-sha256 "$BUNDLE_SHA" --release-ref "$REF" --claim "$CLAIM" --name "$JOB" \
  --storage-class "$MODEL_STORAGE_CLASS" --service-account "$MODEL_READER_SA" \
  --bucket "$BUCKET" --key "$KEY" --version-id "$VERSION_ID" \
  --account-id "$ARTIFACT_OWNER_ACCOUNT" --region ap-southeast-1 \
  --output artifacts/aws-demo/modelwire-s3.yaml
kubectl -n "$NS" apply -f artifacts/aws-demo/modelwire-s3.yaml
kubectl -n "$NS" wait --for=condition=Complete "job/$JOB" --timeout=1200s
kubectl -n "$NS" logs "job/$JOB" -c download
kubectl -n "$NS" logs "job/$JOB" -c modelwire
```

Job dùng `s3api get-object --version-id --expected-bucket-owner --checksum-mode
ENABLED`, rồi tự hash bytes theo pin owner, không dùng ETag. Nếu chưa có SA đúng
trust, thay direct-S3 Job bằng tải local, sau đó chạy A:

```bash
aws --region ap-southeast-1 s3api get-object --bucket "$BUCKET" --key "$KEY" \
  --version-id "$VERSION_ID" --expected-bucket-owner "$ARTIFACT_OWNER_ACCOUNT" \
  --checksum-mode ENABLED "$BUNDLE"
```

Chọn **A hoặc B**; không apply hai Job trùng tên. Output không ghi đè file cũ.
Retry dùng Job/filename mới, có thể dùng cùng PVC sau khi kiểm tra lần lỗi.

## Chọn model ở cả incident worker và controller

Chỉ chọn sau Job `Complete`, log `MODELWIRE_PVC_VERIFIED` và
`registry_available=true`. Job hash trước extraction, cấm links/traversal, giới
hạn archive 1GiB/expanded 2GiB, kiểm graph/load registry trước ghi PVC, dùng atomic
no-overwrite links và publish release cuối. Retry chấp nhận bytes giống hệt.
Job bị ngắt có thể để lại một phần file; đợi retry hoàn tất rồi mới chọn release.
Đây không phải whole-directory transaction hay hot activation.

`render_all.py` nay chuyển tiếp hai field runtime tùy chọn vào renderer worker.
Renderer mount model readOnly `/models` và truyền `--model-store` /
`--model-release-ref` cho **cả incident-worker và remediation-controller**.
Controller dựng registry riêng để replay journal và chạy NativeH4Authority;
chỉ cấu hình incident-worker là chưa đủ. Health worker không cần model.

```bash
jq --arg claim "$CLAIM" --arg ref "$REF" \
  '. + {model_claim:$claim, model_release_ref:$ref}' runtime.json > runtime-model.json
bash scripts/aws_demo/render_all.sh images.json terraform-outputs.json runtime-model.json \
  --output artifacts/aws-demo/k8s-model
```

Nếu chưa từng có incident consumer trên stream này, apply `40-workers.yaml` sau
khi các dependency thật sẵn sàng. Nếu consumer đã chạy chưa có model/khác release,
**không sửa identity/checkpoint DB**: profile gắn model release, consumer ID gắn
health stream. Tạo epoch mới qua artifact root mới. Lệnh offline `epoch` sửa
đồng bộ args + exec probes ba Deployment, giữ nguyên PVC mounts:

```bash
HEALTH_ROOT=/artifacts/health/model-de38fc6b-20261118  # tên mới chưa từng dùng
python -m scripts.aws_demo.modelwire epoch \
  --workers artifacts/aws-demo/k8s-model/40-workers.yaml --health-root "$HEALTH_ROOT" \
  --health-only --output artifacts/aws-demo/modelwire-health-bootstrap.yaml
kubectl -n "$NS" scale deployment sentinelops-incident-worker remediation-controller --replicas=0
kubectl -n "$NS" apply -f artifacts/aws-demo/modelwire-health-bootstrap.yaml
kubectl -n "$NS" rollout status deployment/sentinelops-health-worker --timeout=300s
kubectl -n "$NS" logs deployment/sentinelops-health-worker --since=10m
# Lấy stream_id thật từ READY tiến trình MỚI; đợi COMMITTED cùng stream đó.
export NEW_HEALTH_STREAM='<64 hex stream_id thực vừa ghi nhận>'
jq --arg stream "$NEW_HEALTH_STREAM" '.health_stream=$stream' \
  runtime-model.json > runtime-model-epoch.json
bash scripts/aws_demo/render_all.sh images.json terraform-outputs.json runtime-model-epoch.json \
  --output artifacts/aws-demo/k8s-model-epoch
python -m scripts.aws_demo.modelwire epoch \
  --workers artifacts/aws-demo/k8s-model-epoch/40-workers.yaml --health-root "$HEALTH_ROOT" \
  --output artifacts/aws-demo/modelwire-workers.yaml
# Trước apply full: provision known-good ledger/Secret remediation-runtime đúng
# stream mới và các dependency controller; không dùng ledger/receipt stream cũ.
kubectl -n "$NS" apply -f artifacts/aws-demo/modelwire-workers.yaml
kubectl -n "$NS" rollout status deployment/sentinelops-incident-worker --timeout=300s
kubectl -n "$NS" rollout status deployment/remediation-controller --timeout=300s
```

Lưu bản epoch làm desired state; đừng apply lại bản thiếu root mới. Đổi stream
còn yêu cầu baseline/known-good ledger/deployment-change receipts gắn stream mới
và target UID thật; không tái sử dụng receipt cũ cho H4. Wrapper mặc định tắt
controller policy; modelwire không bật policy. Cấu hình action đã review riêng
cần được bảo toàn theo quy trình rollout của operator.

Kiểm registry thật ở cả hai pod sau rollout:

```bash
for DEPLOY in sentinelops-incident-worker remediation-controller; do
  kubectl -n "$NS" exec "deployment/$DEPLOY" -- python -c \
    'import sys; from sentinelops.ml.registry import ModelRegistry; from sentinelops.telemetry.store import ArtifactStore; r=ModelRegistry.load(ArtifactStore("/models"),sys.argv[1]); assert r.available and r.is_alibaba and r.identity==sys.argv[1]; print(r.identity, "registry.available=true")' "$REF"
done
```

Giữ healthy traffic đủ ít nhất 5 baseline minutes trước 5-minute window và các
bucket v2 được chấp nhận. Kiểm published incident revision có model signals thực,
đúng release và capture `continuous-incident-evidence/v6` khi đi đường
deployment-change v5 + model. READY/status chỉ chứng minh runtime/bundle load,
không chứng minh inference. API legacy `/v1/detect` không phải phép thử Alibaba.

`authority.py:67` còn cần physical deployment change/capture version đúng. Các
gate sau vẫn kiểm RCA không abstain, current revision/source refs, health
ELIGIBLE/caught-up, Payment root, freshness, UID/image/generation, known-good,
policy/kill switch. Nạp model tháo blocker registry, không tự cấp dispatch authority.

## Kiểm thử

```bash
python -m pytest -q tests/test_modelwire.py tests/test_worker_manifests.py \
  tests/test_aws_demo_render.py tests/test_alibaba_release.py \
  tests/test_control_plane_packaging.py tests/test_alibaba_training.py
```

Synthetic bundle/release builder/registry thật, không phải bằng chứng acceptance
de38fc6b. Bao gồm round-trip/idempotency, corruption, thiếu approval/evidence,
library version mismatch, file dư, PVC collision/symlink, tar traversal/links/size,
stdin, S3 version/owner, mount hai consumers và epoch args/probes. Chưa chạy EKS.
Test packaging build wheel thật, nạp registry từ installed wheel ở cwd riêng,
không nhìn thấy source config; kiểm packaged config giữ nguyên bytes.
