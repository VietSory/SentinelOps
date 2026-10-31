# SentinelOps — hợp đồng giữa A và B

Theo [kế hoạch hai người](DELIVERY_PLAN.md), baseline `f4891cf` (2026-10-31).
API/schema hiện có giữ wire truth. Path ghi **mới** là nơi sẽ triển khai, chưa có
implementation chỉ vì được ghi ở đây.

## 1. Một owner cho mỗi vùng code

| Owner | Đường dẫn / phạm vi |
| --- | --- |
| **A** | `src/sentinelops/ml/`, `experiments/`, `telemetry/`, `public_data/`, `workers/`; `trace_query.py`, `trace_evidence.py` |
| **A** | `src/sentinelops/incidents/`: correlation*, health*, rca*, native_rca.py; backend **mới** `src/sentinelops/explanations/` |
| **A** | Capture/training/model/SLO/RCA/evaluation configs; scripts capture/audit/train/evaluate/replay và worker runners tương ứng |
| **B** | `src/sentinelops/api.py`, `api_http.py`, `api_settings.py`, `main.py`; `incidents/domain.py`, `repository.py`, `validation.py`; **mới** `src/sentinelops/remediation/`, `auth/`, `ui/` |
| **B** | `services/`, `docker/`, Compose, `infra/`, `observability/`, `.github/`, `pyproject.toml`, `src/sentinelops/contracts.py`, `schema_registry.py`, `scripts/validate_contracts.py`; configs deployment mới |
| **Theo module** | Tests/samples/runbook của module; report A `docs/reports/ai-data-delivery.md`, report B `docs/reports/platform-delivery.md` |
| **B điều phối** | README, team docs, PROJECT_STATUS, TECHNICAL_SPEC: cập nhật gộp ở mốc integration; cả hai không sửa hằng ngày |

Schema/ADR không tự đổi theo ownership. A đề xuất model/telemetry contract, B đề
xuất API/envelope/action contract; producer+consumer kiểm compatibility trước
version mới. Không đổi strict IER để tiện UI. Path chưa phân công: ghi owner vào
report trước khi sửa, không mặc định hai người cùng sở hữu.

Cần sửa vùng lane kia thì giao yêu cầu/patch nhỏ cho owner đó. Chỉ item bị ảnh hưởng
chờ; các item khác tiếp tục. Shared-interface review chỉ xem diff/caller liên quan,
không bắt review chéo mọi commit hoặc giả là có reviewer khi chỉ self-review.

## 2. Cô lập Git, Docker và data

- Hai clone/checkout/virtualenv riêng; không hai writer chung Git index, corpus
  root, model store hoặc worker checkpoint.
- A có capture checkout sạch/bất biến và Docker/VM dành cho đo data; B có testbed/
  DB riêng. Không đổi source/image/policy/fault/context của acquisition đang mở.
- Khác Compose project **chưa đủ**: nhiều host ports đang hardcode. Ưu tiên VM/
  host riêng. Nếu chung host, B tạo override và kiểm rendered config trước up;
  đổi **tất cả** published ports, không chỉ PostgreSQL. Không chạy load/stress B
  làm nhiễu capture A. Nếu capacity không tách được, chỉ phép đo tranh tài nguyên
  cần xếp lịch; coding/UI/IaC không phải chờ.
- A giữ ports testbed hiện có và corpus PostgreSQL `55435`. B reserve API `18000`,
  services `18001–18003`, commerce/control DB `15432/15433`, Prometheus `19090`,
  Jaeger `26686`, OTLP `14317/14318`, exporter `18889`, Collector health `23133`,
  Grafana `13000`. Đây là allocation để B triển khai, **chưa có override sẵn**;
  cấu hình script URL theo môi trường, không giả CLI có flag mới.
- Project/namespace/volume/root có owner rõ. Không stop/prune/delete tài nguyên
  lane kia, không mount writable corpus A vào app B. Raw/model artifacts immutable.

## 3. H1 — model candidate và activation

**Đã có:** `ModelRegistry`, `sentinelops-model/v2`, operator ledger
`validated-model-release/v1`; xem [models](../runbooks/MODEL_PIPELINE.md).

A giao ba service bundles, `training-manifest.json`, dataset freeze/policy refs,
checksums, library/code versions, evaluation/model cards và source-parity status.
Handoff URI phải materialize được artifacts mà loader/verifier cần; không chỉ
copy model/pickle hoặc path tuyệt đối của máy A. Manifest hash là SHA-256 bytes.

B tích hợp bằng settings hiện có:

```text
SENTINELOPS_MODEL_STORE=<trusted materialized store>
SENTINELOPS_MODEL_RELEASE_REF=artifact://manifests/<content-hash>.json
```

Ledger `validated-model-release/v1` có `models[]`: `bundle_directory` tương đối
trong trusted store, `manifest_sha256`, `validation_ref`. Approval khớp model/
dataset/policy, có evaluation refs và G2/G3/source_parity/activation đủ điều kiện
loader; activation trên AWS còn theo môi trường đích/G7. Flag không thay evidence
hay quyết định operator; training không tự viết approval để bật model.

B hoàn thành wiring/packaging/unavailable UI/rollback release trước H1. Chỉ bước
**bật inference thật** chờ model đủ gate. Activation pin release và restart có
kiểm soát, giữ previous release; không thêm distributed hot-reload cho MVP.

## 4. H2 — IER sang explanation/UI

**Đã có:** IER, IncidentEnvelope revision/context và GroundedExplanation schema.
**Chưa có:** provider/service implementation và HTTP explanation route.

Boundary chốt cho implementation mới:

1. A cung cấp async entry point
   `sentinelops.explanations.service.explain_incident(envelope, *, provider=None)`.
   Input là validated IncidentEnvelope pin incident/revision; output dictionary
   `{"payload": <GroundedExplanation>, "provider": <id>, "fallback": <bool>}`.
   Provider absent/timeout/invalid phải deterministic fallback. Module chỉ dùng
   facts/refs của revision input, không đọc API token hoặc DB credentials của B.
2. B sở hữu route planned `GET /v1/incidents/{id}/explanation?revision=N`, auth,
   tải envelope và response wrapper versioned gồm `incident_id`, `revision`,
   `generated_at`, `provider`, `fallback`, `payload`. B bổ sung JSON Schema/OpenAPI
   wrapper trước khi coi route delivered. Không trả wrapper dưới schema
   GroundedExplanation cũ hoặc nhét metadata vào exact IER.
3. Hai bên test producer/consumer theo boundary này. B wiring sau khi module có;
   trước đó UI đọc IER và explanation unavailable. Test double chỉ dùng unit test;
   tính năng final phải chạy trên IER thật/fallback thật.
4. Nếu cache: key incident+revision+prompt/provider version. Không sửa ranking,
   bịa refs/numbers hoặc diễn giải abstention thành root chắc chắn. Response cũ
   không được ghi đè revision mới. LLM không được gọi action.

Boundary này không tự tạo implementation/schema hoặc cấp quyền gọi Bedrock.

## 5. H3 — deployment và nguồn AWS

B giao image digests/config version, endpoints/quyền private, metric/log/trace
locations, field/unit/timestamp mapping, retention và capture thật có hash. A
sở hữu adapter vào telemetry/features và kiểm missingness, event/receipt clocks,
units, denominator/business routes và source/live parity. Không biến resource
metric thành HTTP RED hoặc suy p95 từ statistic không phù hợp.

Source profile mới phải versioned; compatible hay cần dataset/model mới là kết
quả phép kiểm, không phải flag để deployment xanh. Trước parity/validation đủ,
AWS incident UI có thể dùng nhưng model inference chưa được claim ready.

Một AWS environment, quyền theo workload; B chọn IRSA/Pod Identity phù hợp SDK/
baseline, tách role telemetry, Bedrock và runbook mutation; không role admin chung
([EKS IAM guidance](https://docs.aws.amazon.com/eks/latest/best-practices/identity-and-access-management.html)).
Region/model access/quota/budget và quyền apply được owner xác nhận trước chi phí;
IaC plan không tự cấp quyền cloud. Final holdout có quyền mở riêng.

## 6. H4 — RCA sang action policy

A giao IER/revision và RCA/evidence references có scope, freshness, coverage,
confidence/abstention thật. B sở hữu quyết định policy, known-good ledger,
approval/dry-run, execution, audit và verify. Không lấy lời explanation làm quyền
hành động. Khi RCA abstain hoặc evidence không đủ, auto action vẫn blocked.

B triển khai executor và guard tests trên sandbox riêng trước H4, nhưng demo
auto-remediation end-to-end phải chờ một incident đủ điều kiện thật. Test fixture
không cấp quyền mutate production và không chứng minh quality gate. Đây là
dependency cuối thực sự, không phải lý do dừng UI, local runbook hay cloud plan.

## 7. Nhận bàn giao từng boundary

H1/H2/H3/H4 Ready khi có version, producer SHA, URI/hash hoặc import path, contract,
cách chạy và failure behavior. Consumer xác minh trên môi trường mình, ghi PASS/
FAIL/BLOCKED và thiếu hụt cụ thể. Không đợi toàn lane producer xong.

Hai report riêng là checkpoint. Đổi interface nêu version cũ/mới, caller bị tác
động, migration và regression cần chạy; cùng chốt boundary rồi tiếp tục.
Chỉ chờ ở compatibility/activation/side effect thật sự cần đầu ra của nhau.
