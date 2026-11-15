# Báo cáo lane B theo kế hoạch giao hàng

Snapshot ban đầu: **14/11/2026**, baseline **`a255892` trên main**, đã gồm PR #48 và #50.
Quan sát runtime của snapshot: **15:35:02 UTC / 22:35:02 giờ Việt Nam** cùng ngày.
**Follow-up 15/11/2026 (giờ Việt Nam):** cập nhật packaging/ECR/CI ở mục 3, 5–6
và việc còn lại ở mục 8; mục 7 giữ quan sát reboot gốc cùng kết quả launcher bổ sung.
Bằng chứng runtime cũ không được chuyển thành xác nhận runtime đang hoạt động.

Báo cáo đối chiếu [DELIVERY_PLAN](../delivery/DELIVERY_PLAN.md),
[WORKSTREAM_CONTRACT](../delivery/WORKSTREAM_CONTRACT.md) và
[checkpoint B](../delivery/LANE_B_CHECKPOINT.md). B là vai trò **Application / Platform / UI**,
không suy ra từ Git author. Model training, thiết kế RCA và backend explanation thuộc A;
B sử dụng, tích hợp và review các interface liên quan.

## 1. Kết quả theo B1–B4

| Mốc kế hoạch | B đã giao | Mức hoàn thành và giới hạn |
| --- | --- | --- |
| **B1 — App local** | Sandbox B riêng; API/UI đọc incident thật, revision, evidence, timeline và trạng thái thiếu dữ liệu | Đã triển khai và kiểm chứng bằng workload/DB/browser local. Deployment hiện tại cần resume các role host sau reboot; API chưa phục vụ tại thời điểm chốt |
| **B2 — Product + packaging** | UI/H2/action observations, local auth, OIDC adapter, immutable host releases, image/config lock, dashboards và CI theo tác động; tích hợp model A bàn giao | Phần mềm và nhiều đường local đã kiểm chứng. Identity provider thật và deployment đích chưa kiểm chứng |
| **B3 — Closed loop + cloud foundation** | Guarded executor, known-good review, journal, Kubernetes/RBAC, verify/escalation, review Q-10; IaC baseline | Có kiểm chứng phần mềm và native trials lịch sử. **G6 chưa nghiệm thu**; release mới cần bộ Q-09 riêng |
| **B4 — AWS + release** | IaC/ECR và business/API manifests offline; runbooks deployment/rollback/demo | **Chưa triển khai AWS**, chưa có H3 từ AWS. Chưa hoàn tất B4 |

Không dùng phần trăm hoàn thành: có code, fixture PASS, runtime local PASS và gate
được nghiệm thu là các mức khác nhau. Kết quả runtime dưới đây thuộc từng lần đo;
không phải xác nhận tất cả dịch vụ đang chạy tại thời điểm đọc báo cáo.

## 2. B1 — Incident thật lên API/UI local

| Mục kế hoạch | Phần đã làm | Bằng chứng / giới hạn |
| --- | --- | --- |
| **B1.1 Sandbox riêng** | Compose project `sentinelops-b`, 9 container, port loopback riêng, commerce/control DB và volumes tách biệt; guard ownership trước lifecycle | Đã chạy local. API 18000, control DB 15433; không xóa volume/checkpoint A |
| **B1.2 UI và kết nối** | UI same-origin `/ui/`; token trong memory của tab; login/logout, loading, invalid token, network/store unavailable | Browser/API checks; không nhúng token vào bundle, URL hoặc localStorage |
| **B1.3 List/detail** | List/filter/cursor; đọc exact IER, detail và evidence; hiển thị/copy source references an toàn | Đọc dữ liệu API/DB thật; không hardcode incident, score hay tổng số incident |
| **B1.4 History/uncertainty** | Pin revision, timeline pagination, chống response cũ ghi đè selection; tách model/store readiness; giữ UNKNOWN/missing/abstention | Có fixture tests và native UI. Không suy diễn nguyên nhân đã xác nhận từ ranking |
| **B1.5 Đường chạy thật** | Dùng worker A đã có, nối health → correlation/RCA → IER → PostgreSQL → API → browser; giữ incident qua API restart | IER native `de38fc6b` revision 4: **19 evidence, 12 deterministic claims**, desktop/mobile; refs thuộc revision đang chọn |

Nguồn: [API/UI](../runbooks/INCIDENT_API.md), [local testbed](../runbooks/LOCAL_TESTBED.md),
[báo cáo tích hợp model và local follow-up](LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md).
Các module health/correlation/RCA/persistence đã có được tái sử dụng; B không viết lại pipeline của A.

## 3. B2 — Product, auth, packaging và vận hành

| Hạng mục | Phần đã làm | Mức bằng chứng / phần chưa xong |
| --- | --- | --- |
| H2 explanation | Authenticated GET/wrapper riêng; UI pin revision, provider/fallback, evidence navigation và error states | Native IER có **12 claims**, refs hợp lệ, IER không đổi. Backend của A; deterministic fallback không phải Bedrock/G5 |
| Action observations | API/UI chỉ đọc action journal, action revision/ETag, quarantine receipt và unavailable states | Software/HTTP checks; không có public dispatch hay nút execute cấp quyền ngầm |
| Local auth | Operator chỉ đọc tách producer nhập/sửa IER; 401/403/503; credentials private, không truyền token API vào workers | Đã kiểm trên PostgreSQL/API/systemd local; operator không được ghi revision |
| OIDC/JWKS | Adapter JWT, scope mapping và fail-closed ngoài local/test; static token chỉ local/test | Code + HTTP fixtures. **Chưa cấu hình provider/app client thật** |
| UI session | Protected readiness khi connect/refresh, heartbeat 30 giây khi tab visible, visibility-change check; denial xóa private state, provider outage giữ error/UNKNOWN | PR #45; **32 browser checks**, không page error. Không nghiệm thu provider cloud |
| Immutable host releases | Wheel/dependency snapshot, checksums, pinned interpreter/session/source; systemd start/stop/restart và crash recovery | Có kiểm chứng process/DB thật. Host-bound, không phải portable image; unit không tự enable lúc boot |
| Inactive-unit recovery | Sửa lỗi `reset-failed` trả `Unit not loaded`: inactive start/restart sau integrity/source checks; failed/active giữ rate-limit reset | PR #50; hai inactive regression cases FAIL trước sửa; **65 focused checks PASS** sau tích hợp main; CI head `2831c416` 14/14 PASS |
| Image/config lock | Pin image/config bằng deployment lock, giữ explicit model/source selection | Có đường local đã verify; lock không tự thành known-good approval hoặc source parity |
| Portable API image / manifests | Wheel/API/UI/ML dependencies và worker CLI, production mặc định không model/credential; renderer digest/OIDC/DB reference, config checksum để rollout | Hai ca HTTP container cô lập PASS. PR #52: **MERGED** vào `8ed9ff6`; CI head mới `333517f`: **14/14 PASS, 0 skipped jobs**; chi tiết mục 5–6 |
| Optional action-reader binding | Commit `9b5f71b` thêm Secret reference SELECT-only riêng, chặn dùng cùng Secret với control runtime; giữ checks quyền sẵn có | PR #53 **MERGED** `6590a8c`; head `f1d4aff`, CI `43620994259` **14/14 PASS**. **87 focused tests PASS**, **5 manifests valid**, 22 PostgreSQL software checks; không thêm trial Q-09/G6, provision credential/migration hoặc cấp quyền action |
| Prometheus/Grafana | **10 operator rules**, dashboard read-only **11 panel**; heartbeat/readiness/errors/eligibility/backlog/progress | Đã quan sát Prometheus load rules/alert firing và Grafana evaluate expressions thật. Đây là worker telemetry, không phải business SLO hay model quality |
| CI theo tác động | Workflow luôn có conclusion; foundation/summary, UI scope, native/public suites theo code/config; unknown path chạy đủ | Không coi job skipped là native PASS. Job ML có mandatory imports và chạy riêng `tests/test_alibaba*.py` |
| Demo/runbooks | Hướng dẫn stack, API/UI, credentials, readiness, history, fallback, Grafana và rollback | [Demo local](../runbooks/DEMO_LOCAL.md) ghi rõ giới hạn; không thay nghiệm thu G4–G8 |

Code chính: [UI assets](../../src/sentinelops/ui/assets/), [OIDC](../../src/sentinelops/oidc.py),
[lane_b.py](../../scripts/lane_b.py), [host manager](../../scripts/lane_b_host.py),
[host runtime](../../scripts/host_runtime.py), [CI dispatch](../../scripts/ci_impact.py).
Runbooks: [host processes](../runbooks/HOST_PROCESSES.md), [observability](../runbooks/OBSERVABILITY.md),
[explanations](../runbooks/EXPLANATIONS.md).

### Tích hợp H1/model do A bàn giao

- Bundle hiện hành: **`de38fc6b`**, feature `http-relative-5m/v2`, source `prometheus-increase-60s-5s/v2`; owner duyệt **có điều kiện D-27**.
- Kiểm **6/6 checksum** của gói; source parity trên B có **137 checks P1–P6, 0 failure** ở lần đo được chọn. Các lần FAIL trước vẫn được giữ; không nâng tolerance để loại lỗi.
- Build serving release `092290ce…`, host release `5c520ae6…`, epoch `alibaba-de38fc6b-20261114b`; giữ store/release/epoch cũ cho rollback.
- API và incident-worker dùng cùng model-store/release; health phải READY đúng stream và COMMITTED trước incidents.
- Live-check đã PASS với `model_dependency=true`; tái tính **3 model signals** từ bundle và accepted history; IER có inference thật.
- `/v1/detect` và `/v1/risk` trả **409 `model_requires_stream_history`** là thiết kế của bundle này; không thay bằng demo score. Kết quả đọc ở `service_signals` trong IER.

Đây là kết quả **integration/source parity local**, không phải B huấn luyện model,
không tự nghiệm thu đầy đủ G3 hoặc Q-04…Q-07. Chi tiết input/hash/source commits,
failed attempts và limitations ở [báo cáo H1 trên B](LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md).

## 4. B3 — Controlled remediation và kiểm chứng local

- Có executor `payment-rollback-known-good/v1`, policy/dry-run/approval, idempotency,
  lock, attempt budget/cooldown, PostgreSQL journal, verify bằng telemetry và escalation.
- Có Kubernetes adapter/RBAC scoped, CLI review image/health trước và sau thay đổi,
  quarantine và operator receipt riêng. Không tự tạo known-good approval, không dùng explanation làm authority.
- B đã review Q-10: **111 tests**, **22 PostgreSQL checks**; bổ sung guard/crash/concurrency/HTTP-adapter checks.
  Đây là software/DB checks, không cộng vào số trial rollback sản phẩm.
- Native trials lịch sử với **`badf9da0`**: **9/10 recovered**, **1/1 failed-verify escalated**,
  **0 unsafe patches trên 10 lần mutate**. Trial bị block vẫn giữ trong denominator.
  **G6 chưa được owner nghiệm thu**.
- Với **`de38fc6b`**, native S2/H4 đã PASS: Payment score **0,797891**, coverage **0,80**,
  change bound và dispatch authority hợp lệ trong ca được kiểm; **chưa dispatch rollback trong ca đó**.
  Bộ Q-09 riêng cho model mới còn chờ quyết định operator/known-good cụ thể;
  không chuyển kết quả 9/10 của model cũ sang model mới.

Nguồn: [controlled remediation](../runbooks/CONTROLLED_REMEDIATION.md),
[G6 lịch sử/Q-10](G6_LOCAL_ALIBABA_BADF9DA0.md),
[native S2/H4 với model mới](LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md).

### Hỗ trợ A: sửa và review S4 trong PR #48

Patch A được giữ tác giả; B bổ sung runner failure tests, source attribution và native review:

- Mỗi pressure stage phải acquire lock ít nhất một lần; nếu không run failed với `S4LockNotAcquired`.
- Chỉ gán `rds/db-pressure` sau khi run thành công; run failed không mang nhãn S4 và bị dataset admission loại.
- Sampler loại waiter của injector qua `application_name`; full runner tests kiểm cả stage đầu thất bại và stage sau thất bại dù stage trước đã acquire.
- Native PostgreSQL review ghi **0** waiter khi chỉ injector chờ và **1** khi có waiter độc lập; mutation check làm hai runner tests thất bại khi bỏ guard.
- Capture sạch `3c36e2e`: degrading **239/239** acquire, fault **187/187**; **2352** lock snapshots, **239** có waiter ngoài injector.
- Hồ sơ PR #48 ghi **88 focused tests / 1630 full tests PASS**, CI cuối head `f8da154` **14/14 PASS**.
  Capture là một run development; các lần lỗi/ngắt trước không dùng làm bằng chứng RCA/Q-08.
- RCA ca mới vẫn **abstain**, RDS đứng thứ ba; **chưa đạt Q-08**, chưa đo independent IF alert-emission MTTD.
  Hành vi global `except BaseException` chuyển external cancellation thành run failed vẫn giữ nguyên, được ghi nhận non-blocking.

B không thay weights/thresholds RCA để làm run này pass. Native lock-wait evidence
đã được collector giữ lại nhưng chưa là live RCA scoring input; tích hợp tiếp cần
A/B review evidence contract và protocol evaluation riêng. Xem [báo cáo S4](LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md).

## 5. B4 — Những gì có và chưa có cho AWS/release

| Hạng mục | Trạng thái |
| --- | --- |
| IaC network/EKS/control PostgreSQL/S3/IAM/observability | Có baseline; Terraform validate/fmt và **14 guards** đã kiểm. **Chưa apply**, không chứng minh môi trường AWS đang tồn tại |
| Business manifests | Renderer **9 resources** cho ba service: digest pin, Secret reference, OTLP wiring, probes, pod hardening. Kubeconform strict **Kubernetes 1.34: 9 valid/0 invalid/0 errors/0 skips**; image kiểm là fixture, chưa kiểm registry/deployment đích |
| ECR | B đã review/sửa lifecycle và repository guard, thêm 8 negative cases. [PR #49](https://github.com/VietSory/SentinelOps/pull/49) **MERGED** vào `0364251`; head `fd7a010`, CI `43613141372` attempt 2 **14/14 PASS**. Node image-pull IAM giữ baseline; chỉ IaC offline, chưa tạo registry AWS |
| API deployment | PR #52 có installed image và **5 resources** offline. API/business config changes nay đổi pod template; **123 focused tests PASS**, kubeconform strict 1.34: **14 valid, 0 invalid/errors/skips** trên fixture references. Trạng thái PR/CI mới ở mục 6; chưa publish image/deploy |
| Workers/observability deployment bindings | Chưa triển khai cloud workers: cần remote adapter/source policy và H3, giữ loopback/ownership guards. Observability AWS phải theo CloudWatch/X-Ray baseline; stack local không thay deployment bindings |
| CloudWatch/KMS prerequisite | Bổ sung regional Logs key policy và caller permissions giới hạn đúng log-group ARN; reader không thành exporter. 53 focused Python tests và fmt PASS; PR #54 head `7ac348d`, run `43667473696`: IaC validate/14 guard và 2 AWS/TLS mock tests PASS. Full CI head cuối đối chiếu tại PR. Chưa triển khai collector hoặc AWS |
| Provider identity / EKS remediation adapter-policy | Provider/app client thật và adapter/policy EKS chưa kiểm trên deployment; policy local không tự cấp quyền EKS |
| H3, release AWS, chi phí/teardown | Chưa có AWS source capture/parity; region/account/quota/budget/quyền apply cần được chốt. **B4 chưa hoàn tất** |

Nguồn: [AWS baseline và business manifests](../runbooks/AWS_BASELINE.md),
[local Kubernetes](../runbooks/KUBERNETES_LOCAL.md),
[renderer](../../scripts/render_business_manifests.py), [infra/aws](../../infra/aws/).
Không có AWS provisioning, AWS API call, Bedrock activation hoặc automatic remediation trong các kết quả được báo cáo này nghiệm thu.

## 6. Các PR/CI gần nhất đã đối chiếu

Đây là các mốc kiểm chứng chọn lọc, không phải toàn bộ commit history. CI gắn với
**PR head**, không tự xác nhận một commit mới hơn. Các mốc auth/rules/IaC/dashboard/demo
trước PR #44 nằm trong [checkpoint B](../delivery/LANE_B_CHECKPOINT.md).

| PR | Nội dung | Head đã kiểm | Trạng thái khi chốt | CI |
| --- | --- | --- | --- | --- |
| [#44](https://github.com/VietSory/SentinelOps/pull/44) | Review safety/Q-10 và CI Alibaba | `a917835` | MERGED | 14 PASS, 0 skipped jobs |
| [#45](https://github.com/VietSory/SentinelOps/pull/45) | UI session/protected readiness | `3782984` | MERGED | **4 PASS, 9 skipped jobs ngoài scope** |
| [#46](https://github.com/VietSory/SentinelOps/pull/46) | Offline business manifests | `9b5b9c0` | MERGED | 14 PASS, 0 skipped jobs |
| [#47](https://github.com/VietSory/SentinelOps/pull/47) | Parity/release/live integration `de38fc6b` | `917bc77` | MERGED | 14 PASS, 0 skipped jobs |
| [#48](https://github.com/VietSory/SentinelOps/pull/48) | S4 v2 fail-closed, tests, capture review | `f8da154` | MERGED | 14 PASS, 0 skipped jobs |
| [#50](https://github.com/VietSory/SentinelOps/pull/50) | Recover inactive systemd units; tích hợp main | `2831c416` | MERGED vào `a255892` | [14 PASS, 0 skipped jobs](https://github.com/VietSory/SentinelOps/actions/runs/43607840174) |
| [#49](https://github.com/VietSory/SentinelOps/pull/49) | B review ECR/lifecycle guards | `fd7a010` | MERGED vào `0364251` | [Attempt 2: 14 PASS](https://github.com/VietSory/SentinelOps/actions/runs/43613141372); attempt 1 observability timeout |
| [#52](https://github.com/VietSory/SentinelOps/pull/52) | Installed API image, offline manifests và rollout cấu hình | `333517f` | **MERGED** vào `8ed9ff6` | [Head `a77332c`: 14 PASS](https://github.com/VietSory/SentinelOps/actions/runs/43616362464); [head `333517f`](https://github.com/VietSory/SentinelOps/actions/runs/43618472435): **14/14 PASS, 0 skipped jobs** |
| [#53](https://github.com/VietSory/SentinelOps/pull/53) | Optional SELECT-only action-reader binding | `f1d4aff` | **MERGED** vào `6590a8c` | [Run `43620994259`](https://github.com/VietSory/SentinelOps/actions/runs/43620994259): **14/14 PASS, 0 skipped jobs** |

CI sau merge #53 [run `43665706369`](https://github.com/VietSory/SentinelOps/actions/runs/43665706369)
đã **14/14 PASS, 0 skipped jobs** trên đúng SHA
`6590a8cb0de5e704a630614032cf6e1a2791bf81`. Đối chiếu GitHub ngày 15/11;
không dùng trạng thái đang chạy từ lần bàn giao trước.

PR #52 đã sửa vị trí output image proof ra ngoài runtime root chưa registration;
run `43616362464` xác nhận image smoke, host recovery và cleanup PASS. Image source
revision `7765701bb88a1f38d1c09d2b65032257023c345d` là checkout merge của CI,
khác PR head `a77332c`. Head rollout `333517f` cũng đã có run `43618472435` **14/14 PASS**;
image của run này là `sha256:291474a7101bf4ec238563c9a4695aadc4152b1a7aaa4cc5092a72ea798704cc`,
source revision checkout merge `e867192703c4a753ac162aaaa247b274093a0623`.
Foundation: **1497 passed, 37 skipped**, installed wheel smoke PASS.
Đây là kết quả runner, không phải registry digest hoặc host runtime đang hoạt động.

Số test ở từng mốc là các suite/phạm vi khác nhau, **không cộng thành tổng test dự án**.
Với PR #50, full local suite chưa hoàn tất: có lượt bị reboot/ngắt, và sandbox
bị kẹt trong asyncio; outside-sandbox approval bridge không khả dụng. Không gọi
các lượt đó là PASS. CI đúng head đã thành công, gồm foundation, ML-enabled
Alibaba, native worker/source/DB outages, replay, UI, Kubernetes và host recovery.
Không có skipped job không đồng nghĩa mọi test trong từng dependency profile đều không skip.

## 7. Trạng thái vận hành tại thời điểm chốt

- Máy đã reboot lúc **21:27:28 giờ Việt Nam**. Các unit B mặc định không tự enable khi boot.
- Lần quan sát 22:35:02: **9 container B đang Up**; 7 hiển thị healthy, Collector/Jaeger không hiển thị Docker healthcheck.
- `curl -fsS http://127.0.0.1:18000/ready` trả lỗi kết nối, exit **7**: **API chưa phục vụ**.
  Không khẳng định UI/model/workers đang hoạt động từ các báo cáo PASS cũ.
- Trước reboot, guarded restart đã khôi phục cả API/health/incidents; health có READY/COMMITTED
  đúng stream, incident-worker có PROGRESS đúng consumer. Sau reboot, lệnh guarded
  restart API bị approval bridge trả `Denied, expired, cancelled, or unavailable`;
  lượt này chưa hoàn tất resume các role host.
- PostgreSQL READ ONLY đã kiểm sau resume/reboot: **20/20 revision cũ còn nguyên**;
  source identity, initial bucket và consumer IDs không đổi. Health checkpoint đã
  tiến từ **09:27Z → 14:25Z**, incident checkpoint **09:27Z → 14:23Z**.
  Đây là tiến triển **trước reboot**, không phải xác nhận caught-up hiện tại.
- Bằng chứng local: `artifacts/lane-b/runtime-resume-20261114-01/db-history-verification.json`.
  Raw/model artifacts nằm ngoài Git, path trên máy B không phải portable handoff;
  người nhận phải có quyền materialize/verify theo runbook.

**Bổ sung trong follow-up 15/11:** launcher quan sát cả API/health/incidents
`inactive`, cùng release `5c520ae661e440c892195c814670677f`; health stream vẫn là
`b95058b4d9eaef6e411bc493263068963668de2ced39f89351c3efd3588a4a8f`.
Guarded restart API bị approval bridge trả
`Denied, expired, cancelled, or unavailable`. Chưa resume được host; đây không
phải lỗi code đã chứng minh và CI runner không chứng minh host này đang chạy.

## 8. Việc còn lại và thứ tự tiếp theo

| Ưu tiên | Việc cần làm | Owner / điều kiện kết thúc |
| --- | --- | --- |
| **1 — Vận hành local** | Resume API/health/incidents bằng đúng pinned session/release; kiểm READY đúng stream và COMMITTED trước incidents; kiểm protected API, checkpoint freshness và UI | B/operator; cần service-manager execution hoạt động. Giữ volumes, epoch, model selection và lịch sử; missing telemetry vẫn UNKNOWN |
| **2 — Q-09/G6 model mới** | Chốt protocol/trial và operator duyệt known-good cụ thể trước mỗi thay đổi xấu; chạy, verify và ghi audit riêng cho `de38fc6b` | B/operator, A cung cấp H4 đủ điều kiện; owner nghiệm thu G6. Không kế thừa 9/10 của `badf9da0` |
| **3 — RCA/S4 và timing** | Review cách đưa native lock-wait evidence vào scoring, freeze interface/evaluation trước run mới; đo independent live IF emission MTTD | A chủ trì RCA/evaluation, B hỗ trợ source/platform. Giữ abstention và ngưỡng hiện hành; run S4 đã xem không thành holdout |
| **4 — Packaging đích** | PR #52, ECR #49 và action-reader #53 đã merge; sửa prerequisite CloudWatch/KMS, tiếp tục collector/exporter bindings và external commerce prerequisites | B; worker adapter/source policy cần A/B review trước H3. Configuration-only không phải deployment đã verify |
| **5 — Cloud/H3** | Chốt region/account/quota/budget/quyền; deploy trong phạm vi được cấp, cấu hình provider thật, thu H3 và giao A verify parity | Owner/B/A theo boundary. AWS vẫn chưa được triển khai trong phạm vi báo cáo |
| **6 — Nghiệm thu** | Hoàn tất model/RCA/explanation/action/final evaluation theo các gate còn mở | A/B/owner theo scope; không mở sealed holdout hoặc tự đổi gate thành PASS |

**Đầu ra B đã có:** UI/API local trên incident thật, auth adapters, packaging/lifecycle,
observability, guarded action software, kiểm chứng native có giới hạn và nền tảng
deployment offline. **Chưa hoàn tất:** runtime sau reboot, G6 cho release mới,
provider/deployment AWS/H3 và các quality/final gates liên quan.
