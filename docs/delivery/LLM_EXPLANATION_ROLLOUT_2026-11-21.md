# Biên bản rollout LLM explanation — 21/11/2026

Đích: EKS `sentinelops-dev`, context `sentinelops-demo`, namespace
`sentinelops-b-actions`, Deployment `remediation-controller` (strategy `Recreate`,
ba container `remediation-controller`, `operator-ui`, `oom-demo-controller`).
Provider: API OpenAI chính chủ `https://api.openai.com/v1`, model `gpt-6.1-sol`,
Responses API với `text.format` kiểu `json_schema`.

## Phạm vi thay đổi trên cụm

- Chỉ container `operator-ui` đổi image và thêm ba mục env:
  `SENTINELOPS_LLM_BASE_URL`, `SENTINELOPS_LLM_MODEL` (giá trị trực tiếp) và
  `SENTINELOPS_LLM_API_KEY` (`secretKeyRef` tới Secret `sentinelops-llm`,
  `optional: false`). Không có `SENTINELOPS_LLM_ACTOR_AUTHORIZATION`.
- ConfigMap `sentinelops-api-runtime` không đổi. Image của
  `remediation-controller` và `oom-demo-controller` giữ nguyên.
- Secret `sentinelops-llm` do owner tạo. Không công cụ nào đọc hay in giá trị của nó.

## Diễn biến

| Revision | Thời điểm (UTC) | Image `operator-ui` | Cách áp dụng |
| --- | --- | --- | --- |
| 24 | trước 2026-11-20T17:43Z | `sha256:8749365ece373abb05cc100538d21af9f76be2fe841cb062718157bba76e6761` (`oom-reader-compat-8ba0c5a`) | trạng thái trước LLM |
| 25 | rollout xong 2026-11-20T17:44:36Z | `sha256:963a2555fe0d5a92a0612b9d8cd1cacb0c399c95bc0611a834cb2a7d40d9ecbc` | JSON Patch thủ công, có `test` cho `resourceVersion` 746602, tên và image container |
| 26 | patch 17:52:22Z, rollout xong 17:53:00Z | `sha256:4bfa7193871e7f75a8e8f6cecfa0a1d0fdcf4a2e3b02f584636e453782819113` (`llmfix-3b9a560-a49f33a1`) | `scripts/aws_demo/deploy_llm_explanation.sh`, dry-run rồi `LLM_DEPLOY_EXECUTE=1` |

Revision 26 build từ `3b9a560` cộng các bản vá chưa commit trên nhánh
`fix/llm-explanation-review`. Image label `org.opencontainers.image.revision` ghi
`3b9a560…-llmfix-a49f33a1`.

## Kiểm chứng

- Egress: từ container `operator-ui`, `GET https://api.openai.com/v1/models`
  không kèm key trả HTTP 401; không NetworkPolicy nào chặn egress của Pod.
- Revision 25: trên incident thật có RCA ranking, chỉ 1/6 lần gọi ra
  `fallback=false`. OpenAI luôn trả 200; validator từ chối vì số viết kiểu
  "1.092" và vì narrative nhắc service của card được chọn nhưng không được ref.
  Revision 26 sửa cả hai và bổ sung chỉ dẫn chép số đúng như fact.
- Revision 26, chạy trong container `operator-ui` bằng đọc DB chỉ đọc
  (`default_transaction_read_only=on`), incident
  `INC-26880f10d5855b024c6d7f476132110b761a55b50bb802c14a04d56be408ade5`
  revision 4: **6/6 lần `fallback=false`**, provider `openai-responses/v1/…`,
  root cause `payment-service`, score `0.647789668398006`, narrative tiếng Việt
  dẫn đúng số `1016`/`1092` từ evidence và ghi rõ chưa phải nguyên nhân đã xác nhận.
- Incident mới nhất (`INC-OOM-…`, revision 5) không có RCA ranking nên trả
  fallback "Chưa có giả thuyết nguyên nhân gốc rễ…" — đúng thiết kế, không gọi LLM.
- Kiểm chứng trên chạy trực tiếp trong Pod, không qua route HTTP có OIDC.
  Chưa kiểm bằng trình duyệt có đăng nhập Cognito.
- Test trên cây của revision 26: `pytest tests/` 3163 passed, 48 skipped,
  1 xfailed (giới hạn đã biết về gán số cho metric); `scripts/validate_contracts.py`
  35 schema, 16 sample, 3 cấu hình; `node --test tests/ui/*.test.mjs` 46/46;
  `scripts.check_operator_ui --fixtures` PASS 34 hạng mục, 0 lỗi trang.

## Rollback

`rollback.json` của lượt revision 26 (quyền `0600`) đưa `operator-ui` về revision
25, vẫn bật LLM. File nằm ở scratchpad của phiên làm việc và có thể bị xoá khi
khởi động lại máy.

Để tắt LLM và về đúng trạng thái trước rollout, dùng lịch sử revision của
Kubernetes (Pod sẽ khởi động lại vì `Recreate`):

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions \
  rollout undo deployment/remediation-controller --to-revision=24
```

Nếu có người khác đã rollout revision mới hơn 26, kiểm tra
`kubectl rollout history` trước khi undo để không ghi đè thay đổi của họ.

## Việc owner còn phải làm

- Thu hồi key gateway xingchi cũ, vì key đó đã xuất hiện trong hội thoại.
- Đặt hạn mức chi tiêu cho project chứa key OpenAI.
- Commit và mở PR cho nhánh `fix/llm-explanation-review`; image đang chạy được
  build từ cây chưa commit.
