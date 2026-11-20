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

| 27 | patch 18:05:49Z, rollout xong 18:06:17Z | `sha256:b06898f7db41848b26cd83d74e831c586eac09e9fcf7f5f0875a1e35fe92a851` (`git-eb8a276`) | `scripts/aws_demo/deploy_llm_explanation.sh`, dry-run rồi `LLM_DEPLOY_EXECUTE=1` |

| 28 | patch 18:15:12Z, rollout xong 18:15:40Z | `sha256:8f490f103de896f0aabb1780c8a1f054d3b74021051961b1722dad0208ca6b93` (`git-63c465d-liveui`) | như revision 27 |

Revision 27 làm mất UI React song ngữ đang chạy ở revision 24: lớp UI đó được
dựng bằng `web/deployment/Dockerfile.live` chồng lên image control-plane, từ
workspace chưa commit, nên không có trong image build từ repo. Revision 28 lấy
image `git-eb8a276` của merge `63c465d` làm gốc và chép nguyên lớp UI từ image
revision 24 (`index.html`, bundle React, `demo_routes.py`, `demo_site/`,
`live_observability.py`, `routes.py` đã vá, hook `install_live_observability`
trong `api.py`), giữ `app.js`, `client.js`, `styles.css` và validator mới. Sau
rollout `/ui/` phục vụ lại UI React, `/ui/demo/` trả 200 và explanation trong Pod
vẫn `fallback=false`. Lớp UI React vẫn chưa nằm trong repo; lần build từ repo
tiếp theo phải dựng lại lớp này, nếu không sẽ mất UI một lần nữa.

Revision 25 và 26 build từ cây chưa commit. Revision 27 build từ commit
`eb8a276` (cây sạch, label `org.opencontainers.image.revision` = SHA đầy đủ) và là
bản đang chạy. Các commit `main` merge sau đó (`ad38f14`, `37bd5d1`, `454c51c`)
không đổi file nào nằm trong image control-plane.

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
- Revision 27, cùng incident và cách chạy: **3/3 lần `fallback=false`**, provider
  `openai-responses/v1/…`, root cause `payment-service`, score `0.648`. Trước khi
  deploy, request thật lấy từ Pod được gọi lại 8 lần với OpenAI từ local: 8/8 qua
  validator.
- Incident mới nhất (`INC-OOM-…`, revision 5) không có RCA ranking nên trả
  fallback "Chưa có giả thuyết nguyên nhân gốc rễ…" — đúng thiết kế, không gọi LLM.
- Kiểm chứng trên chạy trực tiếp trong Pod, không qua route HTTP có OIDC.
  Chưa kiểm bằng trình duyệt có đăng nhập Cognito.
- Test trên commit `eb8a276`: `pytest tests/` 3165 passed, 48 skipped,
  1 xfailed (giới hạn đã biết về gán số cho metric); `scripts/validate_contracts.py`
  35 schema, 16 sample, 3 cấu hình; `node --test tests/ui/*.test.mjs` 46/46;
  `scripts.check_operator_ui --fixtures` PASS 34 hạng mục, 0 lỗi trang.

## Rollback

`rollback.json` của lượt revision 27 (quyền `0600`) đưa `operator-ui` về image
của revision 26, vẫn bật LLM. File nằm ở scratchpad của phiên làm việc và có thể
bị xoá khi khởi động lại máy.

Để tắt LLM và về đúng trạng thái trước rollout, dùng lịch sử revision của
Kubernetes (Pod sẽ khởi động lại vì `Recreate`):

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions \
  rollout undo deployment/remediation-controller --to-revision=24
```

Nếu có người khác đã rollout revision mới hơn 27, kiểm tra
`kubectl rollout history` trước khi undo để không ghi đè thay đổi của họ.

## Việc owner còn phải làm

- Thu hồi key gateway xingchi cũ, vì key đó đã xuất hiện trong hội thoại.
- Đặt hạn mức chi tiêu cho project chứa key OpenAI.
- Review và merge PR #88 (`fix/llm-explanation-review`).
