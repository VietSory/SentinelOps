# Báo cáo triển khai Explanation cho Root Cause

**Ngày:** 2026-11-20
**Branch:** `fix/demo-wait-transient`
**Phạm vi:** gửi evidence và giả thuyết root cause hiện có tới LLM, nhận lời giải thích bằng ngôn ngữ tự nhiên, kiểm tra đầu ra rồi hiển thị cho người trực duyệt.

## Cập nhật 21/11/2026

OpenAI Responses chính chủ thay thế gateway Xingchi. DNS không còn là blocker
cho lần gọi từ local. Khi bật Responses, công cụ triển khai mặc định dùng base URL
`https://api.openai.com/v1`. Cấu hình API local vẫn cần URL, model và key cùng nhau.
Actor header là tùy chọn cho gateway tương thích cần header này. Với OpenAI chính
chủ, để `SENTINELOPS_LLM_ACTOR_AUTHORIZATION` unset.
(`scripts/aws_demo/deploy_llm_explanation.sh:33`, `scripts/aws_demo/deploy_llm_explanation.sh:116`,
`src/sentinelops/api_settings.py:54`)

Adapter dùng `text.format.type=json_schema` với schema object và `strict: false`.
OpenAI đã trả HTTP 400 khi request dùng `json_object` mà input không chứa chữ `json`.
Ứng dụng tiếp tục kiểm tra cấu trúc và liên kết evidence cục bộ.
(`.herdr/out/pr-body.md:20`, `src/sentinelops/explanations/openai_compatible.py:14`,
`src/sentinelops/explanations/service.py:205`)

Theo kết quả bàn giao, lượt gọi OpenAI thật từ **local** dùng model `gpt-6.1-sol`
với fixture `samples/incident-envelope.sample.json` trả `fallback=false`, provider
`openai-responses/v1`, root cause `rds`, RCA score `0.91` và refs `E-001`, `E-002`,
`E-003`. ID provider trong wrapper API có hậu tố digest. Kết quả explanation trên
EKS vẫn cần xác minh.
(`docs/delivery/LLM_EXPLANATION_ROLLOUT_2026-11-21.md:21`,
`samples/incident-envelope.sample.json:39`, `src/sentinelops/explanations/openai_compatible.py:139`)

### Các nhóm lỗi đã vá sau review

- **Grounding và lọc dữ liệu.** Mở rộng lọc hostname/IP và mẫu bí mật ở fact gửi
  đi và narrative nhận về. Validator từ chối narrative rỗng, số đếm và phần trăm
  thiếu evidence, tên service không phù hợp và các mẫu khẳng định nhân quả quá
  mức. Ca tráo metric vẫn là `xfail` vì đối chiếu token chưa kiểm được ngữ nghĩa.
  (`/tmp/sentinelops-xp-base/.herdr/out/xp-sec.md:11`, `.herdr/out/xp-adv-fix.md:9`,
  `.herdr/out/xp-adv-fix.md:18`)
- **Transport và credential.** Giới hạn byte khi đọc stream thô và từ chối nội dung
  nén trước khi đọc. Kiểm tra ký tự hợp lệ trong key, loại output phản chiếu
  credential và kiểm tra refusal hoặc trạng thái message chưa hoàn tất ngay cả
  khi response có `output_text`. (`/tmp/sentinelops-xp-base/.herdr/out/xp-sec.md:16`,
  `/tmp/sentinelops-xp-base/.herdr/out/xp-logic.md:5`, `.herdr/out/xp-fix2.md:3`)
- **Fallback, circuit breaker và vòng đời runtime.** Fallback ngay nếu hết card sau
  lọc. Timeout khi chờ slot giữ nguyên bộ đếm lỗi provider. Lifespan dọn runtime
  khi mở control pool lỗi. Bedrock giữ tác vụ khởi tạo dùng chung để shutdown vẫn
  đóng được client tạo sau khi request bị hủy.
  (`/tmp/sentinelops-xp-base/.herdr/out/xp-logic.md:6`, `.herdr/out/xp-adv-fix.md:16`,
  `.herdr/out/xp-fix3.md:3`)
- **UI và fixture.** Tách nhãn và kiểu hiển thị cho AI, deterministic fallback và
  abstention. Card abstention hiện `No AI root cause`. Giữ ngắt dòng và bẻ chuỗi
  dài. Fixture dùng payload `2.0` có `root_cause` với response envelope `1.0`.
  Báo cáo UI ghi sandbox chặn vòng browser fixture trước khi mở Chrome.
  (`/tmp/sentinelops-xp-base/.herdr/out/xp-ui.md:10`,
  `/tmp/sentinelops-xp-base/.herdr/out/xp-ui.md:19`, `.herdr/out/xp-fix-ui.md:5`,
  `.herdr/out/xp-fix-ui.md:16`)
- **Hồi quy API.** Thêm ca mock kiểm tra remap refs cho claims và RCA, fallback khi
  transport hoặc output lỗi, `503` khi kết quả đã render có ref ngoài revision
  và từ chối bật hai provider cùng lúc. Báo cáo API ghi hai ca lọc chạy qua và
  lượt toàn tệp dừng tại `TestClient`. (`.herdr/out/xp-api-tests.md:3`,
  `.herdr/out/xp-api-tests.md:12`)

## Kết quả chính

Các phần dưới đây ghi lại hiện trạng và phép thử ngày 20/11/2026. Xem mục cập nhật
21/11/2026 để biết cấu hình và kết quả hiện hành.

Đã hoàn thiện luồng local end-to-end ở mức code:

```text
IncidentEnvelope hiện có
  → lọc và giới hạn evidence an toàn
  → gửi evidence + ranked root-cause hypothesis tới LLM
  → nhận narrative tiếng Việt
  → kiểm tra service, score, evidence refs và revision
  → map refs về evidence gốc
  → API/UI hiển thị explanation cho người trực
```

Luồng deterministic fallback vẫn hoạt động khi LLM không khả dụng. Fallback được đánh dấu rõ, không được báo thành kết quả AI.

## Những phần đã làm

### 1. Provider API thật

Đã thêm adapter OpenAI Responses-compatible tại:

- `src/sentinelops/explanations/openai_compatible.py`

Adapter thực hiện:

- gọi `POST {base_url}/responses`;
- gửi `model`, `instructions`, `input`, `max_output_tokens=512`;
- đặt `store=false` để không yêu cầu lưu response;
- gửi `Authorization: Bearer ...`;
- hỗ trợ header actor authorization tùy chọn;
- giới hạn kích thước request/response;
- không redirect và không ghi nội dung response vào log;
- chỉ chấp nhận Responses có `status=completed`;
- từ chối JSON lỗi, duplicate key, `NaN`, refusal/incomplete output và output vượt giới hạn.

Cấu hình local ngày 20/11/2026 dưới đây đã được thay thế bằng OpenAI chính chủ:

- Base URL: `https://api.xingchiapi.com/v1`
- Model: `gpt-6.1-sol`
- Wire API: Responses API
- Actor header: `local-image-extension`
- API key: đã đặt trong `.env` local bị Git ignore, không ghi vào report và không xuất hiện trong log.

### 2. Gửi evidence và root cause đã có

`src/sentinelops/explanations/service.py` hiện:

- không để LLM tự xếp hạng lại root cause;
- gửi ranked hypothesis và evidence cards đã giới hạn;
- loại source URI, actor, timeline, trigger reference và dữ liệu nhạy cảm khỏi request ngoài;
- dùng card ID tạm thời khi gửi ra ngoài;
- map card ID trở lại evidence ID gốc ở local;
- bắt buộc service và score phải khớp hypothesis đã xếp hạng;
- bắt buộc evidence refs thuộc đúng hypothesis;
- chặn narrative tự bịa metric, entity, PII hoặc mức chắc chắn không có trong evidence;
- tạo lời giải thích tiếng Việt, dễ đọc cho người trực;
- giữ nguyên score RCA và ghi rõ score không phải xác suất;
- fallback khi provider timeout, lỗi mạng, lỗi output, circuit open hoặc không có provider.

### 3. Bedrock

Đã giữ và hoàn thiện adapter Bedrock tại:

- `src/sentinelops/explanations/bedrock.py`

Adapter có timeout, giới hạn concurrency, xử lý lifecycle và đóng runtime an toàn. IAM Terraform cũng đã được bổ sung theo hướng cấp quyền invoke riêng cho explanation provider, nhưng phần này chưa được deploy hoặc nghiệm thu trên AWS.

### 4. API, schema và giao diện

Đã cập nhật:

- `src/sentinelops/api.py`: chọn OpenAI-compatible provider khi LLM config có mặt, nếu không thì dùng Bedrock hoặc fallback;
- `src/sentinelops/api_settings.py`: validate đầy đủ base URL, model, key, actor header và chống cấu hình đồng thời Bedrock + LLM;
- `contracts/explanation.schema.json`: schema explanation v2;
- `src/sentinelops/ui/assets/app.js` và CSS/client: phân biệt explanation do AI tạo với fallback/AI abstention;
- `docs/runbooks/EXPLANATIONS.md`, ADR và technical docs: mô tả contract, giới hạn, fallback và vận hành;
- `.env.example`: thêm mẫu cấu hình provider nhưng không chứa secret.

## Kết quả gọi API thật

Đã thử gọi bằng fixture tổng hợp `samples/incident-envelope.sample.json`, gồm root cause ranking cho `rds` và các evidence `E-001` đến `E-004`.

Kết quả quan sát được:

```json
{
  "provider": "deterministic-facts/v1",
  "fallback": true,
  "root_cause_service": "rds",
  "root_cause_score": 0.91,
  "evidence_refs": ["E-001", "E-002", "E-003"]
}
```

Đây là fallback, chưa phải explanation do LLM tạo. Nguyên nhân là máy không phân giải được `api.xingchiapi.com`; cả HTTP client và `curl` đều timeout ở bước DNS, nên request chưa tới API và chưa nhận được mã HTTP để kết luận key sai, quota thiếu hay payload bị từ chối.

## Kiểm chứng đã chạy

- Provider OpenAI-compatible, service explanation và Bedrock: **92 tests passed**.
- Contract validation: **35 schemas + 16 samples + 3 configurations passed**.
- Python compile check: passed.
- `git diff --check`: passed.
- Workflow/guard verification: passed.
- Cấu hình từ `.env`: đọc đúng base URL, model, actor header và xác nhận key tồn tại; giá trị key không được in ra.

## Trạng thái bàn giao

Hiện code đang ở local branch, chưa commit, chưa push và chưa deploy. Vì vậy chưa thể kết luận feature đã chạy production hoặc đã có bằng chứng `fallback=false` từ gateway thật.

Điều kiện cuối để demo hoàn chỉnh:

1. Máy/runtime phải resolve và kết nối được `api.xingchiapi.com`.
2. Chạy lại cùng fixture với cấu hình hiện tại.
3. Xác nhận response có `fallback=false`, provider ID là OpenAI-compatible và `root_cause.explanation` là narrative tự nhiên do LLM tạo.
4. Sau khi demo xong, nên rotate API key vì key đã từng được gửi trong hội thoại.
