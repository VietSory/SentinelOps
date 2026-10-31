# SentinelOps — trạng thái triển khai

Cập nhật: **2026-10-31**. Trang này tóm tắt code và khoảng trống sản phẩm.
Service có đang chạy/ready hay không phải kiểm tra trên môi trường hiện tại;
report hoặc CI cũ không thay kết quả đó.

| Phần | Implementation / việc còn thiếu | Cách làm việc hoặc kiểm tra |
| --- | --- | --- |
| Workload / observability | Có ba services, commerce PostgreSQL, OTel, Prometheus/Jaeger local | [Testbed](runbooks/LOCAL_TESTBED.md), [observability](runbooks/OBSERVABILITY.md) |
| Telemetry / features | Có raw capture, quality/provenance, 60s buckets / 300s windows và replay | [Telemetry](runbooks/TELEMETRY.md), [timing](architecture/TRACE_TIMING.md) |
| Data / models | Có prospective HTTP admission, campaign, fitting/bundles/registry guards; **G2/G3 chưa nghiệm thu** | Audit corpus hiện tại theo [corpus runbook](runbooks/LOCAL_HTTP_CORPUS.md); [model lifecycle](runbooks/MODEL_PIPELINE.md) |
| Incident pipeline | Có health/incident workers, control DB, IER revisions và protected read API | [Health](runbooks/HEALTH_WORKER.md), [incident](runbooks/INCIDENT_WORKER.md), [API](runbooks/INCIDENT_API.md) |
| Correlation / RCA | Có conservative grouping/abstention; **full G4 chưa nghiệm thu** | [RCA runbook](runbooks/RCA.md), [research baseline](reports/RCA_DEVELOPMENT.md) |
| UI / explanation / action / AWS | Còn cần hoàn thiện; G5–G8 mở | [Plan hai người](delivery/DELIVERY_PLAN.md), [gates](architecture/CONSTRAINTS_AND_GATES.md) |

[ADR-014](adr/ADR-014-proposed-local-training-evidence.md) và
[contract v2](../configs/training_data_contract_v2.json) quy định prospective local
HTTP fitting, pinned RCAEval evaluation-only; không promote v1/GAMMA/pilot cũ.
Source authorization không đồng nghĩa dataset/model được accepted.

[CI](https://github.com/VietSory/SentinelOps/actions/workflows/ci.yml) cần đối chiếu
đúng SHA. Liveness, DB/model readiness, corpus admission và quality gates là các
trạng thái khác nhau. Raw/model artifacts nằm trên môi trường tương ứng, ngoài Git.

## Việc tiếp theo

- **A:** admission/capture → models/evaluation → RCA/explanation → source parity.
- **B:** UI/API local → packaging/auth/monitoring → controlled rollback → AWS.

Theo [ownership/interfaces](delivery/WORKSTREAM_CONTRACT.md). Cập nhật trang này
khi capability/gap thay đổi; kiểm tra thông thường ghi ở commit/CI, không thêm
nhật ký PASS hoặc report riêng cho mỗi lane.
