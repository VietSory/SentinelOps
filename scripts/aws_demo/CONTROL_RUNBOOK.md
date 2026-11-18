# Control — bàn giao cho Claude, demo AWS

Đây là bộ lệnh **chưa chạy lên cloud**. Chạy từ checkout đã review, bằng Bash trên
Linux/WSL có Python 3.11, AWS CLI v2 mới (hỗ trợ `put-object --if-none-match`),
kubectl, Terraform, jq, curl và openssl. Image control-plane đã build/push riêng;
không dùng image/tag khác hoặc `latest`. Không cần psql trong image.

Phạm vi: bootstrap **DB mới** `sentinelops_control`, migration deployment 001 rồi
002–006, hai ngoại lệ demo `control_writer LOGIN` và `control_action_writer LOGIN`
hết hạn **2026-11-25 00:00 UTC**,
Secrets Manager **sentinelops/dev/control-runtime**, Kubernetes Secret
**control-runtime** key **database-url**, Kubernetes Secret **remediation-runtime**
key **action-database-url**, và upload archive model khi được bàn giao.
Không dùng `001-local-roles.sql`. Không cấp quyền action cho `control_writer`.

Claude/A cần hoàn tất self-review và Codex `xhigh` trước các thao tác ghi AWS theo
AWS_DEMO_PLAN §2/§8. Các block có `--execute`, `kubectl create/apply/delete`,
`rollout restart`, `delete-secret` bên dưới là lệnh operator chạy sau review đó.
Việc chuẩn bị bộ này không phải bằng chứng đã triển khai hay đã nghiệm thu G7.

## 1. Nguồn giá trị và điều kiện trước khi chạy

| Biến | Terraform output chính xác (`infra/aws/outputs.tf`) |
| --- | --- |
| `REGION` | `region` (demo phải là `ap-southeast-1`) |
| `CLUSTER` | `cluster_name` |
| `DB_HOST` | `control_database_endpoint` (DNS, không kèm port) |
| `MASTER_ARN` | `control_database_secret_arn` — RDS managed master secret |
| `RUNTIME_ARN` | `control_runtime_secret_arn` — ARN, không phải giá trị secret |
| `CONTROL_REPO` | `image_repository_urls["control-plane"]` |
| `ARTIFACT_BUCKET` | `artifact_bucket` |

`OWNER_ACCOUNT`, digest image đã review, checksum/archive model **không có output
Terraform**. `5432`, `sentinelops_control`, `control_bootstrap` lấy từ storage.tf,
không tự chế tên output. Không sửa Terraform để lấy mật khẩu hay thêm output.

RDS phải Available, PostgreSQL 16+, DB còn mới (không có control roles/schema).
Namespace mặc định là **sentinelops-b-actions**, đúng renderer worker hiện tại;
Secret/CA phải nằm trong namespace của pod tiêu thụ. API renderer riêng có thể dùng
namespace `sentinelops`: xem §5, không dùng nhầm Secret khác namespace.
Node phải pull được ECR; DNS/SG/NetworkPolicy cho phép Job đến RDS:5432.
Operator cần quyền EKS tạo/xóa Job, ConfigMap, Secret, đọc logs và chờ Job; AWS
Secrets Manager GetSecretValue master/runtime, DescribeSecret/PutSecretValue runtime
và KMS tương ứng. Workload **không** được cấp quyền đọc master secret.
Job không có IRSA hay service-account token, không gọi AWS/Kubernetes API.

```bash
cd /mnt/d/year4/AI4life/SentinelOps
set -euo pipefail
set +x
umask 077
export AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off

# Điền account owner và profile operator đã được cấp quyền; đây không phải secret.
export AWS_PROFILE='REPLACE_OPERATOR_PROFILE'
OWNER_ACCOUNT='REPLACE_12_DIGIT_OWNER_ACCOUNT'
REGION="$(terraform -chdir=infra/aws output -raw region)"
CLUSTER="$(terraform -chdir=infra/aws output -raw cluster_name)"
DB_HOST="$(terraform -chdir=infra/aws output -raw control_database_endpoint)"
MASTER_ARN="$(terraform -chdir=infra/aws output -raw control_database_secret_arn)"
RUNTIME_ARN="$(terraform -chdir=infra/aws output -raw control_runtime_secret_arn)"
CONTROL_REPO="$(terraform -chdir=infra/aws output -json image_repository_urls | jq -er '.["control-plane"]')"
ARTIFACT_BUCKET="$(terraform -chdir=infra/aws output -raw artifact_bucket)"
test "$REGION" = ap-southeast-1
test "$(aws --region "$REGION" sts get-caller-identity --query Account --output text)" = "$OWNER_ACCOUNT"
test "${CONTROL_REPO%%.*}" = "$OWNER_ACCOUNT"

# Điền digest thật từ receipt push_images_ecr.py / ECR đã review, 64 ký tự hex.
CONTROL_IMAGE="${CONTROL_REPO}@sha256:REPLACE_REVIEWED_64_HEX_DIGEST"
[[ "$CONTROL_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]
NS=sentinelops-b-actions
CONTEXT=sentinelops-control-demo
aws --region "$REGION" eks update-kubeconfig --name "$CLUSTER" --alias "$CONTEXT"
kubectl --context "$CONTEXT" cluster-info
kubectl --context "$CONTEXT" get nodes
kubectl --context "$CONTEXT" create namespace "$NS" --dry-run=client -o yaml |
  kubectl --context "$CONTEXT" apply -f -

test "$(aws --region "$REGION" secretsmanager describe-secret --secret-id "$RUNTIME_ARN" --query Name --output text)" = sentinelops/dev/control-runtime
```

Chọn đúng state local của A. Không chạy `terraform apply` trong runbook này.
Không dùng `get-secret-value` ra terminal, shell variable, file, log hay clipboard.
Tắt shell tracing, AWS CLI debug/history ghi request, session recorder ghi stdin;
không chạy workflow bằng môi trường CI đang in input. Kubernetes audit ở mức
Request/Response cho Secret có thể ghi nội dung; operator kiểm tra chính sách audit.

## 2. CA công khai RDS và render Job để review

```bash
CA_DIR="$(mktemp -d /tmp/sentinelops-rds-ca.XXXXXX)"
curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
  "https://truststore.pki.rds.amazonaws.com/${REGION}/${REGION}-bundle.pem" \
  --output "$CA_DIR/ca-bundle.crt"
openssl crl2pkcs7 -nocrl -certfile "$CA_DIR/ca-bundle.crt" |
  openssl pkcs7 -print_certs -noout
sha256sum "$CA_DIR/ca-bundle.crt"
kubectl --context "$CONTEXT" -n "$NS" create configmap control-rds-ca \
  --from-file="ca-bundle.crt=$CA_DIR/ca-bundle.crt" --dry-run=client -o yaml |
  kubectl --context "$CONTEXT" -n "$NS" apply -f -

mkdir -p artifacts/aws-demo/control
COMMON=(--host "$DB_HOST" --image "$CONTROL_IMAGE" --namespace "$NS" \
  --context "$CONTEXT" --region "$REGION" --ca-config control-rds-ca)
python3.11 scripts/aws_demo/control.py bootstrap "${COMMON[@]}" \
  > artifacts/aws-demo/control/bootstrap.rendered.json
```

CA là dữ liệu công khai; xác nhận nguồn CA/chứng thư theo quy trình operator. Job
dùng `ssl.create_default_context(cafile=...)`: kiểm tra chain và hostname DNS thật,
không downgrade sang `require`, không bỏ verify, không dùng IP/port-forward cho RDS.
Worker mount cùng CA và `SSL_CERT_FILE=/etc/sentinelops/tls/ca-bundle.crt` (renderer
đã hỗ trợ). Pod API/trial cần cùng mount + SSL_CERT_FILE. Manifest mẫu
`control-job.yaml` là JSON hợp lệ YAML, có placeholder để **không apply trực tiếp**.
Lệnh render chỉ tạo Job công khai; ConfigMap SQL và master Secret do CLI chuẩn bị
qua stdin khi execute. Không ghi Secret trong file rendered.

## 3. Bootstrap master → migration 001–006

```bash
python3.11 scripts/aws_demo/control.py bootstrap "${COMMON[@]}" \
  --master-secret-arn "$MASTER_ARN" --execute
```

CLI kiểm tra account của AWS caller bằng account của image ECR, kiểm tra ARN cùng
account/region; đọc RDS managed master secret vào memory rồi **operator tạo Secret
tạm `control-master-<id>`** key `master.json` bằng stdin JSON. Không truyền mật khẩu
qua argv/env/file. SQL và runner được mount từ ConfigMap `control-sql-<id>` vì
control-plane image hiện tại không đóng gói migrations. Job chạy đúng image đó,
UID/GID 10001, rootfs read-only, deadline 600s, không retry, không service token.

Thứ tự:
`001-deployment-roles.sql`, `002-incident-store.sql`, `003-health-worker.sql`,
`004-incident-consumer.sql`, `005-remediation.sql`, `006-quarantine-review.sql`.
Kết quả phải có 6 dòng `APPLIED` và `{"action":"bootstrap","status":"PASS"}`
(khoảng trắng JSON có thể khác). Core ledger **[1,2,3]**, remediation ledger **[1,2]**,
không phải version 6. Runtime còn `NOLOGIN`, không được tham gia owner/action roles.

Chi tiết bắt buộc review: checker cũ chạy dưới superuser; RDS master không phải
PostgreSQL superuser. Runner giữ nguyên file SQL, thêm `SET LOCAL ROLE control_owner`
sau CREATE SCHEMA trong 001 để GRANT USAGE dưới owner. Trong 005/006, runner chèn
GRANT owner membership của **chính master** thành INHERIT TRUE ở đầu transaction,
rồi RESET ROLE và khôi phục INHERIT FALSE trước COMMIT. Lỗi/ngắt kết nối rollback
cả quyền tạm; không cấp thêm quyền cho runtime. Advisory lock chặn hai Job cùng chạy.

CLI đọc logs chỉ có trạng thái, xóa Job/pod trước khi xóa master Secret và SQL
ConfigMap, kể cả khi lỗi. TTL 1 giờ chỉ là dự phòng. Kiểm tra không còn tài nguyên
tạm bằng metadata (không lấy data của Secret):

```bash
kubectl --context "$CONTEXT" -n "$NS" get jobs
kubectl --context "$CONTEXT" -n "$NS" get pods -l app.kubernetes.io/name=control-bootstrap
kubectl --context "$CONTEXT" -n "$NS" get secrets -o name
kubectl --context "$CONTEXT" -n "$NS" get configmaps -o name
```

**Không chạy lại bootstrap** nếu 001 đã commit. Mỗi migration có transaction riêng;
lỗi 005 không rollback 001–004. Dừng, giữ log `APPLIED`, review trạng thái/backup và
migration còn thiếu; không DROP schema/role, không dùng bootstrap để resume.
CLI in tên cleanup cụ thể. Nếu bị kill/mất mạng, xóa đúng Job trước, rồi Secret và
ConfigMap theo tên đó bằng các lệnh ở §7. Không xóa theo wildcard.

## 4. Bật login demo, ghi Secrets Manager và Secret runtime

```bash
python3.11 scripts/aws_demo/control.py runtime "${COMMON[@]}" \
  --master-secret-arn "$MASTER_ARN" --runtime-secret-arn "$RUNTIME_ARN" \
  --consumer worker --execute
```

Chỉ chạy lần đầu: CLI từ chối nếu Secrets Manager đã có version hoặc Secret K8s
đã tồn tại. Không dùng để rotate mù. Password được random trong memory và đưa qua
stdin vào Secret master tạm, key `runtime-password`; không in ra. Job dùng master
thực hiện `ALTER ROLE control_writer LOGIN ... VALID UNTIL '2026-11-25 00:00:00+00'`.
SQL chỉ chứa verifier SCRAM (không chứa plaintext password); Job thử đăng nhập thật
bằng password, kiểm tra TLS và quyền, sau đó CLI mới ghi runtime secret.
Không dùng user mới kế thừa control_writer: ngoại lệ demo yêu cầu **chính username
control_writer**. VALID UNTIL chỉ hạn chế xác thực password mới; không tự ngắt
session đang mở và không tự đổi thuộc tính thành NOLOGIN. Bắt buộc dọn ở §7.

Secrets Manager có **SecretString là JSON**:

| Key | Nội dung/consumer |
| --- | --- |
| `database-url` | DSN có username/password URL-encoded, `sslmode=verify-full`, cho API/trial |
| `worker-database-url` | DSN không có credential, cùng hostname/port/database và `sslmode=verify-full` |
| `username`, `password` | `control_writer` và password, tách riêng cho source policy worker |
| `expires-at` | `2026-11-25T00:00:00Z` |

K8s Secret **control-runtime** trong namespace worker có `database-url` = DSN
**không credential**, `password`, `username`, và `api-database-url` = DSN có
credential. Đây là yêu cầu code hiện tại: `SourcePolicy._remote_url` từ chối
credential trong URL, username phải `control_writer`, worker đọc key `password`.
Đặt URL có password vào key `database-url` của worker sẽ làm worker từ chối chạy.

Lệnh public endpoint cho renderer worker (không phải secret):

```bash
CONTROL_ENDPOINT="postgresql://${DB_HOST}:5432/sentinelops_control?sslmode=verify-full"
printf '%s\n' "$CONTROL_ENDPOINT"
# Truyền --control-database "$CONTROL_ENDPOINT" --database-ca-config control-rds-ca
# cho scripts/render_worker_manifests.py cùng các input worker đã được review riêng.
kubectl --context "$CONTEXT" -n "$NS" describe secret control-runtime
```

`describe secret` chỉ cho key/độ dài; không dùng `get secret -o yaml/json`, không
decode nội dung. Nếu bước runtime/publish/sync lỗi, CLI thử chạy Job `disable`
(NOLOGIN, PASSWORD NULL, ngắt session) trước cleanup. Nếu không revoke được, in
`URGENT`: operator phải chạy §7 ngay. Nếu bị kill giữa chừng thì không có đảm bảo
cleanup/revoke; áp dụng cùng quy trình. Secret có thể đã ghi một phần: không
thử bootstrap lại hoặc tự mở lại login khi chưa review.

### 4.1. Login action — ngoại lệ thứ hai đã duyệt

Owner/nvtank duyệt ngày **2026-11-18** cho trial/remediation dùng chính username
`control_action_writer`, giữ nguyên các grant của `005-remediation.sql` và phần
đọc của 006. Login này ghi remediation, không ghi incident và không dùng chung
identity với worker. Hạn password vẫn **2026-11-25 00:00 UTC**.

Sau bootstrap 001–006 và review, dùng cùng `COMMON` ở namespace bắt buộc
**sentinelops-b-actions**; không cần `--runtime-secret-arn`:

```bash
# Render offline để review, không đọc credential hay gọi tool cloud:
python3.11 scripts/aws_demo/control.py action-runtime "${COMMON[@]}" \
  > artifacts/aws-demo/control/action-runtime.rendered.json
# Operator thực hiện sau review:
python3.11 scripts/aws_demo/control.py action-runtime "${COMMON[@]}" \
  --master-secret-arn "$MASTER_ARN" --execute
kubectl --context "$CONTEXT" -n "$NS" describe secret remediation-runtime
```

Password random trong RAM, đưa qua stdin vào Secret master tạm key
`runtime-password`, được mount read-only trong Job như luồng `runtime`. Job kiểm
`pg_roles` để từ chối SUPERUSER/CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS, rồi chạy
`ALTER ROLE control_action_writer LOGIN PASSWORD '<SCRAM verifier>' VALID UNTIL
'2026-11-25 00:00:00+00'`. **RDS master không phải superuser**; PostgreSQL 16 cấm
ghi `NOSUPERUSER/NOREPLICATION/NOBYPASSRLS` trong ALTER ROLE, vì vậy chỉ kiểm các
thuộc tính này bằng `pg_roles`, không thêm chúng vào lệnh ALTER.

Job đăng nhập thật bằng password mới, xác minh TLS với CA/hostname và kiểm đủ
SELECT/INSERT trên năm bảng action, UPDATE đúng các cột states/targets theo 005;
không DELETE/TRUNCATE remediation, không ghi bảng core/incident, không là thành
viên `control_owner`, `control_writer`, `control_action_resolver`, không CREATE
schema/database. CLI chỉ tạo Secret sau khi Job PASS:

- Secret: `sentinelops-b-actions/remediation-runtime`.
- Key duy nhất: `action-database-url`.
- DSN: `postgresql://control_action_writer:<password URL-encoded>@<RDS host>:5432/sentinelops_control?sslmode=verify-full`.

Consumer action/trial phải bind đúng key và mount CA cùng `SSL_CERT_FILE` như §2.
Không tạo/ghi Secrets Manager cho action. CLI từ chối nếu Secret này đã tồn tại;
không rotate ngầm, không có `sync-runtime` cho credential action. Nếu mất bản K8s,
review thu hồi và tạo lại login với password mới; password cũ không được lưu để
đồng bộ. Không decode/in Secret ra terminal hay file.

Nếu Job hoặc việc tạo Secret thất bại, CLI thử `disable`, thu hồi **cả hai** demo
login và ngắt session, nên worker dùng `control_writer` cũng mất kết nối. Nếu
operator bị ngắt hoặc có `URGENT`, chạy §7 ngay, kiểm tra Secret có thể đã được
tạo trước khi response bị mất; review trước lần bật lại.

## 5. Đồng bộ lại / dùng cho API hoặc trial

Không có External Secrets/CSI; thay đổi Secrets Manager không tự cập nhật K8s.
Đồng bộ Secret worker hiện có (không đổi password trong DB):

```bash
python3.11 scripts/aws_demo/control.py sync-runtime "${COMMON[@]}" \
  --runtime-secret-arn "$RUNTIME_ARN" --consumer worker --execute
# Chỉ khi các Deployment tương ứng đã được apply trong đợt triển khai worker:
kubectl --context "$CONTEXT" -n "$NS" rollout restart deployment/sentinelops-health-worker deployment/sentinelops-incident-worker
kubectl --context "$CONTEXT" -n "$NS" rollout status deployment/sentinelops-health-worker --timeout=5m
kubectl --context "$CONTEXT" -n "$NS" rollout status deployment/sentinelops-incident-worker --timeout=5m
```

Owner đã chọn UI trial G6, không triển khai API always-on. Nếu trial chạy cùng
namespace worker, bind `CONTROL_DATABASE_URL` vào **api-database-url** của cùng
Secret, mount CA; không ghi đè key `database-url` đang dành cho worker.
Nếu triển khai API renderer hiện có trong **namespace riêng**, tạo Secret cùng
identity nhưng key `database-url` có credential bằng lệnh sau; không dùng consumer
api trong namespace đang có worker:

```bash
API_NS=sentinelops
kubectl --context "$CONTEXT" create namespace "$API_NS" --dry-run=client -o yaml |
  kubectl --context "$CONTEXT" apply -f -
kubectl --context "$CONTEXT" -n "$API_NS" create configmap control-rds-ca \
  --from-file="ca-bundle.crt=$CA_DIR/ca-bundle.crt" --dry-run=client -o yaml |
  kubectl --context "$CONTEXT" -n "$API_NS" apply -f -
python3.11 scripts/aws_demo/control.py sync-runtime --host "$DB_HOST" --image "$CONTROL_IMAGE" \
  --context "$CONTEXT" --region "$REGION" --namespace "$API_NS" \
  --runtime-secret-arn "$RUNTIME_ARN" --consumer api --execute
```

API renderer hiện tại chưa mount CA RDS: operator phải review CA mount/env trong
manifest trial/API trước apply. Không đổi TLS sang chế độ yếu hơn để pod ready.
Kiểm tra live worker kết nối RDS và incident/readiness riêng; Job PASS không chứng
minh Prometheus parity, model activation, UI authentication hay phục hồi rollback.

## 6. Model bundle — chờ bàn giao, không tự tạo model giả

Hiện **chưa có bundle**. Chờ release owner giao archive bất biến cho `de38fc6b` và
**SHA-256 đủ 64 ký tự từ nguồn độc lập đáng tin**. Không tự băm file rồi coi checksum
đó là bằng chứng đúng release. Script không giải nén/load pickle/joblib hay tự phê
duyệt model. Upload archive không materialize model PVC và không tạo release-ref.

```bash
BUNDLE='/absolute/path/REPLACE_RELEASE_ARCHIVE.tar.gz'
EXPECTED_SHA256='REPLACE_RELEASE_OWNER_64_HEX_SHA256'
# Mặc định: chỉ kiểm checksum local, thiếu bundle/sai checksum thì dừng trước AWS.
python3.11 scripts/aws_demo/upload_model.py --bundle "$BUNDLE" \
  --expected-sha256 "$EXPECTED_SHA256" --release de38fc6b \
  --bucket "$ARTIFACT_BUCKET" --account-id "$OWNER_ACCOUNT" --region "$REGION"

# Sau review, upload + checksum dịch vụ + HEAD version + download/hash readback:
python3.11 scripts/aws_demo/upload_model.py --bundle "$BUNDLE" \
  --expected-sha256 "$EXPECTED_SHA256" --release de38fc6b \
  --bucket "$ARTIFACT_BUCKET" --account-id "$OWNER_ACCOUNT" --region "$REGION" \
  --execute > artifacts/aws-demo/control/model-upload-receipt.json
```

Key là `models/de38fc6b/<sha256>/<tên-archive>`, một file không rỗng tối đa **1 GiB**
(giới hạn riêng của script; chưa hỗ trợ multipart). S3 phải bật versioning và mặc
định SSE-KMS; script dùng default key của bucket, không cần output KMS mới. Gửi
checksum SHA256 native S3; không coi ETag/metadata là checksum. `If-None-Match: *`
từ chối ghi đè key đã có; lỗi trùng key cần kiểm tra version/receipt trước, không
tắt guard. Thiếu quyền download/KMS sau upload có thể để lại object chưa verify;
giữ receipt nếu có và review đúng version, không tự xóa/activate.

Operator cần S3 GetBucketVersioning/GetEncryptionConfiguration/PutObject/
GetObject/GetObjectVersion và KMS GenerateDataKey/Decrypt trên bucket/key này;
không dùng IAM role data-worker để upload vì nó chỉ được đọc `models/`. Không thêm
IAM/Terraform trong bộ này. Phí phát sinh gồm storage/version, PUT/HEAD/GET, KMS và
download readback; xem [S3 pricing](https://aws.amazon.com/s3/pricing/).
Khuyến nghị owner theo dõi access logs, CloudTrail S3 data events và CloudWatch
metrics trong phạm vi observability/budget đã review; không tự bật bằng runbook.

## 7. Thu hồi và cleanup — chạy cả khi destroy bị chặn

Dừng các consumer trước (chỉ các Deployment đã triển khai), gồm action/trial nếu
đang chạy, sau đó revoke cả hai login:

```bash
kubectl --context "$CONTEXT" -n "$NS" scale deployment/sentinelops-health-worker deployment/sentinelops-incident-worker --replicas=0
python3.11 scripts/aws_demo/control.py disable "${COMMON[@]}" \
  --master-secret-arn "$MASTER_ARN" --execute
kubectl --context "$CONTEXT" -n "$NS" delete secret control-runtime remediation-runtime --ignore-not-found
# Nếu đã tạo bản cho API ở §5, dừng trial/API rồi xóa bản đó:
kubectl --context "$CONTEXT" -n sentinelops delete secret control-runtime --ignore-not-found

# Sau self-review + Codex xhigh review teardown: recovery window 7 ngày, không force-delete.
# Secret resource được Terraform quản lý: A ghi nhận deletion vào kế hoạch destroy,
# tránh apply thông thường tái tạo/mở lại secret; xử lý pending deletion nếu destroy báo lỗi.
aws --region "$REGION" secretsmanager delete-secret --secret-id "$RUNTIME_ARN" --recovery-window-in-days 7
```

Job `disable` chạy `ALTER ROLE ... NOLOGIN PASSWORD NULL` cho **control_writer và
control_action_writer**, rồi `pg_terminate_backend` cho mọi session của cả hai.
Các bước NOLOGIN/PASSWORD NULL được commit trước bước terminate, để lỗi ngắt
session không rollback việc khóa login. Nếu master không có quyền
ngắt một session, Job fail: operator có quyền RDS phù hợp phải xác nhận không còn
session. Password expiry không thay thế bước này. Không xóa RDS managed master
secret bằng tay. Phần infrastructure/snapshot/KMS/S3 teardown theo AWS_BASELINE và
AWS_DEMO_PLAN §8, ngoài phạm vi bộ lệnh này.

Nếu CLI bị ngắt và còn tài nguyên tạm, dùng **đúng tên thực tế từ dòng Job/Cleanup**
(thay placeholder, không chạy wildcard), theo thứ tự:

```bash
kubectl --context "$CONTEXT" -n "$NS" delete job/control-REPLACE_ACTION_AND_ID --ignore-not-found --wait=true
kubectl --context "$CONTEXT" -n "$NS" delete secret/control-master-REPLACE_ID --ignore-not-found
kubectl --context "$CONTEXT" -n "$NS" delete configmap/control-sql-REPLACE_ID --ignore-not-found
```

Không xóa CA ConfigMap khi còn consumer. Không xóa model versions/snapshot/KMS
key nếu owner chưa chốt giữ/hủy dữ liệu. Hạn login UTC tương ứng **07:00 ngày
2026-11-25 Asia/Bangkok**; đặt lịch thu hồi ngay sau demo, không chờ password hết hạn.

## 8. Kiểm tra cục bộ đã chuẩn bị

Từ PowerShell, không dùng Python Windows của repo:

```powershell
wsl.exe -d Ubuntu -- sh /mnt/d/year4/AI4life/SentinelOps/scripts/aws_demo/check_local.sh
```

Script archive HEAD sang `/tmp/sentinelops-control-check.*`, copy thêm bộ aws_demo
chưa tracked, `chown` bản sao, chạy `python:3.11-slim-bookworm` network none với
Docker CLI/socket để tạo PostgreSQL **17-alpine đã có sẵn local**, không host ports,
không AWS/kubectl. Không tự pull image; cần image Python và PostgreSQL trên máy.
Không đưa credentials/state/node_modules vào bản sao. Bản sao không có `.git` nên
không có git dubious ownership. SQL/runner là bản source hiện tại chưa commit;
không có commit/push trong bộ kiểm tra.

Behavioral checks bao gồm offline không gọi tool, reject checksum sai,
readback hỏng, sync worker/API đúng dạng DSN, revoke khi publish secret thất bại,
reject session DB không TLS; action-runtime tạo đúng Secret/DSN URL-encoded sau
Job, không ghi Secrets Manager, từ chối Secret có sẵn/namespace khác, thu hồi khi
publish lỗi, kiểm thuộc tính role/TLS/quyền action. Fixture PostgreSQL chạy toàn bộ migrations dưới
master CREATEROLE/CREATEDB **không superuser**, kiểm ledger, SCRAM login, rollback
quyền tạm khi lỗi 005, từ chối sai DB/replay/quyền runtime. Fixture thử SCRAM bằng
cả hai login, ghi thật năm bảng remediation/UPDATE đúng cột, từ chối ghi incident,
owner/CREATE/quyền ngoài 005; cố tình thay đổi grant để kiểm query phát hiện sai
quyền. Teardown kiểm NOLOGIN/PASSWORD NULL và ngắt hai session đang mở, với
`pg_signal_backend` cấp riêng cho fixture master để mô phỏng khả năng terminate
của RDS master; master vẫn không superuser. Fixture local không cấu hình TLS,
không phải bằng chứng kết nối RDS/TLS; các kiểm tra TLS ở đây dùng mock, Job cloud
vẫn bắt buộc CA/hostname verification và kiểm `pg_stat_ssl` cho cả hai kết nối.
Đây là kiểm tra phần mềm, không phải chứng nhận RDS rds_superuser, CA thực tế,
Secrets Manager/KMS, EKS hoặc model bundle. Checker cũ
`scripts/check_deployment_bootstrap.py` chỉ kiểm core 001–004 trên PostgreSQL 16
với fixture superuser; bộ mới bổ sung 005–006 và master bị hạn chế, không sửa checker.

Nguồn: [AWS_BASELINE](../../docs/runbooks/AWS_BASELINE.md),
[AWS_DEMO_PLAN](../../docs/delivery/AWS_DEMO_PLAN.md),
[RDS PostgreSQL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_PostgreSQL.html),
[S3 data protection](https://docs.aws.amazon.com/AmazonS3/latest/userguide/data-protection.html).
