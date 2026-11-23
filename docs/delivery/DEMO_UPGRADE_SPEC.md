# Đặc tả thực thi — nâng cấp demo SentinelOps

Tài liệu cho agent triển khai. Lý do và kịch bản trình chiếu nằm ở
[DEMO_UPGRADE_PLAN.md](DEMO_UPGRADE_PLAN.md); file này quy định **làm gì, ở đâu,
và thế nào là xong**. Viết trên `main` tại `aa11c6d` (2026-11-24).

## 0. Nguyên tắc: test là hợp đồng

- Một work package (WP) xong khi và chỉ khi qua đủ các cổng ở mục 1.4. Không có
  tiêu chí ngầm nào khác.
- Test nghiệm thu được viết và **đóng băng trước** khi triển khai. Agent triển khai
  không được sửa test đã đóng băng, fixture của nó, ngưỡng, hay checker.
- Ba vai, có thể do cùng một agent đảm nhiệm lần lượt nhưng phải ghi rõ:
  - **Người đặc tả**: viết và đóng băng test.
  - **Người triển khai**: viết code cho test pass.
  - **Người review**: lượt 1 tự review diff thật; lượt 2 review độc lập (agent khác
    hoặc lượt thứ hai tách biệt, ghi rõ là self-review nếu không có người khác).

## 1. Quy trình bắt buộc

### 1.1 Chuẩn bị (agent mới vào việc)

1. Đọc `AGENTS.md` của checkout, file này, `DEMO_UPGRADE_PLAN.md`, và các file
   trong mục "Phạm vi file" của WP được giao.
2. Làm trong worktree riêng, không làm trong checkout chính vì có thể chứa thay đổi
   chưa commit của người khác:
   `git worktree add -b demo-upgrade/wpN ~/.cache/sentinelops-wpN origin/main`.
3. Chạy baseline và ghi lại số test pass:
   `.venv-ml/bin/python -m pytest -q` và `node --test tests/ui/*.test.mjs`.
4. Danh tính git và quy tắc commit/push theo `AGENTS.md` mục 5. Không thêm dòng
   Co-Authored-By.

### 1.2 Đóng băng test (người đặc tả, trước mỗi WP)

1. Viết test nghiệm thu của WP đúng theo mục 3:
   - Python: `tests/test_demo_upgrade_wpN_<chủ đề>.py`.
   - UI: `tests/ui/demo_upgrade_wpN.test.mjs`. CI đã chạy glob `tests/ui/*.test.mjs`
     bằng Node 24, nên test UI import trực tiếp được module `.ts` không có import.
2. Chạy test: mọi test mới phải **fail vì thiếu tính năng**. Lỗi cú pháp hay lỗi
   fixture không tính. Lưu output.
3. Review lượt 2 nội dung test, rồi commit riêng `test(demo): freeze WPn acceptance`.
   Ghi SHA (gọi là F_n) vào bảng mục 6.
4. Commit đóng băng chỉ nằm trên nhánh local của WP. Không push lên `main` trước khi
   WP xong, để CI của `main` luôn xanh.

### 1.3 Triển khai (người triển khai)

- Chỉ sửa file trong "Phạm vi file" của WP. Cần sửa file khác thì dừng và hỏi.
- **Không được đổi**:
  - ngưỡng model và release model;
  - trọng số và ngưỡng RCA (`configs/rca_policy.json`);
  - `change_window_seconds` (600), `freshness_seconds` (900), quy tắc xác minh phục
    hồi, kill switch, giới hạn một lần dispatch;
  - guard IaC, trừ phần WP7 cho phép;
  - test hiện có, trừ đúng danh sách ở mục 4.
- Nếu một test đóng băng có vẻ sai: dừng, ghi bằng chứng (test, input, kỳ vọng, lý
  do), báo owner. Không tự sửa test.

### 1.4 Cổng hoàn thành của mỗi WP (phải đạt tất cả)

1. Test đóng băng không đổi:
   `git diff --exit-code F_n -- tests/test_demo_upgrade_* tests/ui/demo_upgrade_* scripts/check_demo_round.py scripts/check_risk_quiet.py`
2. Test nghiệm thu của WP: pass, không có skip hay xfail.
3. Regression đầy đủ pass. Số test không giảm so với baseline, trừ các test được
   thay theo mục 4.
4. `.venv-ml/bin/python scripts/validate_contracts.py` OK.
5. Review lượt 1 và lượt 2 theo checklist mục 5, kết quả ghi trong commit message
   hoặc ghi chú của WP.
6. Push fast-forward lên `main`; CI của đúng SHA đó xanh.

### 1.5 Nghiệm thu live (sau khi WP1–WP7 lên `main`)

Mọi thao tác ghi lên AWS phải qua hai lượt review trước khi chạy.

- **L2, chạy thử** trên AWS dev mới dựng:
  1. Hệ thống khỏe 60 phút, rồi `scripts/check_risk_quiet.py` phải PASS.
  2. Chạy 1 lượt bằng image "chậm dần", rồi `scripts/check_demo_round.py` cho
     receipt đó.
  3. Nếu lượt chạy chỉ trượt ở ngưỡng `lead_seconds` hoặc `deploy_to_breach_seconds`
     thì được chỉnh tham số image trong giới hạn mục 7, rồi chạy lại.
- **L3, nghiệm thu**:
  - Một giờ khỏe PASS `check_risk_quiet`.
  - Tiếp theo là 3 lượt liên tiếp từ **một lần chạy runner**, cả 3 receipt PASS
    `check_demo_round.py --require 3`.
  - Owner xác nhận đã nhận đủ 3 loại email.

## 2. Bản đồ work package

| WP | Nội dung | Phụ thuộc | Lane | Ước lượng |
| --- | --- | --- | --- | --- |
| WP0 | Checker nghiệm thu live (`check_demo_round`, `check_risk_quiet`), đóng băng cùng F_1 | — | A | ½ ngày |
| WP1 | Proposal v2: chờ tới khi có quyết định, không đếm ngược | — | A | 1,5–2 ngày |
| WP2 | Bộ chấm rủi ro mỗi phút và API cảnh báo | — | A | 2 ngày |
| WP3 | Notifier gửi email qua SNS | WP1, WP2 | A | 1 ngày |
| WP4 | UI: bỏ đồng hồ, thẻ cảnh báo, dòng thời gian, nhãn điểm | WP1, WP2 | A | 1,5 ngày |
| WP5 | Image chậm dần, nút Fault Console, receipt runner v2 | WP2 | A | 1 ngày |
| WP6 | Dashboard Grafana và allowlist `/ready` | WP2 | A/B | ½ ngày |
| WP7 | SNS, IAM Pod Identity, manifest sidecar và notifier | WP2, WP3 | B | 1 ngày + review AWS |

Tổng khoảng 9–10 ngày làm việc, cộng 2 ngày cho L2/L3. Có thể chạy song song WP1
với WP2, và WP4 với WP5.

## 3. Đặc tả từng WP

Ký hiệu: **R** là hành vi bắt buộc, **A** là test nghiệm thu (Given / When / Then).
Tên test trong ngoặc là tên hàm pytest hoặc tên `test()` của node.

### WP0 — Checker nghiệm thu live

**Phạm vi file:** `scripts/check_demo_round.py`, `scripts/check_risk_quiet.py`,
`tests/test_demo_upgrade_wp0_checkers.py`.

- **R0.1** `check_demo_round.py <receipt.json>... [--require N]` đọc receipt
  `runbook-demo-round/v2` (định nghĩa ở R5.4) và PASS khi mỗi receipt có đủ:
  - `scenario == "gradual-regression"`;
  - `proposal.proposal_version == "runbook-proposal/v2"` và không có `expires_at`;
  - `warning.service == "payment-service"` và có `warning.episode_started_at`;
  - `lead_seconds = first_breach_at − warning.episode_started_at`, và `lead_seconds ≥ 180` (ít nhất 3 phút, theo DEMO_UPGRADE_PLAN mục 4.5);
  - `0 < deploy_to_breach_seconds ≤ 600`, với `deploy_to_breach_seconds = first_breach_at − deploy_started_at`;
  - `rca_top1 == "payment-service"`;
  - `action.phase == "recovered"`, `action.telemetry_verified is true`, `dispatch_count == 1`;
  - `notifications.early_warning ≥ 1`, `notifications.proposal_pending ≥ 1`,
    `notifications.outcome ≥ 1`. Đây là số tăng của metric
    `sentinelops_notifications_sent_total` theo `kind` trong khung thời gian của lượt.

  Với `--require N`: đúng N receipt, cùng `runner_invocation_id`, `round` là 1..N.
  Exit 0 khi PASS. Exit 1 khi FAIL, in từng điều kiện trượt.
- **R0.2** `check_risk_quiet.py --prometheus URL --minutes 60` PASS khi với mỗi
  service trong 3 service:
  - `max_over_time(sentinelops_risk_episode_active{service=S}[60m]) == 0`;
  - `changes(sentinelops_risk_last_scored_unixtime{service=S}[60m]) ≥ 55`;
  - `time() − sentinelops_risk_last_scored_unixtime{service=S} < 180`.
- **A0.1** (`test_round_checker_passes_a_complete_receipt`) Receipt mẫu đủ điều kiện → exit 0.
- **A0.2** (`test_round_checker_names_every_failed_condition`) Mỗi lần làm hỏng một
  điều kiện của R0.1 (lead 179, deploy→breach 601, có `expires_at`, phase escalated,
  thiếu email, sai `round`) → exit 1, kèm đúng tên điều kiện đó.
- **A0.3** (`test_quiet_checker_uses_the_exact_queries`) Với Prometheus giả: đúng
  các query của R0.2 được gọi; một episode trong cửa sổ → FAIL; dữ liệu cũ → FAIL.

### WP1 — Proposal v2: chờ tới khi có quyết định

**Phạm vi file:**
- `src/sentinelops/remediation/approvals.py`, `controller.py`, `authority.py`,
  `pod_memory.py`, `proposal_api.py`;
- test được thay theo mục 4;
- `tests/test_demo_upgrade_wp1_*.py`.

Hành vi bắt buộc:

- **R1.1** Thêm `PROPOSAL_VERSION_V2 = "runbook-proposal/v2"`. Mọi proposal mới,
  cả rollback Payment lẫn OOM, là v2:
  - cùng các trường như v1, trừ `expires_at`;
  - thêm `"approval_policy": "pending-until-decision/v1"`.
- **R1.2** Proposal v1 đã lưu giữ nguyên hành vi cũ: hết hạn theo `expires_at`. Mọi
  bộ đọc nhận được cả v1 và v2.
- **R1.3** `record_approval`: với v2 chỉ cần `created_at ≤ now`, không có giới hạn
  trên. V1 giữ như cũ.
- **R1.4** Mỗi tick, controller kiểm từng proposal v2 đang chờ, theo thứ tự:
  1. Revision hiện tại của incident khác `incident_revision` → ghi outcome
     `{"phase": "obsolete", "reason": "incident-changed"}`.
  2. Image Payment đang chạy khác `current_image` → outcome
     `{"phase": "obsolete", "reason": "deployment-changed"}`.

  Proposal `obsolete` không bao giờ chuyển `escalated` và không bao giờ dispatch.
- **R1.5** Khi có bản duyệt cho v2, controller chạy lại toàn bộ kiểm tra hiện có:
  revision, quyết định runbook, target binding, kill switch, H4. Riêng trường hợp
  lệch revision thì ghi `obsolete` thay cho `escalated`; các lỗi khác giữ như cũ.
- **R1.6** Quy tắc tuổi RCA trong `authority.qualify`:
  - RCA có `as_of` cũ hơn `freshness_seconds` chỉ được chấp nhận khi
    `assessment.lifecycle == "RCA_READY"` **và** checkpoint health mới nhất, đã bắt
    kịp nguồn, cho service xếp hạng 1 của RCA có `availability == "ELIGIBLE"` và
    `incident_active is True`. Khi đó dùng trường mới `Assessment.root_breaching`,
    tính trong `NativeH4Authority.assess` từ `state["service_states"]`.
  - Ngược lại vẫn `Blocked("evidence-stale-or-future")` như cũ.
  - Không đổi các kiểm tra freshness của health mới nhất và của bản quan sát
    Deployment.
- **R1.7** API `/v1/runbook-proposals`:
  - v2 có các trạng thái `pending`, `approved`, `consumed`, `obsolete` (từ outcome),
    `engineer`, cộng các phase của action OOM như hiện nay.
  - v2 không bao giờ ở trạng thái `expired`.
  - Từ chối (reject) ghi outcome `rejected`.
- **R1.8** OOM v2:
  - bỏ mọi kiểm tra `expires_at`;
  - đang chờ mà pod `ready is True` và không còn `fresh_oom` → outcome
    `{"phase": "obsolete", "reason": "pod-recovered"}`;
  - thêm `obsolete` vào `TERMINAL`;
  - với v2, chỉ được renew thủ công khi action `blocked`.

Test nghiệm thu:

- **A1.1** (`test_new_rollback_proposal_is_v2_without_expiry`) Given incident đủ
  điều kiện / When controller tick / Then proposal là v2, không có `expires_at`, có
  `approval_policy`.
- **A1.2** (`test_v2_approval_two_hours_later_dispatches_once`) Given proposal v2 đã
  2 giờ, incident cùng revision ở `RCA_READY`, checkpoint mới với `payment-service`
  `incident_active=True`, Payment vẫn chạy image lỗi / When ghi bản duyệt rồi tick /
  Then đúng 1 lần PATCH tới image known-good, action vào `verifying`.
- **A1.3** (`test_v2_pending_becomes_obsolete_when_incident_recovers`) Given proposal
  v2 đang chờ / When revision incident tăng do tự hồi phục / Then outcome `obsolete`,
  lý do `incident-changed`, không có outcome `escalated`, không PATCH; bản duyệt sau
  đó bị từ chối.
- **A1.4** (`test_v2_pending_becomes_obsolete_after_newer_deploy`) Image đang chạy
  đổi → outcome `obsolete`, lý do `deployment-changed`.
- **A1.5** (`test_stale_rca_blocked_when_root_service_no_longer_breaching`) RCA cũ 2
  giờ, incident vẫn `RCA_READY`, nhưng `payment-service` `incident_active=False` →
  `Blocked("evidence-stale-or-future")`, không PATCH.
- **A1.6** (`test_stale_rca_allowed_only_with_fresh_health_and_deployment`) RCA cũ
  nhưng root vẫn vi phạm, còn health mới nhất cũ hơn 900 giây hoặc bản quan sát
  Deployment cũ → vẫn bị chặn.
- **A1.7** (`test_v2_second_approval_never_dispatches_twice`) Duyệt hai lần → lần thứ
  hai bị từ chối, chỉ 1 dispatch.
- **A1.8** (`test_v1_proposal_still_expires`) Proposal v1 sau 16 phút → `escalated`,
  lý do `approval-expired`, như cũ.
- **A1.9** (`test_api_v2_status_never_expired`) API trả `pending` sau 3 giờ; trả
  `obsolete` khi có outcome; không bao giờ trả `expired` cho v2.
- **A1.10** (`test_record_approval_v2_late_and_early`) Duyệt sau 3 giờ được chấp
  nhận; duyệt trước `created_at` bị từ chối.
- **A1.11** (`test_v2_kill_switch_blocks_at_approval`) Có kill switch lúc duyệt →
  `escalated`, lý do `runbook-disabled-or-kill-switch`, không PATCH (giữ như cũ).
- **A1.12** (`test_oom_v2_late_approval_and_pod_recovered`) OOM v2 duyệt sau 1 giờ
  khi pod vẫn OOM → dispatch; pod đã Ready và hết OOM → `obsolete`, lý do
  `pod-recovered`.

**Ngoài phạm vi:** đổi `freshness_seconds`; tự động làm mới RCA; rollback phòng ngừa.

### WP2 — Bộ chấm rủi ro mỗi phút

**Phạm vi file:**
- mới: `src/sentinelops/workers/risk.py`, `scripts/run_risk_worker.py`;
- sửa: `web/deployment/observability_api.py`;
- test: `tests/test_demo_upgrade_wp2_*.py`, `web/tests/test_observability_api.py` (chỉ thêm).

Hành vi bắt buộc:

- **R2.1** `run_risk_worker.py` nhận: `--source-store` (health store, chỉ đọc),
  `--health-stream`, `--model-store`, `--model-release-ref`, `--scratch-store`,
  `--metrics-otlp-endpoint`, `--poll-seconds` (mặc định 5). DSN đọc từ
  `CONTROL_DATABASE_URL`, cùng quy tắc nguồn với incident worker.
- **R2.2** Mỗi lần poll:
  1. Đọc dòng `sentinel_control.health_batches` mới nhất của stream.
  2. Nếu `bucket_end` mới hơn bucket đã chấm: đọc batch manifest và normalization
     manifest, rồi gọi `collect_model_signals` với
     `ReadThroughStore(read=health_store, write=scratch_store)` và
     `batch_ref = manifest_ref`.
  3. Không bao giờ ghi vào health store; không ghi vào control DB.
- **R2.3** Kết quả mỗi service: `state` (`normal` | `watch` | `degrading` |
  `unavailable`), `risk_score` (hoặc null), `anomaly_score`, `bucket_end`,
  `model_version`. Model không sẵn sàng hoặc feature không tương thích →
  `unavailable`.
- **R2.4** Quy tắc episode:
  - Bắt đầu ở bucket b khi `state(b) == degrading` và bucket liền trước (cách 60
    giây) có `state ∈ {watch, degrading}`; `episode_started_at = bucket_end(b)`.
  - Kết thúc khi 2 bucket liên tiếp `normal`, hoặc 5 bucket liên tiếp `unavailable`.
  - Mỗi service có tối đa một episode đang mở.
  - Khởi động lại thì bắt đầu từ bucket mới nhất, không chấm bù.
- **R2.5** Metric OTLP, `service.name = sentinelops-risk-worker`. Mọi gauge không có
  unit, nên tên trong Prometheus giữ nguyên:
  - `sentinelops_risk_score{service}`
  - `sentinelops_risk_state{service,state}` (one-hot, 4 giá trị)
  - `sentinelops_risk_episode_active{service}`
  - `sentinelops_risk_episode_started_unixtime{service}` (chỉ khi đang có episode)
  - `sentinelops_risk_last_scored_unixtime{service}`
  - `sentinelops_risk_threshold{level="watch"|"degrading"}`, đọc từ release model,
    không ghi cứng.
- **R2.6** Cảnh báo chỉ để thông báo: không mở incident, không tạo proposal, không
  ghi slot nào.
- **R2.7** API trong `observability_api.py`, cần `authorize_operator`:
  - `GET /v1/observability/risk` trả `{"schema_version": "live-risk/v1",
    "generated_at", "thresholds": {"watch", "degrading"}, "services": {S:
    {"state", "score", "episode_active", "episode_started_at", "last_scored_at"}}}`.
  - `GET /v1/observability/risk/episodes?service=&start=&end=` trả danh sách
    `episode_started_at` khác nhau trong khoảng.
  - Thiếu số liệu → null, không tự đoán.

Test nghiệm thu:

- **A2.1** (`test_scorer_never_writes_the_health_store`) Hash toàn bộ file của health
  store trước và sau khi chấm phải bằng nhau.
- **A2.2** (`test_scorer_matches_incident_model_inference`) Cùng batch fixture: điểm
  của bộ chấm bằng đúng output của `collect_model_signals` gọi trực tiếp.
- **A2.3** (`test_episode_rule`) Chuỗi trạng thái → kết quả:
  - `[normal, degrading]` → không có episode;
  - `[watch, degrading]` → episode ở bucket thứ 2;
  - `[degrading, degrading]` → episode ở bucket thứ 2;
  - `[watch, degrading, normal, normal]` → kết thúc sau bucket thứ 4;
  - 5 lần `unavailable` liên tiếp → kết thúc.
- **A2.4** (`test_unavailable_model_never_starts_an_episode`).
- **A2.5** (`test_risk_metrics_exact_names_labels_and_thresholds`) Dùng in-memory
  reader: đúng tên, nhãn, one-hot, và threshold lấy từ registry.
- **A2.6** (`test_scorer_scores_each_bucket_once`) Poll lặp lại không chấm lại bucket cũ.
- **A2.7** (`test_observability_risk_endpoints`) Prometheus giả: đúng schema; thiếu
  số liệu → null; không có token → 401.

### WP3 — Notifier gửi email

**Phạm vi file:**
- mới: `src/sentinelops/notifications/__init__.py`, `sns.py`, `events.py`,
  `scripts/run_notifier.py`;
- test: `tests/test_demo_upgrade_wp3_*.py`.

Hành vi bắt buộc:

- **R3.1** Interface `Notifier.send(Notification) -> str` (trả message id). Ba bản
  cài đặt:
  - `SnsNotifier(topic_arn, client)`: gọi `publish(TopicArn, Subject, Message)`,
    subject tối đa 100 ký tự;
  - `RecordingNotifier`: dùng cho test;
  - `NullNotifier`: khi không cấu hình topic, ghi log `notifications-disabled` một lần.
- **R3.2** Sự kiện và khoá chống trùng:

  | kind | Khoá | Tiêu đề |
  | --- | --- | --- |
  | `early_warning` | `warning:{service}:{episode_started_at}` | `[SentinelOps] Cảnh báo sớm: {service} có nguy cơ vi phạm SLO` |
  | `warning_cleared` | `warning-cleared:{service}:{episode_started_at}` | `[SentinelOps] Hết nguy cơ: {service}` |
  | `proposal_pending` | `proposal:{proposal_id}` | `[SentinelOps] Cần duyệt: {runbook_id} cho {incident_id}` |
  | `outcome` | `outcome:{proposal_id}:{phase}` (`recovered`, `escalated`, `rejected`, `obsolete`) | `[SentinelOps] {kết quả}: {incident_id}` |

- **R3.3** Nội dung email:
  - Có: service, incident id, thời gian theo UTC và +07:00, trạng thái, link `--ui-url`.
  - Email cảnh báo ghi rõ "đây là dự báo, chưa phải sự cố".
  - Không có token, DSN, digest đầy đủ hay nội dung evidence.
- **R3.4** Nguồn dữ liệu:
  - proposal và outcome đọc từ controller store bằng đúng bộ đọc API đang dùng
    (`ProposalStore`), chỉ đọc;
  - episode đọc từ Prometheus (R2.5).
- **R3.5** Gửi ít nhất một lần:
  - publish thành công rồi mới ghi slot `notified` vào store riêng của notifier;
  - publish lỗi thì thử lại ở lần poll sau, tối đa 10 lần, rồi ghi log
    `notification-failed`;
  - notifier không bao giờ chặn controller.
- **R3.6** Metric xuất qua OTLP như R2.5, với `service.name = sentinelops-notifier`
  và tham số `--metrics-otlp-endpoint`. Hai counter tên instrument
  `sentinelops_notifications_sent` và `sentinelops_notifications_failed`, nhãn
  `kind`. Trong Prometheus chúng có tên `sentinelops_notifications_sent_total` và
  `sentinelops_notifications_failed_total`.

Test nghiệm thu:

- **A3.1** (`test_each_event_sends_once_across_restarts`) Tạo lại notifier trên cùng
  store → không gửi trùng.
- **A3.2** (`test_publish_failure_retries_then_succeeds`) Lần 1 lỗi, lần 2 thành
  công → đúng 1 message, counter đúng.
- **A3.3** (`test_message_contents_are_bounded_and_safe`) Tiêu đề ≤ 100 ký tự; có id
  và link; không khớp mẫu token, DSN hay `sha256:` 64 hex.
- **A3.4** (`test_disabled_without_topic`).
- **A3.5** (`test_sns_publish_call`) Dùng botocore `Stubber`: đúng TopicArn, Subject,
  Message.

### WP4 — UI

**Phạm vi file:**
- mới: `web/src/live/demoLogic.ts`, module thuần không import gì;
- sửa: `web/src/live/LiveApp.tsx`, `web/src/live/adapters.ts`,
  `web/src/types/sentinelops.ts`, các view hiển thị điểm;
- console cũ: `src/sentinelops/ui/assets/proposalwatch.js`;
- test: `tests/ui/demo_upgrade_wp4.test.mjs`.

Hành vi bắt buộc:

- **R4.1** Proposal v2 không có đồng hồ:
  - hiện "Chờ duyệt từ {HH:mm}" (VI) hoặc "Waiting since {HH:mm}" (EN);
  - nút Duyệt và Từ chối bật khi `status == pending`, không phụ thuộc thời gian còn lại;
  - v1 giữ đồng hồ cũ.
- **R4.2** `obsolete` hiện là "Đã đóng: tình trạng đã thay đổi" kèm lý do, nằm ở
  phần lịch sử.
- **R4.3** Thẻ "Cảnh báo sớm" trên dashboard: lấy từ `/v1/observability/risk`, hiện
  khi `state ∈ {watch, degrading}`, gồm service, "nguy cơ vi phạm SLO trong khoảng
  5 phút", thời điểm bắt đầu, và thanh điểm có hai ngưỡng. `unavailable` hiện mờ
  "Chưa có dự báo".
- **R4.4** Chi tiết incident:
  - "Đã cảnh báo trước {m} phút {s} giây" khi episode của service xếp hạng 1 bắt
    đầu trước `first_onset_at`;
  - dòng thời gian: cảnh báo → vi phạm → incident → duyệt → hồi phục, kèm khoảng
    thời gian giữa các mốc.
- **R4.5** Nhãn điểm:
  - điểm giả thuyết RCA → "Điểm RCA", hiện dạng 0,00–1,00, không có dấu %;
  - điểm của proposal → "Mức khớp runbook";
  - tooltip giải thích theo DEMO_UPGRADE_PLAN mục 5.
- **R4.6** `demoLogic.ts` export các hàm thuần: `proposalTiming(row, now)`,
  `proposalStatusLabel(status, locale)`, `riskCards(riskBody)`,
  `warningLead(episodes, firstOnsetAt)`, `incidentTimeline(parts)`,
  `scoreLabel(kind, value, locale)`. UI chỉ dùng các hàm này cho logic trên.

Test nghiệm thu, chạy bằng `node --test`:

- **A4.1** `proposalTiming`: v2 không có `expires_at` → `reviewable` khi pending, không
  có `remaining`; v1 vẫn có `remaining`.
- **A4.2** `proposalStatusLabel('obsolete')` cho VI và EN.
- **A4.3** `riskCards`: `degrading` và `watch` thành thẻ; `normal` không thành thẻ;
  `unavailable` thành thẻ mờ; giá trị null giữ null.
- **A4.4** `warningLead`: episode trước onset 4 phút 30 giây → 270 giây; episode sau
  onset hoặc không có episode → null.
- **A4.5** `scoreLabel('rca', 0.82, 'vi')` cho ra `Điểm RCA 0,82`, không có `%`;
  `scoreLabel('runbook', 0.91, 'vi')` cho ra `Mức khớp runbook 0,91`.
- **A4.6** Console cũ: `validate()` chấp nhận dòng v2 không có `expires_at`;
  `timing()` không có countdown cho v2.
- **A4.7** `npm --prefix web run typecheck` pass. Người review chạy lệnh này; nó không
  nằm trong bộ test đóng băng.

### WP5 — Công cụ kịch bản demo

**Phạm vi file:**
- `scripts/payment_regression.py`;
- mới: `docker/Dockerfile.payment-gradual-regression`;
- `scripts/demo_console/app.py`, `scripts/run_runbook_demo.py`;
- test: `tests/test_demo_upgrade_wp5_*.py`.

Hành vi bắt buộc:

- **R5.1** Thêm hàm `gradual_delay_ms(elapsed_s, *, start_delay_s, ramp_s, max_ms)`:
  - bằng 0 khi `elapsed < start_delay`;
  - tăng tuyến tính tới `max_ms` trong `ramp_s` giây;
  - sau đó giữ nguyên `max_ms`.

  Mặc định `start_delay_s=60`, `ramp_s=600`, `max_ms=1200`. Giới hạn hợp lệ xem
  mục 7. Patch chèn đúng mã nguồn của hàm này (dùng `inspect.getsource`) vào
  `services/payment/app.py`, tại cùng các mốc chèn với patch hiện có.
- **R5.2** `gradual_dockerfile(base, **params)`:
  - `base` phải ghim digest trong repo `sentinelops/application`;
  - label `sentinelops.dev/variant=payment-gradual-regression` và label cho từng
    tham số;
  - file tracked là bản sinh ra với tham số mặc định.
- **R5.3** Fault Console thêm ba action:
  - `payment-gradual-fault`: mỗi 30 giây đặt độ trễ = `1200 × max(0,05, tiến độ)` ms
    trong 1200 giây, đúng như `runner.py`;
  - `deploy-gradual-image`: chạy script cấu hình trong `DEMO_CONSOLE_GRADUAL_SCRIPT`;
  - `reset-demo`: chạy script cấu hình trong `DEMO_CONSOLE_RESET_SCRIPT`.

  Áp dụng cùng quy tắc allowlist và che output như action hiện có.
- **R5.4** `run_runbook_demo.py` ghi receipt `runbook-demo-round/v2` với các trường:
  - `runner_invocation_id`, `round`, `scenario`;
  - các mốc thời gian: `deploy_started_at`, `change_observed_at`, `first_breach_at`
    (lấy từ `first_onset_at` của RCA), `incident_created_at`, `proposal_created_at`,
    `approved_at`, `recovered_at`;
  - `warning.{service, episode_started_at}`, lấy từ Prometheus: lần bắt đầu episode
    đầu tiên sau `deploy_started_at`;
  - `lead_seconds`, `deploy_to_breach_seconds`, `rca_top1`;
  - `proposal` (bản sao, đã che bí mật), `action.{phase, telemetry_verified}`,
    `dispatch_count`, `notifications.{early_warning, proposal_pending, outcome}`;
  - `image_params`.

Test nghiệm thu:

- **A5.1** (`test_gradual_delay_values`) Với tham số mặc định, các mốc
  t = 0, 59, 60, 360, 660, 1000 giây cho ra 0, 0, 0, 600, 1200, 1200 ms.
- **A5.2** (`test_gradual_patch_and_dockerfile`) Đủ các mốc chèn trong
  `services/payment/app.py` hiện tại; file tracked khớp bản sinh ra; từ chối tham
  số ngoài giới hạn và base không đúng repo.
- **A5.3** (`test_console_gradual_actions`) Có đúng 3 action mới; báo lỗi khi chưa cấu
  hình script; lịch đặt độ trễ của `payment-gradual-fault` theo đồng hồ giả đúng như
  R5.3.
- **A5.4** (`test_round_receipt_v2_fields`) Từ nguồn giả: đủ trường, tính đúng
  `lead_seconds` và `deploy_to_breach_seconds`, và receipt mẫu qua được
  `check_demo_round`.

### WP6 — Grafana và allowlist `/ready`

**Phạm vi file:**
- mới: `observability/grafana/dashboards/sentinelops-risk.json`;
- sửa: `infra/aws/edge.tf`, test edge hiện có (chỉ thêm);
- test: `tests/test_demo_upgrade_wp6_*.py`.

Hành vi bắt buộc:

- **R6.1** Dashboard "SentinelOps — Cảnh báo sớm", gồm:
  - timeseries `sentinelops_risk_score` theo service, kèm hai đường ngưỡng từ
    `sentinelops_risk_threshold`;
  - state timeline từ `sentinelops_risk_state`;
  - stat `sum(sentinelops_risk_episode_active)`;
  - annotation `changes(sentinelops_risk_episode_started_unixtime[2m]) > 0`.

  `render_grafana.py` tự nạp mọi file trong thư mục dashboards.
- **R6.2** `edge.tf` cho qua `/ready` và `/ready/incidents` ở cả CloudFront function
  lẫn rule ALB; vẫn chặn `/readyx`, `/docs`, `/openapi.json`.

Test nghiệm thu:

- **A6.1** Dashboard JSON hợp lệ, có đúng 4 panel trên với đúng chuỗi `expr`.
- **A6.2** Regex của CloudFront function và rule ALB cho qua đúng hai đường dẫn mới,
  vẫn chặn các đường dẫn đang bị chặn.

### WP7 — Hạ tầng và manifest (lane B, ghi AWS phải qua 2 lượt review)

**Phạm vi file:**
- mới: `infra/aws/alerts.tf`;
- sửa: `infra/aws/variables.tf`, `scripts/check_aws_iac.py`, `tests/test_aws_iac.py`
  (chỉ thêm), `scripts/render_worker_manifests.py`;
- test: `tests/test_demo_upgrade_wp7_*.py`.

Hành vi bắt buộc:

- **R7.1** SNS và quyền gửi:
  - `aws_sns_topic.demo_alerts`: tên `${var.name_prefix}-demo-alerts`, mã hoá
    `alias/aws/sns`;
  - một `aws_sns_topic_subscription` (protocol `email`) cho mỗi địa chỉ trong
    `var.demo_alert_emails` (sensitive, mặc định rỗng; địa chỉ cấp ngoài repo);
  - role `alerts_publisher` theo mẫu Pod Identity của `ebs_csi.tf`, chỉ có
    `sns:Publish` trên đúng ARN của topic;
  - `aws_eks_pod_identity_association` cho SA `payment-rollback` trong
    `sentinelops-b-actions`, là SA của pod `remediation-controller`;
  - khi khởi động, notifier gọi `sts:GetCallerIdentity` và chỉ log ARN role, không
    log credential. Nếu không lấy được credential thì log
    `notifications-unavailable` rồi thử lại. Bước L2 phải thấy đúng ARN của
    `alerts_publisher`, vì pod này đặt `automountServiceAccountToken: false`.
- **R7.2** Guard IaC: role `alerts_publisher` phải có đúng một statement
  `sns:Publish` trên `aws_sns_topic.demo_alerts.arn`. Mọi wildcard, hành động khác
  hoặc binding khác đều FAIL. Có test né guard theo cùng kiểu test của EBS CSI.
- **R7.3** `render_worker_manifests.py`:
  - pod incident-worker thêm container `risk-worker`, chạy
    `scripts/run_risk_worker.py`; health store mount read-only; scratch là
    `emptyDir`; OTLP trỏ tới Service collector;
  - pod remediation-controller thêm container `notifier`, chạy
    `scripts/run_notifier.py`; controller store mount read-only; store của notifier
    là thư mục `notifier/` trên cùng PVC, mount bằng `subPath` có quyền ghi, để chống
    gửi trùng vẫn đúng sau khi pod khởi động lại; truyền `--topic-arn`, `--ui-url`
    và `--metrics-otlp-endpoint`;
  - không container nào được thêm quyền ghi vào store của container khác.

Test nghiệm thu:

- **A7.1** `check_aws_iac` PASS với cấu hình thật; mỗi cách né guard của R7.2 bị bắt.
- **A7.2** Manifest render ra:
  - `risk-worker` mount health store read-only, không có mount ghi nào khác ngoài
    scratch;
  - `notifier` mount controller store read-only, và chỉ có quyền ghi ở `subPath`
    `notifier/`;
  - không image nào dùng tag; mọi image ghim digest.
- **A7.3** `terraform validate` và các test terraform hiện có pass.

## 4. Test hiện có được phép thay đổi

Chỉ hai loại assertion sau được phép sửa, và chỉ trong WP1 và WP4:

1. Assertion yêu cầu proposal **mới** có `expires_at`, hoặc proposal mới hết hạn
   thành `approval-expired`. Thay bằng assertion v2 tương ứng; giữ một assertion
   cho v1.
2. Assertion RCA cũ bị chặn trong khi service gốc **vẫn đang vi phạm** (thay đổi có
   chủ đích của R1.6).

File được phép đụng: `tests/test_remediation_controller.py`,
`tests/test_runbook_approval.py`, `tests/test_runbook_proposal_api.py`,
`tests/test_manual_memory_recovery.py`, `tests/test_remediation_executor.py`,
`tests/test_runbook_demo.py`, `tests/test_patch_react_oom_review.py`,
`tests/test_ui_preview.py`, `tests/ui/proposals.test.mjs`.

Commit message phải liệt kê từng test được sửa và lý do. Người review kiểm không còn
assertion nào khác bị đổi.

## 5. Checklist review (lượt 1 và lượt 2)

- [ ] Diff của test đóng băng rỗng (mục 1.4.1).
- [ ] Chỉ đụng file trong phạm vi; mọi thay đổi test cũ nằm trong mục 4.
- [ ] Bất biến an toàn còn nguyên:
  - mọi rollback cần người duyệt; một lần dispatch; dry-run trước PATCH;
  - chỉ rollback trong cùng repo image; kill switch chặn dispatch;
  - H4 và change window không đổi.
- [ ] Cảnh báo không mở incident và không cho phép hành động.
- [ ] Email và log không chứa bí mật; không in token, DSN hay env.
- [ ] Đọc được dữ liệu cũ: proposal v1 và receipt cũ.
- [ ] Đường lỗi: notifier lỗi không ảnh hưởng controller; bộ chấm rủi ro lỗi không
  ảnh hưởng incident worker.
- [ ] Tên metric, đường dẫn API và chuỗi UI khớp đúng spec.
- [ ] Regression đầy đủ pass; CI của SHA đã push xanh.

## 6. Nhật ký đóng băng

| WP | Commit đóng băng (F_n) | Ngày | Người duyệt test |
| --- | --- | --- | --- |
| WP0 + WP1 | chưa đóng băng | | |
| WP2 | chưa đóng băng | | |
| WP3 | chưa đóng băng | | |
| WP4 | chưa đóng băng | | |
| WP5 | chưa đóng băng | | |
| WP6 | chưa đóng băng | | |
| WP7 | chưa đóng băng | | |

## 7. Tham số được phép chỉnh khi tập dượt

Chỉ được chỉnh ba tham số của image chậm dần, và chỉ ở bước L2:

| Tham số | Mặc định | Giới hạn |
| --- | --- | --- |
| `start_delay_s` | 60 | 0–120 |
| `ramp_s` | 600 | 300–900 |
| `max_ms` | 1200 | 800–2000 |

Các ngưỡng trong checker (`lead_seconds ≥ 180`, `deploy_to_breach_seconds ≤ 600`),
threshold model, quy tắc episode và mọi quy tắc an toàn đều không được chỉnh. Nếu
không có bộ tham số nào trong giới hạn đạt checker, dùng phương án hai màn của
DEMO_UPGRADE_PLAN mục 6 và ghi kết quả đo.
