# SentinelOps

SentinelOps là dự án AIOps cho ba microservice **Checkout, Payment và Inventory**. Hệ thống biến metrics, logs và traces thành hồ sơ sự cố có thể truy ngược bằng chứng. Người vận hành dùng workspace để xem dịch vụ bị ảnh hưởng, đọc phân tích nguyên nhân và duyệt đề xuất phục hồi khi đủ điều kiện.

## Bắt đầu

| Bạn muốn | Bắt đầu ở đâu |
| --- | --- |
| Xem giao diện và thử luồng duyệt incident | [Demo trên trình duyệt với dữ liệu mẫu](#1-xem-demo-trên-trình-duyệt) |
| Quan sát dữ liệu thật từ các service chạy trên máy | [Testbed local](#2-chạy-testbed-local) |
| Deploy lại demo vào tài khoản AWS của bạn | [Quy trình dựng AWS dev](scripts/aws_demo/REBUILD.md) |
| Đọc thiết kế, model hoặc kết quả đã đo | [Mục lục tài liệu](docs/README.md) |

### 1. Xem demo trên trình duyệt

Cần **Node.js 20+** và npm. Từ thư mục gốc repo vừa clone, mở một terminal:

```bash
cd web
npm ci
npm run dev
```

Mở [demo tiếng Việt](http://127.0.0.1:5173/ui/demo/?lang=vi) hoặc [demo tiếng Anh](http://127.0.0.1:5173/ui/demo/?lang=en). Chọn một incident trong danh sách, xem timeline và evidence, rồi thử thao tác trên đề xuất nếu incident có. Thao tác ở chế độ này được mô phỏng trong trình duyệt. Đường dẫn `/` mở chế độ kết nối backend. [Hướng dẫn web](web/README.md)

### 2. Chạy testbed local

Cần **Python 3.11 hoặc 3.12**, Docker và Docker Compose v2. Nếu dùng WSL,
clone repo từ WSL vào thư mục Linux như `~/SentinelOps`. Testbed dùng quyền
file Linux cho token và cấu hình runtime. Chạy các lệnh sau từ thư mục gốc repo:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m scripts.lane_b config
python -m scripts.lane_b up
python -m scripts.lane_b status
```

Chạy `python -m scripts.lane_b serve` ở một terminal đã kích hoạt venv. Mở [Operator UI local](http://127.0.0.1:18000/ui/) và dùng token operator được tạo ở `artifacts/lane-b/operator-token`. Grafana mở tại [127.0.0.1:13000](http://127.0.0.1:13000/). Để sinh incident thật, làm theo [demo local](docs/runbooks/DEMO_LOCAL.md) để chạy traffic, health worker và incident worker theo stream thực tế. Fault Console là [công cụ riêng cho môi trường demo](scripts/demo_console/README.md).

Khi kết thúc, dùng Ctrl+C cho các tiến trình chạy ở terminal, rồi chạy `python -m scripts.lane_b down` để dừng Compose và giữ volume. [Cổng, auth và cách kiểm tra testbed](docs/runbooks/LOCAL_TESTBED.md)

## Từ một request đến một incident

Checkout gọi Payment và Inventory. Collector chuyển telemetry tới Prometheus và Jaeger. Health worker đánh giá độ trễ, tỷ lệ lỗi và độ tin cậy của dữ liệu theo từng khoảng thời gian. Incident worker liên kết dấu hiệu liên quan và ghi **Incident Evidence Record (IER)** vào PostgreSQL. Mỗi lần cập nhật tạo một revision để operator có thể đối chiếu lại.

RCA xếp hạng giả thuyết dựa trên bằng chứng và có thể trả `UNKNOWN` hoặc abstain. Khi một ca đủ điều kiện của runbook, ví dụ rollback Payment về image known-good đã duyệt, operator xem bằng chứng và phê duyệt trước khi controller thực thi. Kết quả rollout và telemetry sau đó được dùng để kiểm chứng phục hồi. [Luồng incident](docs/guide/03_INCIDENT_FLOW.md) · [Remediation có kiểm soát](docs/runbooks/CONTROLLED_REMEDIATION.md)

Isolation Forest và XGBoost bổ sung tín hiệu latency. Pipeline huấn luyện dùng trace Alibaba v2022; testbed cung cấp nguồn telemetry để đánh giá hành vi. Release `de38fc6b` có kết quả phát triển và G3 có điều kiện. [Nguồn dữ liệu và model](docs/guide/02_AI_TRAINING_DATA.md) · [Phạm vi đã kiểm chứng](docs/PROJECT_STATUS.md)

## Kiến trúc AWS

![Kiến trúc AWS của SentinelOps: CloudFront và Cognito ở lớp truy cập, các workload chạy trong EKS, RDS và S3 lưu dữ liệu vận hành](docs/assets/sentinelops-aws-architecture.png)

*Thiết kế tham chiếu cho môi trường AWS dev. Các stack demo tháng 11/2026 đã được teardown.*

| Lớp | Thành phần và vai trò |
| --- | --- |
| **Truy cập** | Trình duyệt đi qua **CloudFront → VPC origin → ALB nội bộ → EKS** để vào Operator UI, API và Grafana. Cognito đăng nhập operator. |
| **Workload** | EKS chạy Checkout, Payment, Inventory và bộ tạo traffic. Health worker đánh giá tín hiệu theo từng khoảng thời gian; incident worker liên kết các dấu hiệu thành hồ sơ sự cố. |
| **Quan sát** | OpenTelemetry Collector nhận telemetry. Prometheus cung cấp số liệu latency và lỗi để đánh giá SLO, Jaeger cho phép lần theo lời gọi giữa các service, Grafana hiển thị dashboard. |
| **Dữ liệu và triển khai** | RDS PostgreSQL của control plane lưu revision sự cố và nhật ký xử lý, tách với DB nghiệp vụ. S3 lưu artifact, ECR lưu image, còn Secrets Manager giữ cấu hình bí mật của runtime. |

Ví dụ, khi Payment chậm, Checkout có thể chậm theo. Prometheus cho thấy độ trễ tăng ở cả hai service, còn Jaeger giúp kiểm tra thời gian bị dồn vào lời gọi Payment hay nằm ở phần khác của Checkout. Operator xem bằng chứng đó trong revision của incident. Nếu ca có thay đổi image Payment và đủ điều kiện runbook, operator có thể duyệt rollback rồi đối chiếu rollout và telemetry mới để xác nhận phục hồi.

### Dựng lại AWS dev

Bắt đầu với [mẫu cấu hình và hướng dẫn dựng từ clone](scripts/aws_demo/REBUILD.md). Người triển khai điền thông tin tài khoản đích, lập Terraform plan, rồi build lại image và render runtime với endpoint, digest và thông tin xác thực của cụm mới. Checklist nghiệm thu bao gồm đăng nhập, telemetry mới và một lượt demo end-to-end.

Xem [kịch bản AWS đã đo](docs/delivery/DEMO_SCENARIOS_2026-11-19.md) và [bằng chứng local](docs/reports/README.md) để đối chiếu những gì từng được kiểm chứng.

## Làm việc với repo

| Đường dẫn | Vai trò |
| --- | --- |
| [`services/`](services/) | Các ứng dụng phát sinh traffic và telemetry cho testbed. |
| [`src/sentinelops/`](src/sentinelops/) · [`web/`](web/) | Backend, worker, RCA, model, remediation và giao diện. |
| [`contracts/`](contracts/) · [`configs/`](configs/) | Schema, policy, cấu hình và giao thức đánh giá. |
| [`infra/`](infra/) · [`docker/`](docker/) · [`observability/`](observability/) | IaC, SQL/Kubernetes, image và cấu hình quan sát. |
| [`scripts/`](scripts/) · [`tests/`](tests/) · [`docs/`](docs/) | Lệnh vận hành, bộ kiểm thử và tài liệu dự án. |

Từ thư mục gốc, sau khi cài `.[dev]`. Nhóm test script triển khai cần Bash và
`jq`. Bộ test đầy đủ chạy trên Linux hoặc checkout nằm trong filesystem WSL:

```bash
python scripts/validate_contracts.py
python -m pytest -q
npm run test:ui
```

`npm run test:ui` ở gốc kiểm thử JavaScript của Operator UI đi kèm backend. `npm run build` trong `web/` build giao diện React. Xem [workflow CI](.github/workflows/ci.yml) và [LICENSE](LICENSE).
