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

Local testbed/observability, telemetry/features, persistent incident API và
health/incident workers đã có. Guarded model/admission software đã có; **G2/G3
chưa được chấp nhận và chưa có approved model tại checkpoint đã công bố**.
UI read-only đã có tại `/ui/`, đọc IER/evidence/history thật và hiển thị model
unavailable; explanation/provider, action execution và AWS còn cần hoàn thiện.
Inventory/runtime mới nhất phải kiểm tra trên môi trường tương ứng.

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
inference trả 503 khi thiếu model hợp lệ. Theo [API runbook](docs/runbooks/INCIDENT_API.md)
và [local testbed](docs/runbooks/LOCAL_TESTBED.md) để chạy đầy đủ trên tài nguyên riêng.

Lane B: `python -m scripts.lane_b up`, rồi chạy `python -m scripts.lane_b serve`
ở terminal riêng và mở **http://127.0.0.1:18000/ui/**. Token local nằm trong
`artifacts/lane-b/api-token` (0600); không gửi token qua URL hoặc commit vào Git.
Theo [runtime B](docs/runbooks/LOCAL_TESTBED.md#lane-b-isolated-application-runtime)
để chạy các workers/source riêng. Không cần model để xem incident; UI chỉ đọc.

## Ranh giới sản phẩm

- MAD là control, Isolation Forest phát hiện anomaly, XGBoost dự báo pre-SLO gradual.
- IER và deterministic RCA giữ evidence/ranking/uncertainty; Bedrock chỉ giải thích.
- Action chỉ dùng runbook được phép, có policy, audit và telemetry verification.
- Primary fitting dùng prospective local HTTP theo [ADR-014](docs/adr/ADR-014-proposed-local-training-evidence.md);
  RCAEval chỉ evaluation, GAMMA/v1 giữ lịch sử. Source authorization không phải data/model acceptance.

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
