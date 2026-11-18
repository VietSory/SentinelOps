# Kế hoạch demo trên AWS — 5 ngày

Ngày chốt: **2026-11-17**, quyết định trực tiếp của owner. Thay ghi chú "AWS offline only"
trong [PROJECT_STATUS](../PROJECT_STATUS.md) cho phạm vi demo này. Không thay
[kế hoạch giao hàng](DELIVERY_PLAN.md), ADR Accepted hay tiêu chí G0–G8. Theo dõi trao đổi ở
[issue #67](https://github.com/VietSory/SentinelOps/issues/67).

## 1. Mục tiêu

Trong **tối đa 5 ngày làm việc**, demo các feature của vòng khép kín trong
[PRODUCT_SPEC](../architecture/PRODUCT_SPEC.md) **chạy thật trên AWS, không dùng mock**:

> gây lỗi → phát hiện → chỉ ra nguyên nhân → ghi incident (IER, revision, bằng chứng) →
> giải thích → người vận hành duyệt rollback → rollback Payment về image tốt → xác minh hồi phục,
> xem được trên giao diện.

Owner cho phép **không cần đạt các thông số chất lượng** (Q-04…Q-10, G6–G8). Demo không thay
nghiệm thu; giới hạn được nói rõ ở mục 6.

Hoàn thành khi kịch bản demo chạy **3 lượt liên tiếp** trên EKS mà không phải sửa tay.

## 2. Đã chốt

| Mục | Giá trị |
| --- | --- |
| Region | `ap-southeast-1` |
| Account | account của owner; Terraform chỉ chạy trên account này (`allowed_account_ids`) |
| Người apply | **A**, owner giao cho lần demo này. A không sửa IaC của B; sửa thì gửi PR để B review |
| Gói AWS | **Free plan**, owner chọn giữ (2026-11-17). EC2 chỉ nhận loại máy free-tier; RDS từ chối `db.t4g.medium` (`FreeTierRestrictionError`). Hết credit thì account bị khóa |
| Cluster | EKS 1.34, 2 node `m7i-flex.large` (2 vCPU, 8 GB), tối đa 2 (quota 5 vCPU, không xin tăng); cập nhật node kiểu `MINIMAL` |
| Control DB | RDS PostgreSQL `db.t4g.micro`, một AZ, 20 GB, không tự mở rộng (biến `database_multi_az`, `database_storage_autoscaling` của PR #68) |
| Truy cập cluster | Endpoint public chỉ cho IP `/32` của người vận hành; access entry tạo bằng CLI ngoài Terraform |
| State Terraform | Local trên máy A, sao lưu vào bucket artifacts dưới `terraform-state/` |
| Ngân sách | $150; chưa đặt email cảnh báo |
| Bedrock | Tắt. Giải thích dùng backend cố định dựa trên bằng chứng (chạy thật, không phải LLM) |
| Model | `de38fc6b`, release đã được D-27 duyệt có điều kiện |
| Người chạy phần AWS của B | **A**, owner giao ngày 2026-11-18 ("chạy hết phần của B trên aws"): đẩy ECR, triển khai stack, worker, API, tạo Secret. Sửa code vùng B vẫn qua PR để B review; mọi thao tác ghi lên AWS qua self-review và Codex `xhigh` |
| Login runtime DB | **Ngoại lệ cho demo:** bật `LOGIN` có hạn (2026-11-25 00:00 UTC) trên chính role `control_writer`, vì `src/sentinelops/workers/source_policy.py` bắt buộc username `control_writer`; khác runbook (nhóm `NOLOGIN` + login riêng). API và worker dùng chung identity này. **Ngoại lệ thứ hai, owner/nvtank duyệt 2026-11-18:** action `action-runtime` bật `LOGIN` cùng hạn trên chính `control_action_writer` để trial/remediation ghi bảng `sentinel_remediation` theo `005-remediation.sql`, giữ identity action riêng với incident worker; không có owner membership, CREATE schema/database hay quyền ghi incident. Password random trong RAM, SQL chỉ chứa SCRAM verifier, thử đăng nhập TLS và kiểm quyền trước khi tạo K8s Secret `sentinelops-b-actions/remediation-runtime`, key `action-database-url` (credential URL-encoded, `sslmode=verify-full`); không ghi Secrets Manager mới. RDS master không phải superuser: kiểm thuộc tính bằng `pg_roles`, không ghi `NOSUPERUSER/NOREPLICATION/NOBYPASSRLS` trong ALTER ROLE. Teardown `disable` tắt cả hai login, xóa password và ngắt session |

## 3. Kiến trúc demo

- **Trong EKS:** Checkout, Payment, Inventory, PostgreSQL commerce, OTel Collector,
  **Prometheus trong cluster**, Jaeger; API, health worker, incident worker dùng image
  `control-plane` (đã có script worker và thư viện ML).
- **Ngoài EKS (Terraform):** RDS control PostgreSQL (micro, một AZ), S3 artifacts/model store, ECR, KMS, IAM.
- **Đường số liệu:** dịch vụ → Collector → Prometheus → health worker, đúng profile
  `prometheus-increase-60s-5s/v2` mà model và parity đã dùng. Đường CloudWatch/X-Ray chưa
  từng chạy thật nên để ngoài demo.

## 4. Lịch

| Ngày | A | B | Điều kiện xong |
| --- | --- | --- | --- |
| 1 | `plan`/`apply`; quyền `kubectl` cho A và B; sao lưu state | Build và đẩy image `application`, `control-plane` lên ECR | `kubectl get nodes` thấy 2 node Ready; image có digest trong ECR |
| 2 | Kiểm tra số liệu: bucket Prometheus đủ, đúng nhãn, đúng service | Triển khai service, PostgreSQL commerce, Collector, Prometheus, Jaeger | Ba service ready, có traffic, Prometheus có số liệu |
| 3 | Tạo login runtime kế thừa `control_writer` (bootstrap chỉ tạo role `NOLOGIN`), ghi DSN TLS vào secret `control-runtime`, đưa vào Kubernetes Secret `control-runtime` key `database-url`; kiểm tra một incident đi trọn đường | API, health worker, incident worker trên EKS; đăng nhập UI (mục 5) | Workload kết nối RDS bằng login runtime (không dùng master); một lỗi Payment thật tạo IER đọc được trên UI |
| 4 | Kiểm tra bằng chứng rollback và audit | Rollback thật trên EKS; operator duyệt known-good trước | `recovered` và `telemetry_verified`; một lượt failed-verify chuyển `escalated` |
| 5 | Viết kịch bản demo và con số kỳ vọng | Tập dượt 3 lượt, khóa phiên bản | 3 lượt liên tiếp không sửa tay |

Hủy hạ tầng ngay sau buổi demo theo [AWS_BASELINE § Teardown](../runbooks/AWS_BASELINE.md#teardown)
và checklist ở mục 8.

## 5. Quyết định còn mở

| Quyết định | Phương án | Ai quyết, trước khi nào |
| --- | --- | --- |
| Đăng nhập UI | **Owner chốt 2026-11-18:** dùng UI chỉ đọc của pod trial G6 trên EKS, truy cập qua `kubectl port-forward` (token operator trong pod), không bật Cognito, không chạy API always-on trên EKS. Health/incident worker always-on vẫn chạy trên RDS để phát hiện thật. UI và receipt của trial là demo-only (`q09_eligible: false`) | Đã chốt |
| Manifest worker | Có `scripts/render_worker_manifests.py` (`915377d`), dùng image `control-plane`; **code-only**, PR #70 OPEN, chưa merge; chưa runtime EKS, chưa apply/gọi AWS | B, ngày 3 |
| Đưa DSN runtime vào pod | Repo không có External Secrets/CSI. (a) Operator tạo Kubernetes Secret thủ công từ secret `control-runtime`; (b) thêm đồng bộ. Demo: (a) | A đề xuất, B review, trước ngày 3 |
| Bộ dựng phần dịch vụ | Đã chọn `kubernetes_testbed.py --target eks` (`c018fad`), có Prometheus trong cluster; **code-only**, PR #70 OPEN, chưa merge; chưa runtime EKS, chưa apply/gọi AWS | B, ngày 2 |
| Người duyệt known-good | Owner hoặc nvtank, phải có mặt ngày 4 | Owner |

## 6. Giới hạn nói rõ khi demo

- Incident do luật SLO mở; IF và XGBoost cho điểm thật làm bằng chứng bên trong incident.
- Các con số chất lượng là kết quả phát triển (`ald`, G6 local), không phải nghiệm thu.
- Số liệu qua Prometheus trong cluster, chưa qua CloudWatch/X-Ray (G7 chưa đạt).
- Giải thích không dùng LLM.

## 7. Rủi ro và phương án lùi

| Rủi ro | Xử lý |
| --- | --- |
| Hết bộ nhớ trên 2 node | Ước tính toàn bộ stack khoảng 4 GB trên khoảng 14 GB dùng được; giảm replica nếu cần |
| RDS micro (1 GB RAM) chậm hoặc hết kết nối | Demo chỉ có một API và hai worker; theo dõi `db_connections` |
| Hết credit của Free plan giữa tuần | Khoảng $10–12/ngày (EKS, 2 NAT, 2 node, RDS micro); kiểm tra credit còn lại mỗi sáng trong Billing |
| Parity trên EKS không đạt ngay | Ngày 2–3 có dư; demo không phụ thuộc parity đạt ngưỡng |
| Rollback trên EKS chưa từng chạy | Dành trọn ngày 4 |
| Trễ quá ngày 5 | Lùi theo bậc: trọn vòng trên EKS → phát hiện tới giải thích trên EKS, rollback trên cluster local → trọn vòng trên local |

## 8. Checklist dọn dẹp sau demo

Mỗi bước tạo/xóa tài nguyên AWS qua self-review và Codex review trước khi chạy. Từ review Codex
2026-11-17 (PR #68), `destroy` sẽ bị chặn hoặc để lại tài nguyên tính tiền nếu bỏ qua các bước sau:

| Bước | Lý do |
| --- | --- |
| Owner chốt: **giữ hay hủy dữ liệu đã mã hóa** | Snapshot RDS cuối, bucket artifacts và bản sao state cùng dùng KMS key chính. `destroy` đưa key vào hàng chờ xóa: dữ liệu không đọc được ngay, mất hẳn sau 30 ngày. Giữ dữ liệu thì phải giữ key |
| **A** thu hồi access entry của từng người: `access-entry.sh revoke arn:aws:iam::<account>:user/<user>` cho `hviet` và user của B, rồi xác nhận `aws eks list-access-entries` không còn chúng. **Hạn chót 2026-11-24** kể cả khi chưa `destroy` | Tạo ngoài Terraform; tag `Expires` không tự thu hồi quyền cluster-admin |
| **A** chạy action `disable` để tắt cả `control_writer` và `control_action_writer` (`NOLOGIN PASSWORD NULL`, ngắt mọi session; hạn password 2026-11-25 00:00 UTC), xóa secret `sentinelops/dev/control-runtime` cùng Kubernetes Secrets `control-runtime` và `sentinelops-b-actions/remediation-runtime` | Secret runtime tạo bởi Terraform nhưng giá trị do script ghi; cả hai login phải tắt kể cả khi `destroy` bị chặn; action không có Secrets Manager riêng |
| Xóa image trong 2 repo ECR | Repo không có `force_delete`; còn image thì `destroy` lỗi |
| Làm rỗng bucket artifacts, kể cả mọi version | Bucket versioned, không có `force_destroy` |
| Apply `database_deletion_protection = false` (plan riêng, review) | RDS đang bật chống xóa |
| `terraform destroy`, rồi kiểm tra lại không còn NAT, EIP, node, RDS | Các tài nguyên tính tiền theo giờ |
| Xóa log group `/aws/rds/instance/sentinelops-control-dev/postgresql` | RDS tự tạo, Terraform không quản lý |
| Xóa snapshot RDS cuối nếu owner chọn hủy dữ liệu | Snapshot tính tiền lưu trữ |
