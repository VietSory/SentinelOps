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
| Cluster | EKS 1.34, 2 node `t3.large`, tối đa 2 (quota account 5 vCPU, không xin tăng) |
| Truy cập cluster | Endpoint public chỉ cho IP `/32` của người vận hành; access entry tạo bằng CLI ngoài Terraform |
| State Terraform | Local trên máy A, sao lưu vào bucket artifacts dưới `terraform-state/` |
| Ngân sách | $150; chưa đặt email cảnh báo |
| Bedrock | Tắt. Giải thích dùng backend cố định dựa trên bằng chứng (chạy thật, không phải LLM) |
| Model | `de38fc6b`, release đã được D-27 duyệt có điều kiện |

## 3. Kiến trúc demo

- **Trong EKS:** Checkout, Payment, Inventory, PostgreSQL commerce, OTel Collector,
  **Prometheus trong cluster**, Jaeger; API, health worker, incident worker dùng image
  `control-plane` (đã có script worker và thư viện ML).
- **Ngoài EKS (Terraform):** RDS control PostgreSQL, S3 artifacts/model store, ECR, KMS, IAM.
- **Đường số liệu:** dịch vụ → Collector → Prometheus → health worker, đúng profile
  `prometheus-increase-60s-5s/v2` mà model và parity đã dùng. Đường CloudWatch/X-Ray chưa
  từng chạy thật nên để ngoài demo.

## 4. Lịch

| Ngày | A | B | Điều kiện xong |
| --- | --- | --- | --- |
| 1 | `plan`/`apply`; quyền `kubectl` cho A và B; sao lưu state | Build và đẩy image `application`, `control-plane` lên ECR | `kubectl get nodes` thấy 2 node Ready; image có digest trong ECR |
| 2 | Kiểm tra số liệu: bucket Prometheus đủ, đúng nhãn, đúng service | Triển khai service, PostgreSQL commerce, Collector, Prometheus, Jaeger | Ba service ready, có traffic, Prometheus có số liệu |
| 3 | Kiểm tra một incident đi trọn đường, bằng chứng đúng revision | API, health worker, incident worker trên EKS; đăng nhập UI (mục 5) | Một lỗi Payment thật tạo IER đọc được trên UI |
| 4 | Kiểm tra bằng chứng rollback và audit | Rollback thật trên EKS; operator duyệt known-good trước | `recovered` và `telemetry_verified`; một lượt failed-verify chuyển `escalated` |
| 5 | Viết kịch bản demo và con số kỳ vọng | Tập dượt 3 lượt, khóa phiên bản | 3 lượt liên tiếp không sửa tay |

Hủy hạ tầng ngay sau buổi demo theo [AWS_BASELINE § Teardown](../runbooks/AWS_BASELINE.md#teardown).

## 5. Quyết định còn mở

| Quyết định | Phương án | Ai quyết, trước khi nào |
| --- | --- | --- |
| Đăng nhập UI | API production bắt buộc OIDC. (a) Bật Cognito theo IaC optional có sẵn của B; (b) chạy API ở máy vận hành nối vào cluster | B đề xuất, owner chốt, trước ngày 3 |
| Manifest worker | Chưa có; dùng lại image `control-plane` | B, ngày 3 |
| Bộ dựng phần dịch vụ | `kubernetes_testbed.py` (đã có Prometheus trong cluster) hay `render_business_manifests.py` cộng Prometheus | B, ngày 2 |
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
| Parity trên EKS không đạt ngay | Ngày 2–3 có dư; demo không phụ thuộc parity đạt ngưỡng |
| Rollback trên EKS chưa từng chạy | Dành trọn ngày 4 |
| Trễ quá ngày 5 | Lùi theo bậc: trọn vòng trên EKS → phát hiện tới giải thích trên EKS, rollback trên cluster local → trọn vòng trên local |
