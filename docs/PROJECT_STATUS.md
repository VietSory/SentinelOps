# SentinelOps — trạng thái triển khai

Cập nhật: **2026-11-04**. Trang này tóm tắt code và khoảng trống sản phẩm.
Service có đang chạy/ready hay không phải kiểm tra trên môi trường hiện tại;
report hoặc CI cũ không thay kết quả đó.

| Phần | Implementation / việc còn thiếu | Cách làm việc hoặc kiểm tra |
| --- | --- | --- |
| Workload / observability | Có ba services, commerce PostgreSQL, OTel, Prometheus/Jaeger local. Có renderer và sandbox kind cho service thật; đã quan sát Checkout/DB, RED bucket và pod UID nhất quán trong API/metric/trace/log. Chưa chấp nhận Kubernetes source parity, known-good hay H4/G6 | [Testbed](runbooks/LOCAL_TESTBED.md), [Kubernetes local](runbooks/KUBERNETES_LOCAL.md), [observability](runbooks/OBSERVABILITY.md) |
| Telemetry / features | Có raw capture, quality/provenance, 60s buckets / 300s windows và replay | [Telemetry](runbooks/TELEMETRY.md), [timing](architecture/TRACE_TIMING.md) |
| Data / models | Có prospective HTTP admission, campaign, fitting/bundles/registry guards; **G2/G3 chưa nghiệm thu** | Audit corpus hiện tại theo [corpus runbook](runbooks/LOCAL_HTTP_CORPUS.md); [model lifecycle](runbooks/MODEL_PIPELINE.md) |
| Incident pipeline | Có health/incident workers, control DB, IER revisions và protected read API | [Health](runbooks/HEALTH_WORKER.md), [incident](runbooks/INCIDENT_WORKER.md), [API](runbooks/INCIDENT_API.md) |
| Correlation / RCA | Có conservative grouping/abstention; **full G4 chưa nghiệm thu** | [RCA runbook](runbooks/RCA.md), [research baseline](reports/RCA_DEVELOPMENT.md) |
| Operator UI / B1 | Có UI read-only cùng origin `/ui/`: token trong tab, list/filter/cursor, revision-pinned detail/history/evidence, UNKNOWN/503 và model-unavailable. Đã chạy trên incident SLO thật của B, 4 immutable revisions, API/DB restart và browser outage/reconnect | [API/UI](runbooks/INCIDENT_API.md), [runtime B](runbooks/LOCAL_TESTBED.md); không coi ranking là root đã xác nhận |
| B2 integration / CI | Có explicit paired H1 wiring qua registry sẵn có; mặc định không model, đổi release của consumer hiện hữu cần migration A. Có immutable local image/config locks và host release v2: wheel/config/CLI/dependency copy, checksum guard, user systemd start/stop/restart. Đã kiểm API/auth/unavailable, SIGKILL/READY đúng PID và checkpoint PostgreSQL thật; backlog vẫn UNKNOWN. CI phân scope, browser/JS/host-process checks và always-run summary. Có operator Prometheus rules cho worker telemetry: heartbeat/readiness/absence/errors, source eligibility có guard decision, backlog và consumer progress; validate bằng promtool đúng image đã pin, đã quan sát Prometheus thật load 10 rules và alert staleness chuyển firing khi worker dừng. Có Grafana console read-only provisioned trên loopback 13000 (dashboard 11 panel), validate panel query theo metric thật + guard decision + absent-không-thành-healthy, đã xác minh Grafana thật load dashboard và mọi expression evaluate được trên Prometheus thật. Rules/dashboard chỉ là worker telemetry, không phải business/SLO health, model quality hay quyền action; thiếu model **không** alert. Grafana anonymous Viewer là sandbox, **không phải production auth**. Auth hiện local; production auth chưa triển khai | [Host processes](runbooks/HOST_PROCESSES.md), [observability rules](runbooks/OBSERVABILITY.md), [runtime/lock scope](runbooks/LOCAL_TESTBED.md), [workstream contract](delivery/WORKSTREAM_CONTRACT.md) |
| B3 guarded executor | Có internal executor, scoped Kubernetes/RBAC, known-good verifier, PostgreSQL action journal, resume-only worker và SELECT-only API/UI. Có CLI thu hồ sơ image trước/sau + current raw health để operator review, không tự phê duyệt hay xác nhận physical source binding. Có operator review hai bước để gỡ quarantine bằng receipt riêng, đúng rollout/healthy buckets; không đổi `escalated`, IER, cooldown hay attempt budget. Đã kiểm guard/browser fixtures, PostgreSQL outage/concurrency/API thật và Kubernetes API/RBAC thật trên workload fixture; chưa nghiệm thu recovery sản phẩm | [Controlled remediation](runbooks/CONTROLLED_REMEDIATION.md); H4 provider thật vẫn không cấp action permission; chưa có approved real known-good release, producer tự tạo/dispatch action hay IER action integration; unsupported quarantines vẫn giữ khóa |
| Explanation / H2 | Có backend A với deterministic fallback và bounded provider adapter; B có authenticated GET/wrapper riêng và UI pin revision, provider/fallback, refs và error states. Đã mở trên IER SLO thật revision 4 trong DB B, 12/13 evidence claims, refs hợp lệ và IER không đổi. Browser fixture chỉ chứng minh phần mềm. Không tự bật Bedrock, ghi EXPLAINED hay cấp action; G5 chưa nghiệm thu | [Backend](runbooks/EXPLANATIONS.md), [API/UI](runbooks/INCIDENT_API.md) |
| Action activation / AWS | Auto action chưa bật, AWS **chưa triển khai**; G6–G8 vẫn mở. Đã có IaC baseline mô tả network/EKS/RDS/S3/IAM/observability một region: private by default, encrypted, retention có giới hạn, role tách theo workload không admin, controller không có quyền mutate AWS, Bedrock invoke mặc định không được cấp. Validate thật bằng `terraform validate`/`fmt` + 13 guard; tests chứng minh guard bắt được vi phạm. **Chưa apply, chưa gọi AWS API nào, không có credential**; region/quota/budget/model access/quyền apply do owner xác nhận riêng | [AWS baseline](runbooks/AWS_BASELINE.md), [plan hai người](delivery/DELIVERY_PLAN.md), [gates](architecture/CONSTRAINTS_AND_GATES.md) |

[ADR-014](adr/ADR-014-proposed-local-training-evidence.md) và
[contract v2](../configs/training_data_contract_v2.json) quy định prospective local
HTTP fitting, pinned RCAEval evaluation-only; không promote v1/GAMMA/pilot cũ.
Source authorization không đồng nghĩa dataset/model được accepted.

[CI](https://github.com/VietSory/SentinelOps/actions/workflows/ci.yml) cần đối chiếu
đúng SHA. Liveness, DB/model readiness, corpus admission và quality gates là các
trạng thái khác nhau. Raw/model artifacts nằm trên môi trường tương ứng, ngoài Git.

Auth local B2 có credential operator chỉ đọc riêng với producer nhập/sửa IER;
UI dùng file private `operator-token`, producer cũ giữ scope tương thích. Operator
không được ghi revision; credential không cấp model/action authority. Host release
mới truyền hai credential vào API, không truyền cho workers; bundle v2 cũ giữ
cấu hình producer-only. Production identity vẫn fail closed. Xem
[API scopes](runbooks/INCIDENT_API.md) và [host credentials](runbooks/HOST_PROCESSES.md).

Có [demo guide local](runbooks/DEMO_LOCAL.md) cho đường chạy B hiện tại: stack 9
container, API/worker, ba loại readiness khác nhau, ranh giới operator/producer,
UI pin revision, explanation fallback thật và Grafana console. Guide ghi rõ phần
**không** demo được (model, RCA accuracy, Bedrock, auto rollback, AWS) và một
bước đang bị chặn kèm lý do, không che.

**Lưu ý nguồn:** health worker lấy source identity từ hash **toàn bộ** running
Prometheus config, nên thêm `rule_files` cho alert rules đã fork health stream dù
`scrape_configs`/`global` không đổi (`44c63e2c…` vs `06dce0d4…`). Stream cũ
`0b6f3101…` và checkpoint còn nguyên, stream mới đang chạy; hệ quả là có hai
consumer nên `check_lane_b_live` từ chối chạy. Theo stream mới bằng consumer cũ
cần migration protocol của A; `telemetry/prometheus.py` thuộc A nên B không sửa.

## Việc tiếp theo

- **A:** admission/capture → models/evaluation → RCA/explanation → source parity.
- **B:** UI/API local → packaging/auth/monitoring → controlled rollback → AWS.

Theo [ownership/interfaces](delivery/WORKSTREAM_CONTRACT.md). Cập nhật trang này
khi capability/gap thay đổi; kiểm tra thông thường ghi ở commit/CI, không thêm
nhật ký PASS hoặc report riêng cho mỗi lane.
