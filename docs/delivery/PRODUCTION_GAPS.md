# Khoảng cách tới vận hành thật — ngoài phạm vi demo

Rà soát `main` tại `80ec2cb` ngày 2026-11-23, chỉ đọc. Ngày 2026-11-24 owner chọn tập
trung nâng cấp demo ([DEMO_UPGRADE_PLAN.md](DEMO_UPGRADE_PLAN.md)), nên danh sách này
**chưa được lên lịch**. Tài liệu dùng cho hai việc: trả lời thẳng khi ban giám khảo
hỏi "đưa vào vận hành thật thì sao", và làm backlog khi dự án đi tiếp.

Chỉ liệt kê điểm lớn, loại người xem dễ thắc mắc. Lỗi nhỏ không đưa vào đây.
Cỡ việc: S ≤ 1 ngày, M 2–3 ngày, L từ 4 ngày, đã tính hai lượt review.

## A. Vòng tự sửa lỗi gắn với kịch bản demo

### A1. Không có bộ theo dõi deploy thường trực — L

- **Hiện trạng:** H4 chỉ cho rollback khi có bằng chứng "vừa có thay đổi deploy".
  Bằng chứng này chỉ được ghi khi script demo/trial tự deploy image lỗi:
  `DeploymentChangeReview` chỉ được tạo trong `scripts/run_runbook_demo.py`,
  `scripts/run_g6_trial.py`, `scripts/run_s2_h4.py` và
  `scripts/check_rollback_kubernetes.py`.
- **Hệ quả:** một lần deploy lỗi thật do dev hoặc CI thực hiện sẽ không tạo bằng
  chứng, nên hệ thống không bao giờ đề xuất rollback.
- **Hướng sửa:** một service thường trực theo dõi các Deployment mục tiêu và ghi vào
  change store. Incident worker đã có sẵn tham số đọc store này
  (`--deployment-change-store`, `scripts/run_incident_worker.py:177`).

### A2. Known-good là con trỏ tĩnh do người ký — M

- **Hiện trạng:** controller nạp một `--known-good-ref` duy nhất lúc khởi động
  (`scripts/run_remediation_controller.py:107`).
- **Hệ quả:** release tốt mới không tự thành known-good, nên rollback có thể quay về
  một bản rất cũ. Mỗi release phải ký tay rồi khởi động lại controller.
- **Hướng sửa:** release mới chạy khỏe đủ N phút thì tự thành known-good; người vẫn
  ghim hoặc ghi đè được.

### A3. Phạm vi cứng một service — L

- **Hiện trạng:** schema ghim cứng namespace, deployment và container
  (`contracts/payment-known-good.schema.json:21-25`); route cứng
  (`src/sentinelops/telemetry/core.py:13`); runbook OOM cứng `64Mi → 256Mi`
  (`src/sentinelops/remediation/pod_memory.py:28`).
- **Hệ quả:** thêm một service đồng nghĩa với sửa code và schema.
- **Hướng sửa:** service catalog cấu hình được (service, SLO, route, target, giới hạn
  hành động); tham số runbook nằm trong giới hạn của policy.

## B. Quy trình on-call

### B1. Paging — M

- **Hiện trạng:** không có Alertmanager, SNS hay chat. Rule cảnh báo worker
  (`observability/rules/sentinelops-workers.yml`) có được tính nhưng không gửi đi đâu.
  Health worker từng crashloop trên EKS mà không ai được báo.
- **Hướng sửa:** Alertmanager hoặc SNS cộng một kênh nhận, cho các sự kiện: incident
  mới, đề xuất mới, nhắc sau X phút, `escalated`, worker chết hoặc dữ liệu cũ. Bản demo
  chỉ làm email (DEMO_UPGRADE_PLAN mục 4.1–4.2); mức đầy đủ cần cả người trực dự phòng
  và nhắc lại.

### B2. Phiên đăng nhập — S–M

Token chỉ nằm trong RAM: tải lại trang là phải đăng nhập lại kèm TOTP. Với người trực
lúc nửa đêm thì cần refresh token, ví dụ qua cookie httpOnly phía server.

## C. Model và tuyên bố sản phẩm

### C1. Spec và runtime chưa khớp

- **Hiện trạng:** spec ghi Isolation Forest là bộ phát hiện chính
  (`docs/architecture/PRODUCT_SPEC.md` §3). Thực tế incident chỉ do luật SLO mở;
  health worker đặt cứng `model_outputs_available=False`
  (`src/sentinelops/workers/health.py:220`); model chỉ thêm bằng chứng vào RCA.
- **Hướng sửa:** benchmark ở C2, rồi chọn một trong hai:
  - (a) Giữ SLO mở incident, ghi rõ model là bằng chứng phụ.
  - (b) Cho IF mở incident, sau khi IF đã qua gate.

### C2. Số liệu chất lượng còn mỏng — benchmark đầy đủ

Số liệu hiện có của release `de38fc6b`:

| Phép đo | Kết quả | Nguồn |
| --- | --- | --- |
| Alibaba, các service model chưa gặp | IF F1 0,336, MAD 0,300; precision 0,26; 0,45 incident giả mỗi giờ-service khỏe | [G3](../reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md) |
| Testbed local | Chỉ đo S1/S3: 12 sự kiện, 6,35 giờ-service khỏe; IF và MAD bằng nhau (F1 1,0) | [Layer 2](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md) |
| XGBoost | AP 0,95; 20/20 precursor, báo trước khoảng 270 giây (bị giới hạn bởi horizon 300 giây) | [Layer 2](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md) |
| Chưa đo | S2/S4, MTTD live; holdout chưa mở | [Gates](../architecture/CONSTRAINTS_AND_GATES.md) |

Câu cần trả lời: model có hơn baseline đơn giản không? Ba baseline là MAD, luật SLO
(thứ đang mở incident thật) và slope.

1. **B0 — chốt protocol trước khi chạy.** Freeze model, threshold, feature và config;
   viết sẵn metric và quy tắc quyết định; không tune trên tập test.
2. **B1 — Alibaba chưa gặp.** Tính lại kèm khoảng tin cậy. Chạy offline.
3. **B2 — ma trận testbed S1–S5.** Gồm lỗi Payment đột ngột, deploy lỗi, suy giảm từ
   từ, áp lực DB, hai lỗi độc lập; nhiều seed và cường độ. Mục tiêu khoảng 30 sự kiện
   mỗi kịch bản và khoảng 50 giờ-service khỏe.
4. **B3 — live trên EKS.** Chạy khỏe 24 giờ (khoảng 72 giờ-service) cộng các lượt gây
   lỗi thật; đo MTTD có tính độ trễ nguồn.
5. **B4 — holdout.** Mở đúng một lần ở cuối. Không đảo ngược được, cần owner ký.

Metric: precision, recall và F1 theo sự kiện; số incident giả mỗi giờ-service; MTTD
p50/p90; PR-AUC và lead time của XGBoost; RCA top-1/top-3 tính cả lượt abstain; giá
trị gia tăng so với luật SLO (sớm hơn bao lâu, bắt thêm bao nhiêu sự kiện, thêm bao
nhiêu báo động giả).

### C3. Nhãn "confidence %" trên UI — S

Hai con số mang nhãn này là điểm RCA (`web/src/live/adapters.ts:139`) và mức khớp
runbook (`web/src/live/adapters.ts:94`), đều là điểm 0–1 nhân 100, không phải xác
suất đã hiệu chỉnh. Bản demo đổi nhãn kèm giải thích (DEMO_UPGRADE_PLAN mục 5); hiệu
chỉnh thật cần số liệu từ C2.

## D. Độ bền và vận hành

### D1. Bằng chứng nằm trên một ổ đĩa gắn một node — L

- **Hiện trạng:** `ArtifactStore` chỉ lưu trên file system
  (`src/sentinelops/telemetry/store.py:15`), trên ổ EBS 5 GiB gắn một node, không có
  cơ chế xoá hay giới hạn dung lượng. Worker 1 replica, chiến lược `Recreate`
  (`scripts/render_worker_manifests.py:252`). Incident worker buộc nằm cùng node với
  health worker.
- **Hệ quả:** mất node hoặc AZ là mất phát hiện cho tới khi node về; chạy lâu thì đầy ổ.
- **Hướng sửa:** lưu trên S3 có lifecycle (`docs/architecture/PRODUCT_SPEC.md` §4 đã dự kiến adapter S3); worker
  không giữ state; controller có leader election.

### D2. Hạ tầng chạy từ laptop — M

- **Hiện trạng:** Terraform không khai báo backend nên state nằm trên máy cá nhân; bản
  state đã redact được commit vào repo
  ([AWS_TERRAFORM_STATE_REDACTED.json](AWS_TERRAFORM_STATE_REDACTED.json)). Image được
  build và push từ máy cá nhân; CI chỉ kiểm thử và validate, không build/push image và không deploy (`.github/workflows/ci.yml`). Model
  release được copy từ máy thành viên.
- **Hướng sửa:** S3 backend có lock; CI build, push và ký image khi lên `main`; deploy
  qua pipeline; model registry trên S3 có kiểm hash; gỡ state khỏi repo.

### D3. Hồ sơ môi trường — M

Môi trường demo dùng một account, RDS micro một AZ, và theo
[AWS_DEMO_PLAN](AWS_DEMO_PLAN.md) ngân sách chưa có email cảnh báo. Bản vận hành cần
tách account dev và prod, RDS Multi-AZ có PITR, và cảnh báo ngân sách.

## E. Bảo mật

### E1. Các mục còn mở trong SECURITY_POSTURE — M–L

Từ [SECURITY_POSTURE_2026-11-19.md](SECURITY_POSTURE_2026-11-19.md):

- SA `payment-rollback` PATCH được toàn bộ pod template (mức CAO). Cần admission
  policy chỉ cho đổi image sang digest đã duyệt, và bật PSA restricted.
- UI public chạy chung pod, chung UID và chung ổ đĩa với controller, lại không có
  NetworkPolicy. Lỗi ở UI có thể ghi giả một bản duyệt. Cần tách pod và chuyển bản
  duyệt sang DB qua role ghi riêng.
- Chưa có WAF.

### E2. Giải thích LLM gửi dữ liệu ra ngoài AWS — M

- **Hiện trạng:** provider là `api.openai.com`, egress không bị chặn
  ([LLM rollout](LLM_EXPLANATION_ROLLOUT_2026-11-21.md)), trong khi spec và ADR-004
  ghi Bedrock. Tài liệu rollout cũng ghi một key gateway cũ cần thu hồi.
- **Hướng sửa:** ADR quyết định dữ liệu nào được gửi ra ngoài; chặn egress; key trong
  Secrets Manager có xoay vòng.

### E3. Công cụ gây lỗi nằm chung với sản phẩm — M

- **Hiện trạng:** endpoint fault bật trên service EKS
  (`scripts/kubernetes_testbed.py:121`); Fault Console có quyền AWS; job `demo-runner`;
  RBAC `demo-fault` cần ClusterAdmin; trang mô phỏng `/ui/demo/` chạy chung domain.
- **Hướng sửa:** tách namespace và IAM riêng cho testbed/chaos; bản deploy sản phẩm
  không có endpoint fault.

## Trả lời ngắn khi ban giám khảo hỏi

| Câu hỏi | Trả lời trung thực |
| --- | --- |
| "Rollback có chạy với deploy thật không?" | Bản demo theo dõi deploy do kịch bản tạo ra. Bản vận hành cần bộ theo dõi deploy thường trực (A1); kiến trúc đã có chỗ cắm cho việc đó. |
| "AI phát hiện lỗi ở đâu?" | Luật SLO mở incident vì luật này kiểm chứng được. Isolation Forest góp vào điểm RCA; XGBoost cảnh báo trước khi vi phạm. Benchmark đầy đủ nằm ở C2. |
| "Nếu node chết thì sao?" | Bản demo là một replica trên ổ đĩa của node. Bản vận hành chuyển bằng chứng sang S3 và chạy worker không giữ state (D1). |
| "Dữ liệu sự cố có bị gửi ra ngoài không?" | Phần giải thích LLM hiện gọi OpenAI; bằng chứng gốc và quyết định vẫn nằm trong hệ thống. Bản vận hành cần quyết định nhà cung cấp và chặn egress (E2). |
| "Ai được bấm duyệt?" | Chỉ operator đăng nhập Cognito có MFA. Mỗi lần duyệt được ghi tên người duyệt, thời điểm và lý do nếu có. |

## Thứ tự gợi ý nếu làm tiếp

1. A1 và B1 — để vòng tự sửa lỗi dùng được với deploy thật.
2. C2 — benchmark rồi chốt cách định vị model (C1).
3. D1, D2, E1, E2, E3 — độ bền, hạ tầng, bảo mật; phần lớn thuộc lane B.
4. A2, A3 — mở rộng phạm vi.
