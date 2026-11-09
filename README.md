# SentinelOps

Evidence-driven AIOps cho ba microservice: **Checkout, Payment, Inventory**.
Hệ thống thu metrics/logs/traces, tính SLO và model signals, tạo incident có bằng
chứng, rồi hỗ trợ giải thích và remediation có kiểm soát.

Model phát hiện bất thường (MAD + Isolation Forest + XGBoost) hiện train trên
trace production thật của Alibaba theo
[ADR-016](docs/adr/ADR-016-public-production-training-source.md) — xem
[Ranh giới sản phẩm](#ranh-giới-sản-phẩm). Mọi gate G0–G8
([CONSTRAINTS_AND_GATES](docs/architecture/CONSTRAINTS_AND_GATES.md)) tách rõ
bốn mức bằng chứng — runtime-verified, fixture-tested, code-only, blocked —
không coi report hay CI cũ là bằng chứng hiện hành.

## Bắt đầu ở đây

| Bạn cần | Tài liệu |
| --- | --- |
| Mới tham gia / nhận việc A hoặc B | [Team handoff](docs/TEAM_HANDOFF.md) |
| Biết phần nào đã có, phần nào còn thiếu | [Trạng thái hiện tại](docs/PROJECT_STATUS.md) |
| Kế hoạch hai người và vùng sở hữu | [Delivery plan](docs/delivery/DELIVERY_PLAN.md), [workstream contract](docs/delivery/WORKSTREAM_CONTRACT.md) |
| Tìm đặc tả, lệnh chạy hoặc quyết định kiến trúc | [Mục lục tài liệu](docs/README.md) |

Local testbed/observability, telemetry/features, persistent incident API và
health/incident workers đã có. UI read-only đã có tại `/ui/`, đọc IER/evidence/
history thật và hiển thị model unavailable. Model Alibaba (ADR-016) đã build
release và chạy inference thật trên stack local lane B (health/incidents/api
roles qua `lane_b_host`, profile `prometheus-increase-60s-5s/v2`), nhưng chỉ
trong phạm vi owner duyệt có điều kiện (D-22: tích hợp local H4→G6) — **G3 đầy
đủ và G8 chưa nghiệm thu**. Explanation/provider cloud, action execution sản
phẩm và AWS còn cần hoàn thiện. Inventory/runtime mới nhất phải kiểm tra trên
môi trường tương ứng, không suy từ report cũ.

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
`artifacts/lane-b/operator-token` (0600, chỉ đọc); `api-token` dành cho producer
nhập/sửa IER. Không gửi token qua URL hoặc commit vào Git.
Theo [runtime B](docs/runbooks/LOCAL_TESTBED.md#lane-b-isolated-application-runtime)
để chạy các workers/source riêng. Không cần model để xem incident; UI chỉ đọc.

## Model pipeline (Alibaba, ADR-016)

```sh
python -m scripts.train_alibaba --help
python -m scripts.alibaba_controls --help
python -m scripts.check_alibaba_source_parity --help   # target-environment source parity, bắt buộc trước build
python -m scripts.build_alibaba_release --help         # operator-only, cần parity PASS + owner G3 decision
python -m scripts.lane_b_host start --role health --source-profile prometheus-increase-60s-5s/v2 --help
python -m scripts.check_lane_b_live --help             # verify end-to-end sau khi chọn release
```

Chi tiết từng bước và điều kiện gate ở [model pipeline runbook](docs/runbooks/MODEL_PIPELINE.md#source-parity-and-serving-release)
và [lane B checkpoint §7.6](docs/delivery/LANE_B_CHECKPOINT.md#76-cập-nhật-2026-11-09-model-chính-thức-là-model-alibaba-adr-016).
`build_alibaba_release` không activate gì; B tự chọn release qua
`SENTINELOPS_MODEL_STORE`/`SENTINELOPS_MODEL_RELEASE_REF` sau khi owner duyệt G3.

## Ranh giới sản phẩm

- MAD là control theo luật SLO, Isolation Forest phát hiện anomaly, XGBoost dự
  báo pre-SLO gradual; từ ADR-016, ba model này là **một bundle service-agnostic
  dùng chung** cho Checkout/Payment/Inventory (`http-relative-5m/v1`), không còn
  bundle riêng từng service.
- IER và deterministic RCA giữ evidence/ranking/uncertainty; Bedrock chỉ giải thích.
- Action chỉ dùng runbook được phép, có policy, audit và telemetry verification.
- Nguồn train chính thức là trace production Alibaba theo
  [ADR-016](docs/adr/ADR-016-public-production-training-source.md), supersedes
  [ADR-014](docs/adr/ADR-014-proposed-local-training-evidence.md) (prospective
  local HTTP). Corpus local và RCAEval chỉ còn vai trò evaluation/development,
  không fitting. Source authorization không phải data/model acceptance.

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
