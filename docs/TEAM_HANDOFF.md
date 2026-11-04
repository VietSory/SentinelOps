# SentinelOps — bắt đầu làm việc trong đội hai người

Baseline: `main` sau PR #24, merge `f4891cf`, 2026-10-31. Đây là context sản phẩm
cho checkout mới; không chứng minh máy mới đã có data/model/runtime.

## 1. Sản phẩm và trạng thái hiện tại

SentinelOps giám sát Checkout → Inventory/Payment → PostgreSQL. Services xuất
OpenTelemetry metrics/traces và JSON logs; Collector, Prometheus và Jaeger là
nguồn local. Pipeline tạo bucket 60 giây, feature 300 giây, quality/provenance;
health/incident workers tạo episode, RCA và IER có revision trong control DB.
API/UI đọc kết quả này; commerce DB và control DB có vai trò tách biệt.

Isolation Forest phát hiện bất thường, MAD là baseline; XGBoost dự báo suy giảm
từ từ trước SLO trong horizon 300 giây. Deterministic RCA xếp hạng suspect hoặc
abstain. Bedrock giải thích facts đã có. Action chỉ dùng runbook được phép và
verify bằng telemetry. Missing data là UNKNOWN, không phải healthy.

**Đã có:** workload/observability, telemetry/features/replay, experiment controller,
prospective admission, guarded fitting/loading, health/incident workers, persistent
API và conservative RCA; UI đọc IER thật và H2 explanation/fallback pin revision
đã có local. **Chưa được chấp nhận:** dataset/models G2/G3, RCA quality đầy đủ,
G5/provider cloud. **Chưa hoàn thành:** action activation sản phẩm và AWS.
Không làm lại những module đã có hoặc coi fixture model là model đã được chấp nhận.
Report cũ có thể ghi active capture; đó là quan sát tại thời điểm report, không
phải xác nhận controller vẫn sống hôm nay. Kiểm tra trên đúng máy trước khi tiếp tục.

## 2. Đọc vừa đủ rồi nhận lane

Đọc [DELIVERY_PLAN.md](delivery/DELIVERY_PLAN.md) và [WORKSTREAM_CONTRACT.md](delivery/WORKSTREAM_CONTRACT.md)
trước, sau đó đọc caller/code bị tác động. Không cần đọc toàn bộ reports cũ.

| Cần hiểu | Nguồn sự thật |
| --- | --- |
| Trạng thái / phần chưa đạt | [PROJECT_STATUS.md](PROJECT_STATUS.md), đối chiếu Git/CI/runtime của mình |
| Sản phẩm / thiết kế / acceptance | [PRODUCT_SPEC.md](architecture/PRODUCT_SPEC.md), [TECHNICAL_SPEC.md](architecture/TECHNICAL_SPEC.md), [gates](architecture/CONSTRAINTS_AND_GATES.md), [ADR index](adr/README.md) |
| A: data/model hiện tại | [ADR-014](adr/ADR-014-proposed-local-training-evidence.md), [v2 contract](../configs/training_data_contract_v2.json), [corpus](runbooks/LOCAL_HTTP_CORPUS.md), [models](runbooks/MODEL_PIPELINE.md) |
| A: worker/RCA | [health](runbooks/HEALTH_WORKER.md), [incident](runbooks/INCIDENT_WORKER.md), [RCA](runbooks/RCA.md), [RCAEval](runbooks/RCAEVAL_NATIVE_DEVELOPMENT.md) |
| B: API/DB/UI | [API runbook](runbooks/INCIDENT_API.md), [storage](runbooks/INCIDENT_STORE.md), `src/sentinelops/api.py`, `contracts/` |
| B: deploy/action | [ADR-006](adr/ADR-006-control-plane-and-storage.md), [ADR-009](adr/ADR-009-payment-rollback-runbook.md), technical spec §13–14, Compose/infra/observability |

## 3. Quyết định không cần research lại

1. Nguồn hiện tại là **prospective local HTTP v2**; pinned RCAEval chỉ RCA evaluation.
   GAMMA/v1 và pilot/worker captures cũ là lịch sử, không fitting mới, không ghép
   rows hoặc thêm nguồn thứ ba. Source authorization không phải admission PASS.
2. Giữ group trước windowing, chronology, train-only fitting, calibration riêng,
   final sealed. Không đổi label/SLO vì data/score không đạt. G2 cần 300/100 healthy
   train/calibration windows mỗi service và support/group/precursor theo contract.
3. Feature `http-red-5m/v1`, source local `prometheus-increase-60s-5s/v1`.
   Model thiếu thì inference 503; risk quá phút anchor unavailable, không backdate.
4. IER là authoritative record, schema strict; metadata bổ sung ở envelope versioned.
   Payment/Inventory độc lập nhưng cùng Checkout không được gộp/chọn một root tùy ý.
5. Bedrock không sửa ranking/facts, quyết định root hoặc gọi action. UI vẫn đọc
   incident/evidence được khi model/Bedrock chưa sẵn sàng.
6. Giữ baseline một môi trường Kubernetes/EKS, control PostgreSQL và S3; không thêm
   service mesh/multi-region/ML platform mới cho MVP. Đổi topology lớn là quyết định riêng.

## 4. Checkout mới và lệnh bắt đầu hiện có

Mỗi người clone vào thư mục riêng. Dùng tài khoản GitHub được owner cấp cho mình,
commit đúng danh tính mình; không dùng bot hoặc mượn danh tính người khác.

```sh
git clone https://github.com/VietSory/SentinelOps.git SentinelOps-work
cd SentinelOps-work
git switch main
git pull --ff-only
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,ml]'
python scripts/validate_contracts.py
```

**A bắt đầu:**

```sh
python -m pytest -q tests/test_local_corpus.py tests/test_local_campaign.py tests/test_local_data.py tests/test_local_labels.py tests/test_ml_pipeline.py
python scripts/capture_local_campaign.py --help
python scripts/audit_local_corpus.py --help
python scripts/train_models.py --help
```

Trước campaign: kiểm tra máy capture, corpus root tuyệt đối, controller/slot đang
sống và source đã freeze. Không tạo corpus mới vì clone thiếu `artifacts/`: raw/
models không nằm trong Git. Nếu có capture sống, lấy root/process/hash từ người
giữ máy, không restart mù quáng. Theo [runbook](runbooks/LOCAL_HTTP_CORPUS.md); audit dùng
output mới vì CLI không overwrite. A có capture checkout riêng sạch/bất biến,
implementation checkout tiếp tục coding được trong lúc controller chạy.

**B bắt đầu:**

```sh
python -m pytest -q tests/test_api.py tests/test_incident_api_contracts.py
uvicorn sentinelops.main:app --app-dir src --host 127.0.0.1 --port 18000
```

Uvicorn chỉ bật API, không tự dựng DB/workers/models. Theo API runbook: cấp
`CONTROL_DATABASE_URL` bằng runtime role, `SENTINELOPS_OPERATOR_TOKEN` cho UI đọc
và `SENTINELOPS_API_TOKEN` cho producer nhập/sửa IER. Hai token khác nhau, mỗi token
ít nhất 32 ký tự; `.env` không tự load. Launcher B tạo hai file private riêng và
không đổi token cũ. Dựng DB/testbed của B sau
khi cô lập ports theo contract. Không reuse volume/secret A. Production auth chưa
có; public API local bằng static token không phải AWS acceptance.

B có [host releases/user services](runbooks/HOST_PROCESSES.md): wheel/config/CLI
và dependency được copy, lifecycle do systemd quản lý; source/root/checkpoint giữ
nguyên. Mặc định không model, credentials private ngoài bundle. Release gắn với
host Python/OS, không phải portable image hoặc quyền action. Lệnh foreground cũ
vẫn dùng được; không chạy cùng role foreground và managed song song.

## 5. API đủ để B làm UI ngay

| Interface đã có | Consumer xử lý |
| --- | --- |
| `GET /health` | Liveness, không chứng minh model/store ready |
| `GET /ready`, `/ready/incidents` | Model/store readiness tách riêng; incident UI vẫn dùng khi model 503 |
| `GET /v1/incidents` | Bearer auth; state/service/limit/cursor; limit 1–100; chưa có time-range filter |
| `GET /v1/incidents/{id}` | Exact IER, current hoặc `revision`, ETag/revision headers |
| `GET /v1/incidents/{id}/context` | IncidentEnvelope và timeline phân trang; không nhét field mới vào IER |
| `GET /v1/incidents/{id}/explanation?revision=N` | Wrapper riêng version 1.0, GroundedExplanation trong payload, provider/fallback, refs thuộc IER đang chọn; GET không ghi EXPLAINED hoặc cấp action |
| `GET /v1/incidents/{id}/actions`, `/v1/actions/{id}` | Optional SELECT-only action journal, pin incident revision và action ETag; xem [action runbook](runbooks/CONTROLLED_REMEDIATION.md#read-only-action-http-and-ui) |
| `GET /v1/actions/{id}/quarantine` | Optional migration 2, receipt operator riêng; không biến `escalated` thành recovered, không có HTTP release/dispatch |
| `POST /v1/detect`, `/v1/risk` | FeatureWindow, source-profile header, approved model; unavailable 503 |
| Incident writes | Trusted producer/operator; If-Match + Idempotency-Key, không public upload tùy ý |

H2 [backend](runbooks/EXPLANATIONS.md) và [API/UI](runbooks/INCIDENT_API.md) đã có
đường đọc local với deterministic fallback thật khi thiếu model/provider. Không
tự gọi Bedrock hoặc nghiệm thu G5. Action submission vẫn **planned**; observation
GET đã có với credential SELECT-only riêng, không cấp quyền execute.
CLI `scripts/review_payment_image.py` thu hồ sơ image/health cho operator review;
không tự tạo approved known-good ledger hoặc xác nhận physical source binding/H4.
MVP UI đọc IER thật từ worker/API trên môi trường B. Fixture chỉ dùng unit test
hoặc demo profile có nhãn, không đếm làm data/model evidence. B không cần raw train
của A để hoàn thành UI. Luôn hiển thị uncertainty/abstention và missing dependency.

## 6. Nội dung giao trực tiếp cho từng người

**Giao A:** hoàn tất A1→A4; sở hữu Data/AI/RCA/explanation backend theo contract,
giữ capture pipeline riêng; giao model candidate có evaluation và source parity,
không tự cấp activation khi gate thiếu.

**Giao B:** hoàn tất B1→B4; giao UI/local packaging bằng API hiện có ngay, làm
action/Kubernetes/AWS song song A; giao auth/deploy/known-good rollback/runbook;
chỉ bật model/cloud trong phạm vi được duyệt.

Owner đã nhận lane B/UI; [checklist thực thi B](delivery/DELIVERY_PLAN.md#lane-b-execution)
chốt lát cắt local đầu tiên, mapping fields API, isolation và thứ tự commit.
Các mục là việc cần triển khai, không phải capability đã có.

B có [sandbox Kubernetes cho service thật](runbooks/KUBERNETES_LOCAL.md) và renderer
image-digest; source observations giữ raw RED, trace/log và pod UID để A review.
Một bucket và metadata nhất quán không tự cấp source parity, known-good hoặc H4;
không dùng capture này làm corpus training.

Handoff gồm SHA, cách chạy, giới hạn, artifact URI/hash/version và dependency thật;
dùng commit/CI và tài liệu hiện có, không tạo report chỉ để ghi service đã PASS.
Chỉ giữ report riêng cho kết quả data/model/evaluation cần dùng lại. Raw/model binary truyền qua artifact store
được phép, không commit Git. Consumer phải có quyền truy cập và cách materialize/
verify; path chỉ tồn tại trên máy người gửi chưa đủ bàn giao. Không gửi holdout
raw ngoài quyền, không ghi token/DSN trong report.

Có thể gửi người mới URL tài liệu này và đoạn giao B. Họ đọc ba tài liệu team,
đối chiếu baseline của mình rồi bắt đầu, không đợi đọc hết lịch sử hoặc audit lại
toàn bộ hệ thống.
