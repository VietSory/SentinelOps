# Tài liệu SentinelOps

## Chọn theo việc bạn muốn làm

| Mục tiêu | Điểm bắt đầu |
| --- | --- |
| Mở UI demo hoặc chạy service trên máy | [Quickstart](../README.md#bắt-đầu) |
| Hiểu hệ thống bằng ví dụ | [Hướng dẫn tổng quan, model và incident](guide/README.md) |
| Xem phần đã có và giới hạn đã ghi nhận | [Trạng thái dự án](PROJECT_STATUS.md) |
| Dựng một AWS dev mới | [Hướng dẫn dựng lại](../scripts/aws_demo/REBUILD.md) |
| Nhận bàn giao công việc trong đội | [Team handoff](TEAM_HANDOFF.md), [ownership và tích hợp](delivery/WORKSTREAM_CONTRACT.md) |
| Chuẩn bị buổi demo: cảnh báo sớm, duyệt, email | [Kế hoạch nâng cấp demo](delivery/DEMO_UPGRADE_PLAN.md) |

## Thiết kế và yêu cầu hiện hành

| Tài liệu | Mục đích |
| --- | --- |
| [Product spec](architecture/PRODUCT_SPEC.md) | Mục tiêu, phạm vi và trách nhiệm thành phần |
| [Technical spec](architecture/TECHNICAL_SPEC.md) | Data/model/API/storage/deployment interfaces và failure behavior |
| [Constraints and gates](architecture/CONSTRAINTS_AND_GATES.md) | Điều kiện nghiệm thu và quyết định còn mở |
| [Telemetry conventions](architecture/TELEMETRY_CONVENTIONS.md) | Tên trường, labels và đơn vị |
| [Trace timing](architecture/TRACE_TIMING.md) | Source/receipt clocks và giới hạn diễn giải |
| [ADR index](adr/README.md) | Quyết định kiến trúc, status và quan hệ supersession |
| [Production gaps](delivery/PRODUCTION_GAPS.md) | Khoảng cách tới vận hành thật, ngoài phạm vi demo |

## Runbooks — mở khi cần thực hiện

| Công việc | Runbook |
| --- | --- |
| Dựng workload/quan sát request | [Local testbed](runbooks/LOCAL_TESTBED.md), [observability](runbooks/OBSERVABILITY.md) |
| Capture và dựng features | [Telemetry](runbooks/TELEMETRY.md), [experiments](runbooks/EXPERIMENTS.md) |
| Tải nguồn và train model hiện tại | [Model pipeline Alibaba](runbooks/MODEL_PIPELINE.md) |
| Capture HTTP để đánh giá trên testbed | [Prospective HTTP corpus](runbooks/LOCAL_HTTP_CORPUS.md) |
| Incident API/control DB | [API](runbooks/INCIDENT_API.md), [storage](runbooks/INCIDENT_STORE.md) |
| Health/incident processes | [Health worker](runbooks/HEALTH_WORKER.md), [incident worker](runbooks/INCIDENT_WORKER.md) |
| Hiểu quyết định incident | [SLO health](runbooks/SLO_HEALTH.md), [correlation](runbooks/CORRELATION.md), [RCA](runbooks/RCA.md) |
| RCAEval development/evaluation | [Native adapter](runbooks/RCAEVAL_NATIVE.md), [development](runbooks/RCAEVAL_NATIVE_DEVELOPMENT.md), [partition/access](runbooks/RCAEVAL_PARTITIONS.md) |

## Lịch sử và bằng chứng

- [Research results](reports/README.md): chỉ baseline/evaluation cần dùng lại, không phải hồ sơ service đã PASS.
- [Kịch bản demo AWS lịch sử](delivery/DEMO_SCENARIOS_2026-11-19.md) và
  [kiểm kê trước/sau teardown](delivery/AWS_TERRAFORM_STATE_REDACTED.md).
- [Archive](archive/README.md): spec/checkpoint/guide đã thay thế, gồm nghiên cứu GAMMA/SMD.
- Input dùng cho regression nằm ở `tests/fixtures/`. JSON trong reports chỉ đi kèm
  kết quả nghiên cứu còn cần dùng; raw data/model binaries nằm ngoài Git.

## Quy tắc giữ tài liệu gọn

- Mỗi chủ đề có một bản hiện hành. Sửa bản đó thay vì tạo thêm plan/status gần giống.
- Thay đổi capability mới cập nhật PROJECT_STATUS. Smoke PASS, sửa nhỏ và việc đã
  có test/CI bảo vệ không cần report riêng hoặc bản lưu trong archive.
- ADR dành cho quyết định kiến trúc/breaking boundary; bug fix và tiến độ không cần ADR.
- Runbook chứa lệnh/điều kiện chạy; report giữ kết quả đo; không trộn hai mục đích.
- Chỉ đưa bản cũ vào archive khi có lý do tra cứu lâu dài; phiên bản thông thường
  dùng Git history. Hai trang chuyển tiếp
  ở `DELIVERY_PLAN.md`/`WORKSTREAM_CONTRACT.md` chỉ giữ URL đã chia sẻ; không thêm bản sao nội dung.
