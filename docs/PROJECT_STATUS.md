# SentinelOps — trạng thái triển khai

Cập nhật: **2026-11-12**. Trang này tóm tắt code và khoảng trống sản phẩm.
Service có đang chạy/ready hay không phải kiểm tra trên môi trường hiện tại;
report hoặc CI cũ không thay kết quả đó.

| Phần | Implementation / việc còn thiếu | Cách làm việc hoặc kiểm tra |
| --- | --- | --- |
| Workload / observability | Có ba services, commerce PostgreSQL, OTel, Prometheus/Jaeger local. Có renderer và sandbox kind cho service thật; đã quan sát Checkout/DB, RED bucket và pod UID nhất quán trong API/metric/trace/log. Đã có parity v2 trên các môi trường được báo cáo và H4 native trong trial; không suy thành G4/G6 hoặc AWS acceptance | [Testbed](runbooks/LOCAL_TESTBED.md), [Kubernetes local](runbooks/KUBERNETES_LOCAL.md), [observability](runbooks/OBSERVABILITY.md) |
| Telemetry / features | Có raw capture, quality/provenance, 60s buckets / 300s windows và replay | [Telemetry](runbooks/TELEMETRY.md), [timing](architecture/TRACE_TIMING.md) |
| Data / models | Fitting dùng Alibaba v2022 (ADR-016, ADR-019). Candidate **`de38fc6b`** (ADR-019) thay `badf9da0`: qua cổng chuỗi tổng hợp và kiểm tra phát triển trên `alb` (IF 12/12, precursor 20/20); lớp 1 IF F1 0,336, IF hơn MAD có ý nghĩa. Chờ owner G3; lớp 2 epoch `ald` đang capture, chấm một lần | [G3 packet](reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md), [ADR-019](adr/ADR-019-breach-relative-retrain.md), [model lifecycle](runbooks/MODEL_PIPELINE.md) |
| Incident pipeline | Có health/incident workers, control DB, IER revisions và protected read API | [Health](runbooks/HEALTH_WORKER.md), [incident](runbooks/INCIDENT_WORKER.md), [API](runbooks/INCIDENT_API.md) |
| Correlation / RCA | Có conservative grouping/abstention; **full G4 chưa nghiệm thu** | [RCA runbook](runbooks/RCA.md), [research baseline](reports/RCA_DEVELOPMENT.md) |
| Operator UI / B1 | Có UI read-only cùng origin `/ui/`: token trong tab, list/filter/cursor, revision-pinned detail/history/evidence, UNKNOWN/503 và model-unavailable. Đã chạy trên incident SLO thật của B, 4 immutable revisions, API/DB restart và browser outage/reconnect | [API/UI](runbooks/INCIDENT_API.md), [runtime B](runbooks/LOCAL_TESTBED.md); không coi ranking là root đã xác nhận |
| B2 integration / CI | Có explicit paired H1 wiring qua registry sẵn có; mặc định không model, đổi release của consumer hiện hữu cần migration A. Có immutable local image/config locks và host release v2: wheel/config/CLI/dependency copy, checksum guard, user systemd start/stop/restart. Đã kiểm API/auth/unavailable, SIGKILL/READY đúng PID và checkpoint PostgreSQL thật; backlog vẫn UNKNOWN. CI phân scope, browser/JS/host-process checks và always-run summary. Có operator Prometheus rules cho worker telemetry: heartbeat/readiness/absence/errors, source eligibility có guard decision, backlog và consumer progress; validate bằng promtool đúng image đã pin, đã quan sát Prometheus thật load 10 rules và alert staleness chuyển firing khi worker dừng. Có Grafana console read-only provisioned trên loopback 13000 (dashboard 11 panel), validate panel query theo metric thật + guard decision + absent-không-thành-healthy, đã xác minh Grafana thật load dashboard và mọi expression evaluate được trên Prometheus thật. Rules/dashboard chỉ là worker telemetry, không phải business/SLO health, model quality hay quyền action; thiếu model **không** alert. Grafana anonymous Viewer là sandbox, **không phải production auth**. Có local scopes và OIDC/JWKS adapter với tests; provider thật chưa triển khai | [Host processes](runbooks/HOST_PROCESSES.md), [observability rules](runbooks/OBSERVABILITY.md), [runtime/lock scope](runbooks/LOCAL_TESTBED.md), [workstream contract](delivery/WORKSTREAM_CONTRACT.md) |
| B3 guarded executor | Có internal executor, scoped Kubernetes/RBAC, known-good verifier, PostgreSQL action journal, resume-only worker và SELECT-only API/UI. Có CLI thu hồ sơ image trước/sau + current raw health để operator review, không tự phê duyệt hay xác nhận physical source binding. Có operator review hai bước để gỡ quarantine bằng receipt riêng, đúng rollout/healthy buckets; không đổi `escalated`, IER, cooldown hay attempt budget. Đã kiểm guards, PostgreSQL outage/concurrency và Kubernetes/RBAC. Native trials đã rollback Payment thật; B review lại Q-10 và bổ sung executor/HTTP-adapter checks ngày 2026-11-12. G6 vẫn chờ nghiệm thu | [Controlled remediation](runbooks/CONTROLLED_REMEDIATION.md); Known-good được duyệt trước thay đổi xấu và native H4 đã cấp authority trong các trial được ủy quyền; không có public dispatch, unsupported quarantines vẫn giữ khóa. Xem [Q-09/Q-10](reports/G6_LOCAL_ALIBABA_BADF9DA0.md) |
| Explanation / H2 | Có backend A với deterministic fallback và bounded provider adapter; B có authenticated GET/wrapper riêng và UI pin revision, provider/fallback, refs và error states. Đã mở trên IER SLO thật revision 4 trong DB B, 12/13 evidence claims, refs hợp lệ và IER không đổi. Browser fixture chỉ chứng minh phần mềm. Không tự bật Bedrock, ghi EXPLAINED hay cấp action; G5 chưa nghiệm thu | [Backend](runbooks/EXPLANATIONS.md), [API/UI](runbooks/INCIDENT_API.md) |
| Action activation / AWS | Auto action chưa bật, AWS **chưa triển khai**; G6–G8 vẫn mở. Đã có IaC baseline mô tả network/EKS/RDS/S3/IAM/observability một region: private by default, encrypted, retention có giới hạn, role tách theo workload không admin, controller không có quyền mutate AWS, Bedrock invoke mặc định không được cấp. Validate thật bằng `terraform validate`/`fmt` + 13 guard; tests chứng minh guard bắt được vi phạm. **Chưa apply, chưa gọi AWS API nào, không có credential**; region/quota/budget/model access/quyền apply do owner xác nhận riêng | [AWS baseline](runbooks/AWS_BASELINE.md), [plan hai người](delivery/DELIVERY_PLAN.md), [gates](architecture/CONSTRAINTS_AND_GATES.md) |

[ADR-016](adr/ADR-016-public-production-training-source.md) thay nguồn fitting bằng
Alibaba v2022; ADR-014/contract v2 và candidate local giữ lịch sử theo D-23.
Local HTTP và RCAEval chỉ evaluation, không fitting/tuning.
[ADR-018](adr/ADR-018-proposed-monotone-latency-detector.md) bị owner từ chối 2026-11-12:
detection phải do model đã train quyết định, không dùng MAD/slope; epoch `alc` dừng khi chưa chấm và đã xoá.

B có [business manifests renderer](runbooks/AWS_BASELINE.md#offline-business-service-manifests)
cho ba service với digest/Secret reference/probes, được kiểm input và schema
Kubernetes 1.34 offline. Đây là configuration-only: chưa deploy, chưa kiểm source
parity hoặc registry/commerce/collector của AWS. [API-only preview PR #45](runbooks/HOST_PROCESSES.md#api-only-ui-rollout)
trên máy B đọc IER thật và giữ nguyên DB/model/checkpoint qua restart; incident
được tạo ngày 2026-11-09, không phải workload hay inference mới.

[CI](https://github.com/VietSory/SentinelOps/actions/workflows/ci.yml) cần đối chiếu
đúng SHA. Liveness, DB/model readiness, corpus admission và quality gates là các
trạng thái khác nhau. Raw/model artifacts nằm trên môi trường tương ứng, ngoài Git.

Auth local B2 có credential operator chỉ đọc riêng với producer nhập/sửa IER;
UI dùng file private `operator-token`, producer cũ giữ scope tương thích. Operator
không được ghi revision; credential không cấp model/action authority. Host release
mới truyền hai credential vào API, không truyền cho workers; bundle v2 cũ giữ
cấu hình producer-only. Production identity vẫn fail closed. Xem
[API scopes](runbooks/INCIDENT_API.md) và [host credentials](runbooks/HOST_PROCESSES.md).

UI kiểm bearer permission qua protected readiness khi connect/refresh, mỗi 30 giây
khi tab visible và khi quay lại tab. Quan sát 401/403 `insufficient_scope` sẽ xóa
private state; lỗi mạng của public readiness không che được denial này. Provider
outage giữ các record đã tải với lỗi readiness/UNKNOWN. Các đường này được kiểm
bằng JS/API và browser HTTP fixtures, không nghiệm thu identity provider thật.

Có [demo guide local](runbooks/DEMO_LOCAL.md) cho đường chạy B hiện tại: stack 9
container, API/worker, ba loại readiness khác nhau, ranh giới operator/producer,
UI pin revision, explanation fallback thật và Grafana console. Guide ghi rõ phần
**không** được nghiệm thu từ demo (RCA accuracy, Bedrock, auto action, AWS).
Walkthrough no-model và blocker cũ được ghi ngày rõ ràng; deployment Alibaba
có báo cáo riêng, không lấy readiness thay model quality.

**Nguồn legacy:** việc hash toàn bộ Prometheus config từng fork stream khi thêm
`rule_files`; A đã sửa source identity v2. [Báo cáo máy B](reports/LANE_B_ALIBABA_V2_ACTIVATION.md)
ghi rollover và live-check PASS ngày 2026-11-09. Stream/checkpoint cũ giữ nguyên;
máy khác nâng release theo runbook, không xóa checkpoint để né guard.

**ADR-015 (2026-11-08):** IF/MAD đạt 0/20 trên development vì bộ dữ liệu chỉ có run
gradual (XGBoost AP 0.93–1.00). Owner duyệt thêm epoch S1 lỗi đột ngột chỉ để đánh
giá, cùng protocol `local-model-development/v2` bên cạnh v1; kết quả v1 giữ nguyên.
H4 phụ thuộc H1 (`anomaly_strength` của IF). Xem [handoff B](delivery/LANE_B_CHECKPOINT.md#7-handoff-a--b-ngày-2026-11-08-adr-015-và-việc-b-làm-song-song).
Ngày 2026-11-09 owner duyệt gỡ đường train trên dữ liệu local. Các công cụ của
candidate local (`build_model_release`, `check_model_source_parity`,
`g3_review_packet`, handoff/verify, `train_models`) đã gỡ khỏi code; bằng chứng
`38bcc766` dưới đây giữ nguyên và tra được trong git history. Đường hiện hành:
`check_alibaba_source_parity`, `build_alibaba_release`, `run_s2_h4`, `run_g6_trial`
([model pipeline](runbooks/MODEL_PIPELINE.md#source-parity-and-serving-release)).

Đường hiện hành ở [model runbook](runbooks/MODEL_PIPELINE.md#source-parity-and-serving-release);
§7.5 của checkpoint giữ candidate local đã retired. Chẩn đoán candidate local cũ:
IF báo nhầm trên run healthy vì khoảng p95 healthy của tập train rất hẹp (IF không
phụ thuộc vào thang đo feature). Không dùng chẩn đoán này thay kết quả Alibaba
lớp 2 bên dưới.

**Mốc native ngày 2026-11-08** (local Kubernetes, release H1 `656bc7c5` dựng từ G3 có điều kiện D-19):
- **H4 PASS:** `run_s2_h4` cấp dispatch authority cho incident S2; Payment score 0,799.
- **Trial G6 đầu tiên `recovered`:** known-good được duyệt trước thay đổi xấu → H4 → rollback → 3 bucket telemetry healthy sau action → `recovered`, journal đầy đủ.

Đây là **1 trial**. G6 cần ≥ 9/10 trial (Q-09) cùng ma trận safety, nên **G6 chưa PASS**. Hai lần thử trước dừng do lỗi của harness, và đều đã được sửa:
- traffic bị ngắt lúc thay pod;
- image xấu nằm khác repo, nên guard chặn đúng.

**Mốc native ngày 2026-11-09 với model chính thức Alibaba** (release `3966e75e`, G3 có điều kiện D-22):
- **Source parity profile v2 PASS** (P1–P6): mean latency Prometheus lệch tối đa 4,3% so với log server.
- **H4 PASS:** health stream v2, 11 phút healthy trước image xấu; IF Alibaba cho `anomaly_strength` 0,989, Payment score 0,797, coverage 0,80, `dispatch_authority=true`.
- **G6 local Q-09 đạt đúng ngưỡng** ([báo cáo](reports/G6_LOCAL_ALIBABA_BADF9DA0.md)): 9/10 golden-path recovered (trial 04 blocked do race của harness, vẫn tính); 1/1 failed-verify escalated với audit đầy đủ; 0 patch không an toàn trên 10 lần mutate. **G6 chưa PASS**: B đã review lại Q-10 và bổ sung kiểm executor/adapter; owner chưa quyết định nghiệm thu.
- **Lớp 2 xong 2026-11-12** ([báo cáo](reports/LAYER2_LOCAL_ALIBABA_BADF9DA0.md)): 5 nhóm, 25 run, chạy evaluation đúng một lần, không fit, không chỉnh ngưỡng. Nhóm `alb-04` bị owner loại cả nhóm vì máy ngủ trong lúc warmup, quyết định ghi trước khi có điểm. **Model Alibaba không chuyển được sang lỗi local ở ngưỡng đã đóng băng:** IF bắt 1/12 sự cố latency, MAD bắt 12/12 (6 báo nhầm ở checkout); XGBoost precursor recall 0/20, slope 20/20; lỗi 5xx do SLO rule bắt 8/8. Q-04, Q-05, Q-07 **FAIL** (development). Candidate kế nhiệm cần thiết kế mới trên Alibaba và epoch mới; ADR-018 (phục vụ MAD/slope) bị owner từ chối; hướng tiếp theo là train lại IF/XGBoost trên Alibaba.

## Việc tiếp theo

- **A:** đã train lại theo ADR-019 (`de38fc6b`); capture epoch `ald` từ 2026-11-13 01:13, chấm một lần khi xong; full G4/G8 chưa nghiệm thu.
- **B:** khi owner duyệt G3 `de38fc6b`, đo parity một lần với gói mới rồi build release; giao review Alibaba/Q-10 và hồ sơ G6 cho owner; provider identity, EKS policy/adapter và AWS chỉ triển khai khi đủ quyền/phạm vi. UI/packaging/monitoring local không cần làm lại.

[Lane B checkpoint](delivery/LANE_B_CHECKPOINT.md) chốt mức bằng chứng từng
capability (runtime-verified / fixture-tested / code-only / blocked), commit và CI
đúng head, hai mục cần A quyết định, và thứ tự bước tiếp theo kèm blocker thật.

Theo [ownership/interfaces](delivery/WORKSTREAM_CONTRACT.md). Cập nhật trang này
khi capability/gap thay đổi; kiểm tra thông thường ghi ở commit/CI, không thêm
nhật ký PASS hoặc report riêng cho mỗi lane.
