# SentinelOps — trạng thái triển khai

Cập nhật: **2026-11-15** (giờ Việt Nam). Trang này tóm tắt code và khoảng trống sản phẩm.
Service có đang chạy/ready hay không phải kiểm tra trên môi trường hiện tại;
report hoặc CI cũ không thay kết quả đó.

| Phần | Implementation / việc còn thiếu | Cách làm việc hoặc kiểm tra |
| --- | --- | --- |
| Workload / observability | Có ba services, commerce PostgreSQL, OTel, Prometheus/Jaeger local. Có renderer và sandbox kind cho service thật; đã quan sát Checkout/DB, RED bucket và pod UID nhất quán trong API/metric/trace/log. Đã có parity v2 trên các môi trường được báo cáo và H4 native trong trial; không suy thành G4/G6 hoặc AWS acceptance | [Testbed](runbooks/LOCAL_TESTBED.md), [Kubernetes local](runbooks/KUBERNETES_LOCAL.md), [observability](runbooks/OBSERVABILITY.md) |
| Telemetry / features | Có raw capture, quality/provenance, 60s buckets / 300s windows và replay | [Telemetry](runbooks/TELEMETRY.md), [timing](architecture/TRACE_TIMING.md) |
| Data / models | Fitting dùng Alibaba v2022 (ADR-016, ADR-019). Candidate **`de38fc6b`** (ADR-019) thay `badf9da0`: qua cổng chuỗi tổng hợp và kiểm tra phát triển trên `alb` (IF 12/12, precursor 20/20); lớp 1 IF F1 0,336, IF hơn MAD có ý nghĩa. Owner duyệt G3 có điều kiện (D-27); lớp 2 epoch `ald` chấm một lần 2026-11-14: kết quả phát triển S1/S3 tốt (IF 12/12, 0 báo sai, trễ 150 s; XGB precursor 20/20, AP 0,95 > slope 0,81–0,84) nhưng **chưa nghiệm thu Q-04…Q-07**: thiếu S2/S4, MTTD live và holdout G8 ( [báo cáo](reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md)) | [G3 packet](reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md), [ADR-019](adr/ADR-019-breach-relative-retrain.md), [model lifecycle](runbooks/MODEL_PIPELINE.md) |
| Incident pipeline | Có health/incident workers, control DB, IER revisions và protected read API | [Health](runbooks/HEALTH_WORKER.md), [incident](runbooks/INCIDENT_WORKER.md), [API](runbooks/INCIDENT_API.md) |
| Correlation / RCA | Có conservative grouping/abstention; **full G4 chưa nghiệm thu** | [RCA runbook](runbooks/RCA.md), [research baseline](reports/RCA_DEVELOPMENT.md) |
| Operator UI / B1 | Có UI read-only cùng origin `/ui/`: token trong tab, list/filter/cursor, revision-pinned detail/history/evidence, UNKNOWN/503 và model-unavailable. Đã chạy trên incident SLO thật của B, 4 immutable revisions, API/DB restart và browser outage/reconnect | [API/UI](runbooks/INCIDENT_API.md), [runtime B](runbooks/LOCAL_TESTBED.md); không coi ranking là root đã xác nhận |
| B2 integration / CI | Có explicit paired H1 wiring qua registry sẵn có; mặc định không model, đổi release của consumer hiện hữu cần migration A. Có immutable local image/config locks và host release v2: wheel/config/CLI/dependency copy, checksum guard, user systemd start/stop/restart. Đã kiểm API/auth/unavailable, SIGKILL/READY đúng PID và checkpoint PostgreSQL thật; backlog vẫn UNKNOWN. CI phân scope, browser/JS/host-process checks và always-run summary. Có operator Prometheus rules cho worker telemetry: heartbeat/readiness/absence/errors, source eligibility có guard decision, backlog và consumer progress; validate bằng promtool đúng image đã pin, đã quan sát Prometheus thật load 10 rules và alert staleness chuyển firing khi worker dừng. Có Grafana console read-only provisioned trên loopback 13000 (dashboard 11 panel), validate panel query theo metric thật + guard decision + absent-không-thành-healthy, đã xác minh Grafana thật load dashboard và mọi expression evaluate được trên Prometheus thật. Rules/dashboard chỉ là worker telemetry, không phải business/SLO health, model quality hay quyền action; thiếu model **không** alert. Grafana anonymous Viewer là sandbox, **không phải production auth**. Có local scopes và OIDC/JWKS adapter với tests; provider thật chưa triển khai | [Host processes](runbooks/HOST_PROCESSES.md), [observability rules](runbooks/OBSERVABILITY.md), [runtime/lock scope](runbooks/LOCAL_TESTBED.md), [workstream contract](delivery/WORKSTREAM_CONTRACT.md) |
| B3 guarded executor | Có internal executor, scoped Kubernetes/RBAC, known-good verifier, PostgreSQL action journal, resume-only worker và SELECT-only API/UI. Có CLI thu hồ sơ image trước/sau + current raw health để operator review, không tự phê duyệt hay xác nhận physical source binding. Có operator review hai bước để gỡ quarantine bằng receipt riêng, đúng rollout/healthy buckets; không đổi `escalated`, IER, cooldown hay attempt budget. Đã kiểm guards, PostgreSQL outage/concurrency và Kubernetes/RBAC. Native trials đã rollback Payment thật; B review lại Q-10 và bổ sung executor/HTTP-adapter checks ngày 2026-11-12. G6 vẫn chờ nghiệm thu | [Controlled remediation](runbooks/CONTROLLED_REMEDIATION.md); Known-good được duyệt trước thay đổi xấu và native H4 đã cấp authority trong các trial được ủy quyền; không có public dispatch, unsupported quarantines vẫn giữ khóa. Xem [Q-09/Q-10](reports/G6_LOCAL_ALIBABA_BADF9DA0.md) |
| Explanation / H2 | Có backend A với deterministic fallback và bounded provider adapter; B có authenticated GET/wrapper riêng và UI pin revision, provider/fallback, refs và error states. Đã mở trên IER SLO thật revision 4 trong DB B, 12/13 evidence claims, refs hợp lệ và IER không đổi. Browser fixture chỉ chứng minh phần mềm. Không tự bật Bedrock, ghi EXPLAINED hay cấp action; G5 chưa nghiệm thu | [Backend](runbooks/EXPLANATIONS.md), [API/UI](runbooks/INCIDENT_API.md) |
| Action activation / AWS | Auto action chưa bật, AWS **chưa triển khai**; G6–G8 vẫn mở. Đã có IaC baseline mô tả network/EKS/RDS/S3/ECR/IAM/observability một region: private by default, encrypted, retention có giới hạn, role tách theo workload không admin, controller không có quyền mutate AWS, Bedrock invoke mặc định không được cấp. Validate thật bằng `terraform validate`/`fmt` + 14 guard; tests chứng minh guard bắt được vi phạm. **Chưa apply, chưa gọi AWS API nào, không có credential**; region/quota/budget/model access/quyền apply do owner xác nhận riêng | [AWS baseline](runbooks/AWS_BASELINE.md), [plan hai người](delivery/DELIVERY_PLAN.md), [gates](architecture/CONSTRAINTS_AND_GATES.md) |

[ADR-016](adr/ADR-016-public-production-training-source.md) thay nguồn fitting bằng
Alibaba v2022; ADR-014/contract v2 và candidate local giữ lịch sử theo D-23.
Local HTTP và RCAEval chỉ evaluation, không fitting/tuning.

B đã thêm [portable API image và manifest offline](runbooks/CONTROL_PLANE_IMAGE.md):
wheel/ML dependencies, hai worker CLI, non-root/read-only, mặc định không
model/credential. PR #52: **MERGED** vào `8ed9ff6`; head `a77332c` có CI **14/14 PASS**
(run `43616362464`). Head rollout fix `333517f` có **123 focused tests PASS**,
API + business manifests **14/14 valid** với fixture references; CI `43618472435`:
**14/14 PASS, 0 skipped jobs**. Checksum public config làm thay đổi pod template khi cấu hình
đổi; Secret bytes vẫn cần rollout explicit. Optional action-reader Secret binding
`9b5f71b` đã triển khai (**87 focused tests PASS**, **5 valid manifests**). PR #53
đã **MERGED** vào `6590a8c`; CI head `f1d4aff`, run `43620994259`: **14/14 PASS,
0 skipped jobs**. Reader vẫn phải có SELECT-only và optional migrations đúng runbook.
CI sau merge #53: run `43665706369`, đúng SHA
`6590a8cb0de5e704a630614032cf6e1a2791bf81`, **14/14 PASS, 0 skipped jobs**.

ECR PR #49 đã được B review/sửa guard và **MERGED** vào `0364251`; head `fd7a010`,
CI `43613141372` attempt 2 **14/14 PASS**, node pull IAM giữ baseline. Đây vẫn
là packaging/IaC offline, chưa registry publication hoặc deployment AWS. Cloud
worker adapters/source policy, observability delivery thực tế và H3 còn thiếu;
không nới guard local để phát manifest worker.

Follow-up CloudWatch/KMS: đã bổ sung key policy cho regional Logs, giới hạn đúng
ba log-group ARN; telemetry-reader chỉ decrypt/describe qua Logs, flow-log writer
chỉ dùng key cho nhóm VPC của mình. PR #54 đã **MERGED** vào `ed878da`; head
`ae62a6a`, run `43667608441`: **14/14 jobs PASS**, gồm validate/14 guard và
**2 mock test PASS**, không có AWS credentials. CI sau merge `43669428327` cũng
**14/14 jobs PASS, 0 skipped jobs**, đúng SHA `ed878dad53177ceb52f528fc36a58427bae36166`.
Xem [binding](runbooks/AWS_BASELINE.md#cloudwatch-encryption-binding).

B đã thêm [CloudWatch operations dashboard offline](runbooks/AWS_BASELINE.md#cloudwatch-operations-dashboard):
business request/error series theo route/status, control RDS và OTLP raw logs.
Không fill missing thành zero/healthy, không suy p95/model readiness hoặc thêm
action/alarms. 47 IaC guard tests PASS; Terraform mock evaluation theo CI head
của thay đổi. Chưa apply/kiểm console, viewer permissions hay live AWS delivery.

B đã bổ sung [collector AWS offline](runbooks/AWS_BASELINE.md#offline-aws-collector):
6-resource renderer, CloudWatch EMF/OTLP Logs/X-Ray, projected STS token và role
`telemetry-exporter` riêng. Reader không được tái dùng làm exporter. 111 focused
Python tests PASS; binary contrib 0.162.0 đã validate config trong container
không mạng. PR #55, run `43671071492`, head `31ca5b8`: IaC job PASS với 3 native
Terraform mock tests, checker collector hợp lệ/invalid PASS và kubeconform strict
1.34: **6 valid, 0 invalid/errors/skips**. Foundation: **1537 passed, 37 skipped**,
installed wheel PASS. Head cuối `28d6464`, run `43671259567`: **14/14 PASS,
0 skipped jobs**. PR #55 đã **MERGED** vào `04caa52`; CI merge `43673513991`
đúng SHA `04caa52a030d59843c09320ebb9b42c3c1632ae0`: **14/14 PASS**.
Chưa deploy/gửi telemetry AWS; delivery verification và H3 vẫn thiếu.

Follow-up business logs: emitter OTLP opt-in (`--export-logs`) giữ JSON stdout,
event/context/pod identity, queue hữu hạn và timeout riêng. Mặc định local tắt,
không thêm AWS identity vào business pods. PR #56 head `791cc06`, run `43674451205`:
**14/14 PASS, 0 skipped jobs**, foundation **1554 passed, 37 skipped**, gồm ba ca
loopback gRPC/middleware/outage thật; kubeconform **15 valid, 0 invalid/errors/skips**.
Local socket/launcher execution vẫn bị approval bridge chặn. Follow-up đồng bộ
`LOG_LEVEL` cho stdout/OTLP cần đối chiếu CI head mới, không kế thừa PASS head cũ.
Chỉ business events được export; stdout hạ tầng/DB/uvicorn còn cần collection riêng.
Xem [log export](runbooks/AWS_BASELINE.md#business-event-log-export).

Follow-up host: launcher quan sát API/health/incidents `inactive`, giữ release
`5c520ae6…` và stream `b95058b4…`. Guarded restart API bị approval bridge từ chối;
**chưa khôi phục runtime sau reboot**. CI image/host-process trên runner không
thay bằng chứng host đang hoạt động. Xem [báo cáo B cập nhật](reports/LANE_B_DELIVERY_REPORT_2026-11-14.md).

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

**Local follow-up sau merge PR #47 (2026-11-14):** main `6f6e587` có CI 14/14
PASS; Q-10 111 tests/22 PostgreSQL checks PASS; UI/explanation trên incident
`de38fc6b` thật và native S2/H4 đã kiểm xong. PR #48 đã có capture S4
sau review fail-closed ở `3c36e2e`, source review PASS; RCA abstain, RDS đứng
thứ ba nên chưa chứng minh RCA đúng hoặc nghiệm thu Q-08. Independent detector
MTTD vẫn chưa đo. Q-09 của release mới cần bộ trial riêng; 9/10 cũ thuộc
`badf9da0`. Xem [checklist local hiện hành](delivery/LANE_B_CHECKPOINT.md#10-local-follow-up-sau-merge--2026-11-14).

## Việc tiếp theo

- **A:** đã train lại theo ADR-019 (`de38fc6b`); lớp 2 `ald` (2026-11-14) cho kết quả phát triển S1/S3 tốt, chưa nghiệm thu Q-04…Q-07 (thiếu S2/S4, MTTD live, holdout); full G4/G8 chưa nghiệm thu.
- **B:** đã tích hợp local bundle `de38fc6b` theo D-27: parity P1–P6 PASS, host/model release mới, stream epoch riêng, baseline và live-check PASS với model signal được tái tính từ accepted history ([báo cáo](reports/LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md)). PR #52 và action-reader #53 đã merge; tiếp tục observability deployment bindings sau sửa prerequisite KMS. Resume host qua launcher khi execution được phép. Q-09/G6 của release mới cần trial/known-good riêng; provider identity, EKS policy/adapter và AWS chỉ triển khai khi đủ quyền/phạm vi.

[Lane B checkpoint](delivery/LANE_B_CHECKPOINT.md) chốt mức bằng chứng từng
capability (runtime-verified / fixture-tested / code-only / blocked), commit và CI
đúng head, hai mục cần A quyết định, và thứ tự bước tiếp theo kèm blocker thật.

Theo [ownership/interfaces](delivery/WORKSTREAM_CONTRACT.md). Cập nhật trang này
khi capability/gap thay đổi; kiểm tra thông thường ghi ở commit/CI, không thêm
nhật ký PASS hoặc report riêng cho mỗi lane.
