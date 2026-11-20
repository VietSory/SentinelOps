# Báo cáo triển khai Explanation cho Root Cause

**Ngày:** 2026-11-20
**Branch:** `fix/demo-wait-transient`
**Phạm vi:** gửi evidence và giả thuyết root cause hiện có tới LLM, nhận lời giải thích bằng ngôn ngữ tự nhiên, kiểm tra đầu ra rồi hiển thị cho người trực duyệt.

## Kết quả chính

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

Cấu hình local đã đặt theo thông tin được cung cấp:

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
