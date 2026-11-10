# SentinelOps

Evidence-driven AIOps cho ba microservice: **Checkout, Payment, Inventory**.
Hệ thống thu metrics/logs/traces, tính SLO và model signals, tạo incident có bằng
chứng, rồi hỗ trợ giải thích và remediation có kiểm soát.

## Bắt đầu ở đây

| Bạn cần | Tài liệu |
| --- | --- |
| Mới tham gia / nhận việc A hoặc B | [Team handoff](docs/TEAM_HANDOFF.md) |
| Biết phần nào đã có, phần nào còn thiếu | [Trạng thái hiện tại](docs/PROJECT_STATUS.md) |
| Kế hoạch hai người và vùng sở hữu | [Delivery plan](docs/delivery/DELIVERY_PLAN.md), [workstream contract](docs/delivery/WORKSTREAM_CONTRACT.md) |
| Tìm đặc tả, lệnh chạy hoặc quyết định kiến trúc | [Mục lục tài liệu](docs/README.md) |

Local testbed/observability, telemetry/features, persistent incident API,
health/incident workers và UI read-only tại `/ui/` đã có. Model chính thức được
train trên **Alibaba v2022 theo ADR-016**; owner duyệt G3 **có điều kiện**, chỉ
cho tích hợp local H4→G6 (D-22). Đã có [báo cáo tích hợp v2 trên máy B](docs/reports/LANE_B_ALIBABA_V2_ACTIVATION.md)
và [các trial G6 local](docs/reports/G6_LOCAL_ALIBABA_BADF9DA0.md); **G6 chưa
nghiệm thu**, kết quả chính thức còn chờ lớp đánh giá local v2 và G8. AWS và
Bedrock chưa triển khai. Runtime hiện tại phải kiểm tra trên môi trường tương ứng.

## Chạy API local

Python 3.11 là baseline CI; Python 3.12 được project cho phép.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,ml]'
python scripts/validate_contracts.py
uvicorn sentinelops.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Liveness: `http://127.0.0.1:8000/health`; OpenAPI: `http://127.0.0.1:8000/docs`.
Lệnh này chưa dựng control DB/worker/model. Incident API cần DB và bearer identity;
inference trả 503 khi thiếu model hợp lệ. Với release Alibaba, `/v1/detect` và `/v1/risk`
trả 409 `model_requires_stream_history`; kết quả model nằm ở `service_signals` của
`GET /v1/incidents/{id}?revision=…`. Token tĩnh chỉ dùng cho local/test; môi trường khác
dùng OIDC. Theo [API runbook](docs/runbooks/INCIDENT_API.md)
và [local testbed](docs/runbooks/LOCAL_TESTBED.md) để chạy đầy đủ trên tài nguyên riêng.

Lane B: `python -m scripts.lane_b up`, rồi chạy `python -m scripts.lane_b serve`
ở terminal riêng và mở **http://127.0.0.1:18000/ui/**. Token local nằm trong
`artifacts/lane-b/operator-token` (0600, chỉ đọc); `api-token` dành cho producer
nhập/sửa IER. Không gửi token qua URL hoặc commit vào Git.
Theo [runtime B](docs/runbooks/LOCAL_TESTBED.md#lane-b-isolated-application-runtime)
để chạy các workers/source riêng. Không cần model để xem incident; UI chỉ đọc.

## Ranh giới sản phẩm

- MAD là control, Isolation Forest phát hiện anomaly, XGBoost dự báo pre-SLO gradual.
- IER và deterministic RCA giữ evidence/ranking/uncertainty; Bedrock chỉ giải thích.
- Action chỉ dùng runbook được phép, có policy, audit và telemetry verification.
- Nguồn train chính thức là trace production Alibaba v2022 theo
  [ADR-016](docs/adr/ADR-016-public-production-training-source.md); một bundle
  dùng chung cho ba service, với feature tương đối và mean latency qua source
  profile v2. Xem [model pipeline](docs/runbooks/MODEL_PIPELINE.md).
- Dữ liệu local HTTP chỉ dùng evaluation, không dùng fit, chọn model hay chỉnh
  ngưỡng; đường train local đã gỡ theo D-23. ADR-014 đã được thay thế về nguồn
  train; RCAEval chỉ dành cho RCA evaluation, GAMMA/v1 giữ làm lịch sử.
- Alibaba không có trường lỗi 5xx; SLO rule và MAD đảm nhận phần này. Build release
  không tự bật model; parity phải được đo trên môi trường đích và activation phải
  nằm trong phạm vi owner duyệt.

Schemas trong `contracts/` là wire truth. Thiết kế ở
[technical spec](docs/architecture/TECHNICAL_SPEC.md); breaking architecture changes
được ghi bằng ADR trước implementation. Thay đổi nhỏ cập nhật tài liệu hiện có.

## Cấu trúc repository

```text
src/sentinelops/   API, telemetry, models, incidents và workers
services/          Checkout / Payment / Inventory
scripts/           Capture, train, replay, kiểm tra và vận hành
contracts/         JSON Schemas
configs/           Policy, source/split và experiment configuration
infra/             Database và deployment infrastructure
observability/     Collector và Prometheus
tests/            Regression và contract tests
docs/             Đặc tả, delivery, runbooks, ADR và lịch sử có phân loại
```

Raw data, model binaries, secrets và output chạy thử không commit vào Git.
Cách chia hai người và mức kiểm tra theo tác động nằm trong delivery plan.
