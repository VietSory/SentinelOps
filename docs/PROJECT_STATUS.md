# SentinelOps — trạng thái hiện tại

Checkpoint tài liệu: **2026-10-31**, baseline `main` sau PR #24.
Trang này chỉ giữ trạng thái hiện hành và việc kế tiếp. Diễn biến cũ nằm trong
[checkpoint archive](archive/checkpoints/PROJECT_STATUS_2026-10-31.md) và
[reports](reports/README.md); chúng không xác nhận process còn sống hôm nay.

## Khả năng đang có và khoảng trống

| Phần | Trạng thái tại checkpoint đã công bố | Đọc khi làm phần đó |
| --- | --- | --- |
| Workload / observability | Ba services, commerce PostgreSQL, OTel, Prometheus/Jaeger local đã có | [Testbed](runbooks/LOCAL_TESTBED.md), [observability](runbooks/OBSERVABILITY.md) |
| Telemetry / features | Raw capture, quality/provenance, UTC 60s buckets / 300s windows và replay đã có | [Telemetry](runbooks/TELEMETRY.md), [timing](architecture/TRACE_TIMING.md) |
| Data / model software | Prospective HTTP v2 registration/admission, campaign, fitting/bundles/registry guards đã có; **G2/G3 chưa accepted, zero approved models** | [Corpus](runbooks/LOCAL_HTTP_CORPUS.md), [models](runbooks/MODEL_PIPELINE.md), [admission evidence](reports/2026-10-31-prospective-http-admission.md) |
| Incident pipeline | Continuous health/incident workers, control DB, immutable IER revisions và protected read API đã chạy local | [Health worker](runbooks/HEALTH_WORKER.md), [incident worker](runbooks/INCIDENT_WORKER.md), [API](runbooks/INCIDENT_API.md) |
| Correlation / RCA | Conservative grouping, scope/evidence validation và abstention đã có; **full G4/diagnostic quality chưa accepted** | [RCA](runbooks/RCA.md), [development evidence](reports/PHASE3_RCA_AI_INTEGRATION.md) |
| UI / explanation / remediation / AWS | Chưa hoàn thành/nghiệm thu; G5–G8 còn mở, cloud permissions/budget cần xác nhận | [Delivery plan](delivery/DELIVERY_PLAN.md), [gates](architecture/CONSTRAINTS_AND_GATES.md) |

## Nguồn và kiểm chứng

- [ADR-014](adr/ADR-014-proposed-local-training-evidence.md) / [contract v2](../configs/training_data_contract_v2.json):
  prospective local HTTP fitting; pinned RCAEval evaluation-only. V1/GAMMA và pilot
  cũ không promote. Không thay source/label/threshold để làm gate pass.
- Fix `98cdd67` của PR #24 đã merge qua `f4891cf`; 743 regression tests, contracts
  và installed wheel pass ở bản fix. Plan hai người ở `439220e` có
  [exact CI 41200790141 pass 8/8](https://github.com/VietSory/SentinelOps/actions/runs/41200790141).
  Kết quả này không tự chứng minh dataset/model hoặc deployment mới.
- [CI hiện tại](https://github.com/VietSory/SentinelOps/actions/workflows/ci.yml)
  phải đối chiếu đúng SHA. Corpus root/process inventory, accepted counts và model
  availability phải kiểm tra trên máy đang giữ dữ liệu; Git không chứa raw/models.

## Việc tiếp theo của hai người

- **A:** admission/capture → models/evaluation → RCA/explanation → source parity.
- **B:** UI/API local → packaging/auth/monitoring → controlled rollback → AWS.

Theo [plan](delivery/DELIVERY_PLAN.md) và [ownership/interfaces](delivery/WORKSTREAM_CONTRACT.md).
Mỗi lane cập nhật report riêng khi triển khai; chỉ cập nhật trang này khi trạng
thái sản phẩm thay đổi, không nối nhật ký từng test hoặc CI vào cuối trang.
