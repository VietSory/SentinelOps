# Tài liệu SentinelOps

## Lộ trình đọc

1. [Trạng thái hiện tại](PROJECT_STATUS.md): hệ thống đã có gì, còn thiếu gì.
2. [Team handoff](TEAM_HANDOFF.md): context và lệnh bắt đầu cho người mới.
3. [Delivery plan](delivery/DELIVERY_PLAN.md) và [workstream contract](delivery/WORKSTREAM_CONTRACT.md): nhận lane A/B, file ownership và integration.
4. Mở đặc tả/runbook của phần đang làm; không cần đọc toàn bộ reports hoặc archive.

## Thiết kế và yêu cầu hiện hành

| Tài liệu | Mục đích |
| --- | --- |
| [Product spec](architecture/PRODUCT_SPEC.md) | Mục tiêu, phạm vi và trách nhiệm thành phần |
| [Technical spec](architecture/TECHNICAL_SPEC.md) | Data/model/API/storage/deployment interfaces và failure behavior |
| [Constraints and gates](architecture/CONSTRAINTS_AND_GATES.md) | Điều kiện nghiệm thu và quyết định còn mở |
| [Telemetry conventions](architecture/TELEMETRY_CONVENTIONS.md) | Tên trường, labels và đơn vị |
| [Trace timing](architecture/TRACE_TIMING.md) | Source/receipt clocks và giới hạn diễn giải |
| [ADR index](adr/README.md) | Quyết định kiến trúc, status và quan hệ supersession |

## Runbooks — mở khi cần thực hiện

| Công việc | Runbook |
| --- | --- |
| Dựng workload/quan sát request | [Local testbed](runbooks/LOCAL_TESTBED.md), [observability](runbooks/OBSERVABILITY.md) |
| Capture và dựng features | [Telemetry](runbooks/TELEMETRY.md), [experiments](runbooks/EXPERIMENTS.md) |
| Thu data/train model hiện tại | [Prospective HTTP corpus](runbooks/LOCAL_HTTP_CORPUS.md), [model pipeline](runbooks/MODEL_PIPELINE.md) |
| Incident API/control DB | [API](runbooks/INCIDENT_API.md), [storage](runbooks/INCIDENT_STORE.md) |
| Health/incident processes | [Health worker](runbooks/HEALTH_WORKER.md), [incident worker](runbooks/INCIDENT_WORKER.md) |
| Hiểu quyết định incident | [SLO health](runbooks/SLO_HEALTH.md), [correlation](runbooks/CORRELATION.md), [RCA](runbooks/RCA.md) |
| RCAEval development/evaluation | [Native adapter](runbooks/RCAEVAL_NATIVE.md), [development](runbooks/RCAEVAL_NATIVE_DEVELOPMENT.md), [partition/access](runbooks/RCAEVAL_PARTITIONS.md) |

## Lịch sử và bằng chứng

- [Reports](reports/README.md): kết quả có ngày/SHA và scope; không phải current status.
- [Archive](archive/README.md): spec/checkpoint/guide đã thay thế, gồm nghiên cứu GAMMA/SMD.
- JSON trong `reports/evidence/` còn được tests/CLIs/CI tham chiếu; giữ paths/bytes
  trừ khi có migration của consumer. Raw data/model binaries nằm ngoài Git.

## Quy tắc giữ tài liệu gọn

- Mỗi chủ đề có một bản hiện hành. Sửa bản đó thay vì tạo thêm plan/status gần giống.
- Thay đổi capability mới cập nhật PROJECT_STATUS; diễn biến chạy nằm trong report
  của lane, không thêm một report cho từng vòng sửa/test.
- ADR dành cho quyết định kiến trúc/breaking boundary; bug fix và tiến độ không cần ADR.
- Runbook chứa lệnh/điều kiện chạy; report giữ kết quả đo; không trộn hai mục đích.
- Khi thay thế tài liệu, đưa bản cũ vào archive và sửa links. Hai trang chuyển tiếp
  ở `DELIVERY_PLAN.md`/`WORKSTREAM_CONTRACT.md` chỉ giữ URL đã chia sẻ; không thêm bản sao nội dung.
