# Báo cáo triển khai AWS và tính năng tự sửa LOW — 18/11/2026

Người thực hiện: Claude (lead), cùng 11 Codex worker, theo chỉ đạo của nvtank. Nhánh: `deploy/aws-demo`.
Không ghi account ID, email hay IP trong file này. Các giá trị đó nằm trong `infra/aws/terraform.tfvars` (gitignored).

## 1. Tóm tắt

| Hạng mục | Kết quả |
|---|---|
| Hạ tầng AWS (Terraform) | **Đã lên** `ap-southeast-1`: EKS 1.34 (2 node `m7i-flex.large`), RDS PostgreSQL 16.15 `db.t4g.micro` single-AZ, ECR, S3, KMS, IAM, VPC, budget. 77/78 tài nguyên; thiếu CloudWatch dashboard (bug IaC, §7) |
| Image | `application`, `control-plane`, `payment-regression` trên ECR, pin theo digest |
| Workload trên EKS | 3 service + Postgres commerce + OTel Collector + Prometheus + Jaeger + load generator; 7/7 pod chạy |
| Control plane | RDS đã bootstrap (migration 001–006), login runtime demo, health worker và incident worker chạy thường trực |
| Phát hiện sự cố trên EKS | **Đạt**: tiêm +950 ms vào Payment → incident OPEN → RCA_READY → ESCALATED |
| Tính năng tự sửa LOW ≥ 0.80 | **Code xong**, toàn bộ suite **2467 test pass**, image đã build. **Chưa chạy trọn vòng trên EKS**: thiếu model bundle, duyệt known-good, và kết nối cluster ổn định (§9) |

## 2. Phạm vi và quyết định đã chốt

1. Chủ repo thu hẹp phạm vi còn **một tính năng** để demo. Quy tắc:

   | Điều kiện | Hành động |
   |---|---|
   | severity = **low**, điểm khớp runbook **≥ 0.80**, cách runner-up ≥ 0.15, runbook chạy được | **Tự chạy, không cần duyệt** |
   | low, 0.50 ≤ điểm < 0.80 | On-call duyệt (đợt 2, chưa làm) |
   | điểm < 0.50 hoặc severity ≠ low | Báo engineer tự sửa |

   Medium/high chưa được tự chạy. Quy tắc "high tái phát nhiều thì được sửa" để giai đoạn sau.
2. **Lỗi demo:** Payment bị deploy image lỗi (`payment-regression`, +0.95 s/request). Được chọn vì:
   - Nhiều dữ liệu đầu-cuối nhất: 10 trial G6 rollback, 0 unsafe.
   - Cụ thể: có digest xấu/tốt và deployment change thật.
   - Tái hiện được: Dockerfile đã review.
   - Trên EKS đo được p95 Payment ≈ 981 ms, Checkout ≈ 981 ms, không có 5xx. Hai giá trị này rơi đúng band low.
3. **Severity theo ảnh hưởng khách** (`severity/v2`):
   - critical: Checkout 5xx ≥ 20% (giữ nguyên).
   - high: Checkout 5xx ≥ 5%, hoặc Checkout p95 > 1500 ms, hoặc ≥ 2 root.
   - low: root p95 ∈ (750, 1250], lỗi ≤ 5%.
   - medium: phần còn lại.

   Lý do: Checkout gọi đồng bộ Payment/Inventory, nên với luật cũ mọi sự cố đều thành high.
4. **Điểm khớp là deterministic**, không phải xác suất đã hiệu chỉnh. Tự chạy vẫn đi qua mọi guard của executor: H4, known-good, dry-run, 1 lần/incident, cooldown, kill switch. Còn đúng một bước người: duyệt image known-good **một lần**, trước demo.
5. **Kiểm soát:**
   - Claude là người duy nhất ghi lên AWS.
   - Plan được Codex review trước khi apply. Chủ repo cho chạy ở mức `high`, thay cho mức `xhigh` ghi trong AWS_DEMO_PLAN.
   - Không bật Cognito (quyết định owner `f7244d2`).
   - Budget $80 cảnh báo qua email.
   - Thêm ngoại lệ demo thứ hai: login `control_action_writer` hết hạn 25/11 00:00 UTC (nvtank duyệt).

## 3. Hạ tầng đã triển khai

Nguồn IaC: `main@de4d2ba` + merge `origin/fix/aws-demo-live@0713112`. Nhánh này chứa bản sửa sizing cho gói Free, kết nối RDS, runtime secret và addon EBS CSI.

| Bước | Kết quả |
|---|---|
| `terraform validate` / `fmt` / `test` | PASS, 9/9 test mock |
| `scripts/check_aws_iac.py` + 313 pytest IaC (Codex `iac`) | PASS, 16/16 guard |
| Plan | 78 create, 0 change, 0 destroy |
| Codex `reviewer` | **BLOCK**, 4 điểm. Xử lý ở §6 |
| Apply lần 1 | 76 tài nguyên. RDS lỗi: `Cannot find version 16.10` |
| Apply lần 2 (`database_engine_version = "16.15"`) | RDS available sau 8m33s. Dashboard lỗi (§7) |
| Access entry `AmazonEKSClusterAdminPolicy` cho operator | Tạo bằng CLI, tag `Expires=2026-11-24` |
| Endpoint EKS | Public chỉ `/32` của operator, private bật. Đã đổi CIDR một lần khi IP operator thay đổi (apply có target, chỉ vào cluster) |
| Backup state | Bucket artifacts, prefix `terraform-state/` |

Cấu hình chi phí: RDS `db.t4g.micro`, 20 GB cố định, `multi_az=false`, backup 1 ngày; node 2×`m7i-flex.large`; 2 NAT; log 7 ngày.

Reviewer ước tính **≈ $12,16/ngày**. Credit gói Free lúc kết thúc phiên còn **$140**, vì AWS cộng thêm credit theo hoạt động.

Có thêm **budget toàn tài khoản** tạo bằng CLI: $80, không lọc tag, không tính credit, cảnh báo ở 25/50/80% và khi dự báo vượt 100%. Lý do: budget trong IaC lọc theo tag nên không thấy chi phí node EC2.

## 4. Image (ECR, pin theo digest)

| Image | Tag | Digest |
|---|---|---|
| application | `git-d200fc0` | `sha256:e520e70255f4…` |
| payment-regression (chung repo `application`) | `git-d200fc0-payment-regression` | `sha256:766dd5f5a32e…` |
| control-plane, bản sửa health worker | `git-e766681` | `sha256:6014b25f7f14…` (đang chạy health/incident worker) |
| control-plane, bản có tính năng | `git-d1eee79` | `sha256:2330dd18e674…` |

Image bên thứ ba pin theo digest: postgres 16-alpine, otel-collector 0.162.0, prometheus v3.5.0, jaeger 2.21.0, collector-contrib.

## 5. Workload và control plane trên EKS

Namespace `sentinelops-b-actions` (giống kind).

| Cổng | Bằng chứng |
|---|---|
| P3 — workload + traffic | 7/7 pod Running. Traffic: Checkout 90, Inventory 181, Payment 181 req/phút. p95 nền: 42 / 4,7 / 4,8 ms |
| P4 — control DB | Job bootstrap: 6/6 migration `APPLIED`, ledger core `[1,2,3]`, remediation `[1,2]`. Job runtime: login thật qua TLS, đúng ranh giới quyền. Secrets Manager `sentinelops/dev/control-runtime` có version `AWSCURRENT`; K8s Secret `control-runtime` đã tạo. Tài nguyên tạm (master secret, SQL ConfigMap) tự dọn |
| Health worker | READY, stream `6028fde7…`, commit bucket mỗi phút, 0 restart |
| Incident worker | READY, consumer `37c81847…`, đọc toàn bộ bucket, `actions_allowed=false` |
| **Phát hiện thật** | Tiêm +950 ms vào Payment lúc 06:37:49Z → `INC-3100e1ec…`: rev1 OPEN → rev2 RCA_READY → rev3 **ESCALATED**. RCA từ chối kết luận vì lỗi là cờ fault (không có deployment change) và chưa có model. Đây là hành vi đúng. Lỗi đã được gỡ lúc 06:43:15Z |

Secret commerce và runtime được sinh ngẫu nhiên trong bộ nhớ, truyền qua stdin, không ghi ra đĩa hay log. Observer token dùng Secret `kubernetes.io/service-account-token` do Kubernetes tự cấp.

## 6. Review và phản biện

### Codex reviewer (BLOCK) và cách xử lý

| # | Phát hiện | Đánh giá của Claude | Xử lý |
|---|---|---|---|
| B1 | Không có access entry cho operator | Đúng, nhưng là bước sau apply theo thiết kế | Tạo access entry bằng CLI ngay sau apply |
| B2 | Budget lọc tag nên không thấy chi phí EC2 node, và tính cả credit | **Đúng, quan trọng** | Thêm budget toàn tài khoản (§3) |
| B3 | IAM đọc Logs/ECR rộng hơn cần | Đúng về nguyên tắc; tài khoản trống, demo ngắn | Chấp nhận rủi ro, đưa vào PR IaC tiếp theo |
| B4 | Destroy xóa KMS key trong khi vẫn giữ snapshot | Đúng; là quyết định lúc teardown | Quyết định trước khi destroy (AWS_DEMO_PLAN §8) |

### Phản biện 7 kế hoạch Codex (giai đoạn lập kế hoạch)

- 6 kế hoạch con cộng lại 132–197 ngày công và mâu thuẫn nhau: 3 định nghĩa "low" khác nhau, TTL duyệt 60 s / 10 phút / 15 phút.
- Không kế hoạch nào có bộ phân loại *loại* nguyên nhân.
- Testbed dùng cờ fault nằm trong RAM, nên restart "chữa" được mọi lỗi giả lập.
- Fingerprint tái phát khóa theo digest nên gần như không bao giờ khớp.
- Từ đó phạm vi được thu về một tính năng (§2).

## 7. Lỗi tìm ra khi triển khai và đã sửa

| Lỗi | Nguyên nhân | Sửa |
|---|---|---|
| RDS `Cannot find version 16.10` | AWS đã ngừng 16.10 ở region này | `database_engine_version = "16.15"` (tfvars) |
| CloudWatch dashboard `Should NOT have more than 4 items` | Widget RDS có 5 phần tử (`{unit}`) | **Chưa sửa.** Cần PR cho `infra/aws/dashboard.tf` vì có test Terraform canh cấu trúc dashboard |
| Health worker crash `requires a credential-free loopback Prometheus endpoint` | `workers/health.py` chỉ truyền source policy khi `remote`; `in-cluster` rơi về `local` | `!= "local"` (commit `e766681`) |
| Job runtime `permission denied to alter role` | RDS master không phải superuser; PostgreSQL 16 cấm ghi `NOSUPERUSER/NOREPLICATION/NOBYPASSRLS` trong `ALTER ROLE` | Bỏ các thuộc tính đó, kiểm bằng `pg_roles` |
| `put-secret-value` báo `Invalid JSON` | AWS CLI v2 trong WSL không đọc `--cli-input-json file:///dev/stdin` khi stdin là pipe | File 0600 trên tmpfs `/dev/shm`, xóa ngay sau khi dùng |
| Incident worker Pending | Thiếu PVC `deployment-change-artifacts` | Tạo PVC gp2 |
| Image `control-plane` thiếu file cho controller | Dockerfile/dockerignore là allow-list | Thêm `run_remediation_controller.py`, `severity_policy.json`, `runbook_catalog.json`; cập nhật test packaging |
| Mất kết nối kubectl | IP egress đổi (WARP, sau đó ISP luân phiên 2 IP) | Đã đổi `/32`; IP thứ hai chờ quyết định (§9) |

## 8. Tính năng tự sửa LOW ≥ 0.80

Interface chung: `docs/design/AUTO_RUNBOOK_INTERFACE.md`.

| Thành phần | File | Test |
|---|---|---|
| Severity theo ảnh hưởng (`severity/v2`) | `configs/severity_policy.json`, `src/sentinelops/incidents/severity.py`, `rca_replay.py` | 73 test riêng |
| Trích triệu chứng + catalog 5 runbook + chấm điểm/quyết định | `src/sentinelops/remediation/matching.py`, `configs/runbook_catalog.json`, `contracts/runbook-catalog.schema.json` | 82 test riêng |
| Controller + hook `authorize` (kiểm lại ngay trước PATCH) + kill switch | `remediation/controller.py`, `executor.py`, `scripts/run_remediation_controller.py` | 68 test controller/executor |
| Manifest EKS cho controller + script demo 3 lượt | `scripts/render_worker_manifests.py`, `scripts/run_runbook_demo.py` | 118 test renderer/demo |
| Login `control_action_writer` + Secret `remediation-runtime` | `scripts/aws_demo/control.py`, `control_job.py` | 11 test + fixture PostgreSQL |
| Tooling deploy | `scripts/aws_demo/*` (render, bootstrap, upload model, build image, traffic) | — |

**Toàn bộ suite trên cây đã tích hợp: 2467 passed** (baseline 2225); `validate_contracts.py`: 35 schema + 16 sample + 3 config.

## 9. Việc còn lại để chạy trọn vòng tự sửa trên EKS

1. **Kết nối cluster ổn định.** ISP của operator luân phiên 2 IP egress, chỉ một IP được phép, nên khoảng 1/3 request kubectl bị timeout. Cần chủ repo quyết: thêm IP thứ hai vào `cluster_public_access_cidrs`, hoặc dùng đường mạng có một IP cố định.
2. **Model bundle `de38fc6b`** từ teammate. Upload bằng `scripts/aws_demo/upload_model.py` (có kiểm checksum). H4 bắt buộc có model.
3. Nâng incident worker lên `control-plane@2330dd18…` (tính năng severity). Lệnh đã chạy nhưng timeout do mục 1.
4. Chạy `control.py action-runtime` để tạo login `control_action_writer` và Secret `remediation-runtime`.
5. Thu bằng chứng trên image Payment khỏe, rồi **nvtank duyệt known-good** (`scripts.approve_known_good`).
6. Deploy `remediation-controller` với `--policy-enabled`, rồi chạy `scripts/run_runbook_demo.py` 3 lượt: deploy regression → incident low → điểm ≥ 0.80 → tự rollback → `recovered` + `telemetry_verified`. Thêm 1 lượt bật kill switch.
7. Mở PR cho các bản sửa ở §7 để B review; sửa `dashboard.tf`.

## 10. Hạn cứng và teardown

- **24/11:** thu hồi access entry EKS (`aws eks delete-access-entry`).
- **25/11 00:00 UTC:** login `control_writer` và `control_action_writer` hết hạn. Chạy `control.py disable` và xóa secret runtime.
- Teardown theo `AWS_DEMO_PLAN.md` §8: xóa image ECR, làm rỗng bucket versioned, tắt deletion protection RDS, quyết định giữ hay hủy snapshot/KMS (B4), `terraform destroy`, xóa log group RDS, xóa budget toàn tài khoản tạo bằng CLI.
- Kiểm credit mỗi ngày: `aws freetier get-account-plan-state`.
