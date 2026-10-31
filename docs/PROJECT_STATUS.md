# SentinelOps — trạng thái triển khai

Cập nhật: **2026-11-01**. Trang này tóm tắt code và khoảng trống sản phẩm.
Service có đang chạy/ready hay không phải kiểm tra trên môi trường hiện tại;
report hoặc CI cũ không thay kết quả đó.

| Phần | Implementation / việc còn thiếu | Cách làm việc hoặc kiểm tra |
| --- | --- | --- |
| Workload / observability | Có ba services, commerce PostgreSQL, OTel, Prometheus/Jaeger local | [Testbed](runbooks/LOCAL_TESTBED.md), [observability](runbooks/OBSERVABILITY.md) |
| Telemetry / features | Có raw capture, quality/provenance, 60s buckets / 300s windows và replay | [Telemetry](runbooks/TELEMETRY.md), [timing](architecture/TRACE_TIMING.md) |
| Data / models | Có prospective HTTP admission, campaign, fitting/bundles/registry guards; **G2/G3 chưa nghiệm thu** | Audit corpus hiện tại theo [corpus runbook](runbooks/LOCAL_HTTP_CORPUS.md); [model lifecycle](runbooks/MODEL_PIPELINE.md) |
| Incident pipeline | Có health/incident workers, control DB, IER revisions và protected read API | [Health](runbooks/HEALTH_WORKER.md), [incident](runbooks/INCIDENT_WORKER.md), [API](runbooks/INCIDENT_API.md) |
| Correlation / RCA | Có conservative grouping/abstention; **full G4 chưa nghiệm thu** | [RCA runbook](runbooks/RCA.md), [research baseline](reports/RCA_DEVELOPMENT.md) |
| Operator UI / B1 | Có UI read-only cùng origin `/ui/`: token trong tab, list/filter/cursor, revision-pinned detail/history/evidence, UNKNOWN/503 và model-unavailable. Đã chạy trên incident SLO thật của B, 4 immutable revisions, API/DB restart và browser outage/reconnect | [API/UI](runbooks/INCIDENT_API.md), [runtime B](runbooks/LOCAL_TESTBED.md); không coi ranking là root đã xác nhận |
| B2 integration / CI | Có explicit paired H1 wiring qua registry sẵn có; mặc định không model, đổi release của consumer hiện hữu cần migration A. Có immutable local image/config locks đã kiểm tra locked-up giữ đúng 8 image IDs và history. CI phân scope, browser/JS checks và always-run summary; host-process release packaging và production auth còn cần hoàn thiện | [Plan B](delivery/DELIVERY_PLAN.md#lane-b-execution), [runtime/lock scope](runbooks/LOCAL_TESTBED.md), [workstream contract](delivery/WORKSTREAM_CONTRACT.md) |
| B3 recovery component | Có read-only verifier: raw-verified ledger, ba bucket Payment hợp lệ hoàn toàn sau effective clock, receipt cutoff/deadline và reset UNKNOWN/thiếu traffic. Đã kiểm fixture và replay trên health ledger thật của B sau fault reset; chưa chứng minh Kubernetes rollback, chưa ghi action/IER recovery | [Controlled remediation](runbooks/CONTROLLED_REMEDIATION.md); executor, known-good approval, durable action journal và H4 integration còn thiếu |
| Explanation / action / AWS | H2 backend chưa có; chưa triển khai executor/action và AWS. G5–G8 vẫn mở | [Plan hai người](delivery/DELIVERY_PLAN.md), [gates](architecture/CONSTRAINTS_AND_GATES.md) |

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
