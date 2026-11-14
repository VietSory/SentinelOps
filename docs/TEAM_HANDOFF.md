# SentinelOps — bắt đầu làm việc trong đội hai người

Baseline lịch sử: `main` sau PR #24, merge `f4891cf`, 2026-10-31. Trạng thái
bàn giao cập nhật 2026-11-14; không chứng minh máy mới đã có data/model/runtime.

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

**Đã có:** workload/observability, telemetry/features/replay, guarded fitting/loading,
health/incident workers, persistent API, conservative RCA, UI/H2 pin revision,
local operator/producer scopes và OIDC adapter. Model hiện hành là Alibaba
`de38fc6b` (ADR-019), G3 có điều kiện D-27; xem
[báo cáo parity/release/inference trên máy B](reports/LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md).
Lớp 2 `ald` đạt các ngưỡng phát triển S1/S3, nhưng chưa nghiệm thu đầy đủ
Q-04…Q-07 (thiếu S2/S4, MTTD live và holdout). Native trials của bundle cũ
`badf9da0` ghi 9/10 recovered, 1/1 failed-verify escalated; B đã review safety
tests. **Chưa nghiệm thu:** chất lượng model đầy đủ, full G4, G5, G6 và G8.
AWS/provider identity thật chưa triển khai.
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
| A: data/model hiện tại | [ADR-016](adr/ADR-016-public-production-training-source.md), [ADR-019](adr/ADR-019-breach-relative-retrain.md), [model packet](reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md), [layer 2](reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md), [models](runbooks/MODEL_PIPELINE.md); ADR-018 đã bị từ chối |
| A: worker/RCA | [health](runbooks/HEALTH_WORKER.md), [incident](runbooks/INCIDENT_WORKER.md), [RCA](runbooks/RCA.md), [RCAEval](runbooks/RCAEVAL_NATIVE_DEVELOPMENT.md) |
| B: API/DB/UI | [API runbook](runbooks/INCIDENT_API.md), [storage](runbooks/INCIDENT_STORE.md), `src/sentinelops/api.py`, `contracts/` |
| B: deploy/action | [ADR-006](adr/ADR-006-control-plane-and-storage.md), [ADR-009](adr/ADR-009-payment-rollback-runbook.md), technical spec §13–14, Compose/infra/observability |

## 3. Quyết định không cần research lại

1. Nguồn fitting hiện hành là **Alibaba v2022, ADR-016**, một bundle dùng chung
   cho ba service. Local HTTP và RCAEval chỉ evaluation; đường fitting local đã
   gỡ theo D-23. ADR-014/contract v2 là lịch sử, không tái dùng để fitting mới.
2. Giữ native service splits, train-only fitting, calibration riêng và source
   provenance theo ADR-016; local không fit/chọn model/chỉnh ngưỡng. ADR-017 pin
   identity của từng epoch, không tự gộp kết quả giữa các phiên bản. Final sealed.
3. Feature Alibaba `http-relative-5m/v2`, source `prometheus-increase-60s-5s/v2`.
   MAD/slope chỉ dùng đánh giá. Code hiện hành không nạp bundle v1 `badf9da0`.
   Thiếu model trả 503; thiếu history thì signal unavailable. Release Alibaba cần
   history nên `/detect` và `/risk` trả 409; đọc `service_signals` trong IER.
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
python -m scripts.train_alibaba --help
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
khi cô lập ports theo contract. Không reuse volume/secret A. OIDC adapter đã có
code/tests; provider thật còn thuộc giai đoạn deployment. Public API local bằng
static token không phải AWS acceptance.

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
| `POST /v1/detect`, `/v1/risk` | FeatureWindow, source-profile header, approved model; unavailable 503; Alibaba release 409 `model_requires_stream_history` (read `service_signals` from `GET /v1/incidents/{id}?revision=…`) |
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
