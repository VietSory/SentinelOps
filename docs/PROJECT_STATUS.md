# SentinelOps — trạng thái và giới hạn

Bản tổng hợp này phân biệt capability trong repo, bằng chứng từ từng lần chạy
và trạng thái của môi trường. Để chạy thử hoặc dựng stack, bắt đầu ở
[README](../README.md).

## Môi trường

- **Local:** có demo UI dữ liệu mẫu và testbed Docker Compose. Testbed có ba
  workload, commerce DB, control DB và observability. API và worker có hướng
  dẫn chạy riêng. [Chạy testbed](runbooks/LOCAL_TESTBED.md)
- **AWS dev:** đã triển khai và chạy thử tháng 11/2026; hai stack dev và trial
  đã teardown ngày 22/11/2026. [Kịch bản đã đo](delivery/DEMO_SCENARIOS_2026-11-19.md) ·
  [Mốc Terraform](delivery/AWS_TERRAFORM_STATE_REDACTED.md) · [Dựng mới](../scripts/aws_demo/REBUILD.md)

## Capability và bằng chứng

| Phần | Đã có | Nguồn đối chiếu |
| --- | --- | --- |
| Telemetry | Metrics, logs và traces từ testbed; bucket 60 giây, feature window 300 giây và nguồn bằng chứng truy ngược. | [Telemetry](runbooks/TELEMETRY.md) · [quy ước thời gian](architecture/TRACE_TIMING.md) |
| Incident và RCA | Health/incident worker, IER với revision và API được bảo vệ. Correlation và RCA hỗ trợ abstain khi bằng chứng yếu. G4 đầy đủ còn mở. | [Incident worker](runbooks/INCIDENT_WORKER.md) · [baseline RCA](reports/RCA_DEVELOPMENT.md) |
| Model | Isolation Forest và XGBoost huấn luyện trên Alibaba v2022. Release `de38fc6b` có G3 có điều kiện và kết quả development S1/S3; các yêu cầu S2/S4, MTTD live và holdout vẫn theo cổng nghiệm thu. | [Model pipeline](runbooks/MODEL_PIPELINE.md) · [G3](reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md) · [đánh giá local](reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md) |
| Operator UI và giải thích | Workspace theo revision, lịch sử, evidence và action. Explanation có fallback deterministic và adapter LLM tùy cấu hình. | [Incident API](runbooks/INCIDENT_API.md) · [Explanation](runbooks/EXPLANATIONS.md) |
| Remediation | Runbook giới hạn phạm vi, phê duyệt của operator, journal, kiểm chứng phục hồi và xử lý quarantine. Các tuyên bố về rollback cần receipt từ đúng lượt và môi trường. | [Controlled remediation](runbooks/CONTROLLED_REMEDIATION.md) · [trường hợp AWS](delivery/DEMO_SCENARIOS_2026-11-19.md) |

## Việc tiếp theo

- Với thử nghiệm local, chọn kịch bản và lưu giá trị trước/sau, revision cùng
  nguồn xác minh theo [runbook demo](runbooks/DEMO_LOCAL.md).
- Với AWS, dựng môi trường mới rồi kiểm tra tuần tự hạ tầng, runtime, telemetry
  và luồng incident/remediation. [Checklist dựng lại](../scripts/aws_demo/REBUILD.md)
- Với nghiên cứu và nghiệm thu, dùng [Gates G0–G8](architecture/CONSTRAINTS_AND_GATES.md)
  và [báo cáo theo mốc](reports/README.md). Kết quả test fixture, development,
  trial live và holdout được đối chiếu theo tiêu chí riêng.

[Lịch sử trang trạng thái trước khi thu gọn](https://github.com/VietSory/SentinelOps/blob/88d51f8af8debb86f935e61396bc7d9ec777e781/docs/PROJECT_STATUS.md)
