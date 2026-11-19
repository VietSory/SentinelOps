# Kết quả demo SentinelOps — 19/11/2026

**Cả bốn kịch bản KB1, KB2, KB3 và B7 đã chạy thật. B7 lúc 09:47–09:56 UTC đã sinh đề xuất runbook đầu tiên, chứng minh di trú epoch thành công.** Ba lượt trước đều đến `ESCALATED` và không có đề xuất. B7 có capture **v6**, **`model_evidence` với 3 signal `AVAILABLE`**, **1 receipt deployment-change trong capture**; RCA **không abstain**, Payment đứng đầu với coverage **0,80**, score **0,7974433166482036**, margin **0,39658798795773287**. Nguồn: **LIVE-19**, **xác minh live 19/11/2026**, **LIVE-19-FX3** và **LIVE-19-B7**, định nghĩa dưới đây.

**Đề xuất B7 CHƯA ĐƯỢC DUYỆT; rollback tự động CHƯA CHẠY.** Owner chưa có mã MFA nên chưa bấm **Duyệt** trên website. Proposal có TTL **15 phút**, hết hạn khoảng **2026-11-19T10:10:52Z**; phải chạy lại B7 để sinh đề xuất mới. **Policy đang BẬT**: owner thêm `--policy-enabled` lúc **09:58:23**, controller rollout xong **09:58:36 UTC**. Khôi phục image gốc lúc 09:55:59.088 là thao tác kết thúc lượt chạy, chưa phải executor rollback.

## 1. Phương pháp và nguồn bằng chứng

- **LIVE-19** là kết quả đo/quan sát live ngày **19/11/2026 do operator cung cấp trong yêu cầu giao việc ban đầu**, gồm timeline KB1, số liệu Prometheus/Grafana, log worker/controller, trạng thái incident, thay đổi hạ tầng và chẩn đoán H4. Nhãn này không có nghĩa người soạn đã chạy lại kịch bản.
- **Xác minh live 19/11/2026** là kết quả kiểm tra trực tiếp bổ sung của worker **`v2-store`**: readiness qua port-forward, kết nối Postgres, snapshot incident và phản hồi CloudFront. Dùng nhãn này để dẫn bằng chứng live; không đưa đường dẫn artifact bị gitignore vào tài liệu. Phân biệt trạng thái cuối đã xác minh với các lifecycle trung gian trong timeline ban đầu.
- **LIVE-19-FX3** là kết quả đo thật bổ sung ngày **19/11/2026**, được bàn giao trong yêu cầu cập nhật tài liệu: timeline KB2/KB3, model đã nạp và readiness mới, coverage RCA, lưu trữ Prometheus và ba chốt còn lại. Nhãn này không có nghĩa người soạn đã chạy lại kịch bản. Snapshot cũ có 2 incident và `model=false` được giữ kèm bối cảnh lịch sử; chúng không mô tả trạng thái mới sau KB2/KB3 và nạp model.
- **LIVE-19-B7** là số đo thật operator bàn giao cho lượt B7 **09:47–09:56 UTC**, các ID/artifact receipt, capture, RCA, proposal và cập nhật policy đến **09:58:36 UTC ngày 19/11/2026**. Đây là nguồn cho kết quả sau di trú; người soạn không chạy lại EKS. Bản cập nhật tài liệu dùng branch `feat/grafana-incident-dashboard`, commit nền `a16eeb9`.
- **`path:line`** là bằng chứng source/config trong workspace tại lúc soạn, dùng giải thích cách tính metric và điều kiện hệ thống; không thay thế bằng chứng một thao tác đã chạy trên EKS.
- Tất cả mốc KB1/KB2/KB3/B7 và bật policy dùng **UTC ngày 19/11/2026**. Khoảng **02:11–02:12** của KB1 chỉ chính xác đến phút; không gán timestamp giây riêng cho từng event, lifecycle hoặc controller poll. Nguồn: **LIVE-19**, **LIVE-19-FX3**, **LIVE-19-B7**.
- So sánh baseline với lúc fault đang bật theo cùng service và đơn vị ms; tách thời điểm POST fault, snapshot metric, xuất bản incident và gỡ fault. Không suy ra thời gian phát hiện chính xác từ snapshot metric hoặc khoảng log theo phút. Nguồn timeline: **LIVE-19**.

### Môi trường của lượt chạy

| Thuộc tính | Giá trị | Bằng chứng |
| --- | --- | --- |
| Cụm EKS | `sentinelops-dev` | LIVE-19 |
| AWS profile / Kubernetes context | `AWS_PROFILE=console` / `sentinelops-demo` | LIVE-19 |
| Namespace | `sentinelops-b-actions` | LIVE-19 |
| Branch | `feat/grafana-incident-dashboard` | LIVE-19 |
| UI public | <https://dr76usfhbe2w8.cloudfront.net/ui/> | LIVE-19: CloudFront đã bật hôm nay |
| Grafana public | <https://dr76usfhbe2w8.cloudfront.net/grafana/> | LIVE-19: CloudFront đã bật hôm nay |

### Cách đọc số đo

| Đại lượng | Định nghĩa trong cấu hình | Bằng chứng |
| --- | --- | --- |
| Request rate | Tổng `rate(sentinelops_http_requests_total[1m])` theo service, đơn vị req/s | `observability/rules/sentinelops-business.yml:22` |
| p95 latency | `histogram_quantile(0.95, sum by (service, le) (rate(sentinelops_http_request_duration_ms_milliseconds_bucket[5m])))`; histogram đã dùng ms, không nhân thêm 1000 | `observability/rules/sentinelops-business.yml:82` |
| `sentinelops:slo_latency_breach` | `1` khi p95 lớn hơn 750 ms, `0` khi có dữ liệu trong ngưỡng; lọc dữ liệu không hợp lệ trước so sánh | `observability/rules/sentinelops-business.yml:112` |
| Error ratio / error breach | Tỷ lệ 5xx theo cửa sổ 5 phút; ngưỡng error breach lớn hơn 0,05 | `observability/rules/sentinelops-business.yml:25`, `observability/rules/sentinelops-business.yml:115` |
| Health policy | Tối thiểu 30 request, coverage 0,8; 2 bucket vào trạng thái breach, 3 bucket để hồi phục | `configs/slo_health_policy.json:5` |

Recording rule mô tả cửa sổ metric hiện tại, không phải toàn bộ trạng thái SLO có lưu của health worker (`observability/rules/sentinelops-business.yml:2`). Request rate tại một snapshot cũng không thay thế bằng chứng eligibility/coverage của từng bucket; bảng KB1 ghi đúng rate đã được bàn giao và không suy thêm kết quả eligibility chưa có.

### Hạ tầng đã làm trong ngày

| Hạng mục | Kết quả đã bàn giao | Bằng chứng |
| --- | --- | --- |
| CloudFront | Đã bật distribution, có hai URL public thật nêu trên | LIVE-19 |
| Recording rules nghiệp vụ | Thêm **17** rule: tuổi telemetry counter/histogram; request rate; error ratio 5m/1h/6h; success ratio 5m/1h/6h; p50/p95/p99 latency; error budget remaining và burn rate 6h; latency breach, error breach và trạng thái SLO theo service | `observability/rules/sentinelops-business.yml:16`, `observability/rules/sentinelops-business.yml:22`, `observability/rules/sentinelops-business.yml:25`, `observability/rules/sentinelops-business.yml:82`, `observability/rules/sentinelops-business.yml:101`, `observability/rules/sentinelops-business.yml:112`, `observability/rules/sentinelops-business.yml:115`, `observability/rules/sentinelops-business.yml:124`; triển khai hôm nay: LIVE-19 |
| Dashboard nghiệp vụ/sự cố | Thêm dashboard UID `sentinelops-incidents`, tên `SentinelOps — suc khoe nghiep vu va su co` | `observability/grafana/dashboards/sentinelops-incidents.json:2`; thực hiện hôm nay: LIVE-19 |
| Ship nhiều dashboard | Renderer đọc toàn bộ file dashboard JSON rồi đưa vào ConfigMap `grafana-dashboards` | `scripts/aws_demo/render_grafana.py:181`, `scripts/aws_demo/render_grafana.py:193`; thực hiện hôm nay: LIVE-19 |
| Traffic | Tăng `sentinelops-traffic` lên **2 replica** | LIVE-19 |
| Lưu trữ Prometheus | Đã chuyển từ `emptyDir` sang **PVC 20Gi**, retention **15 ngày**; trước đó đã mất lịch sử **2 lần** | LIVE-19-FX3 |

## 2. KB1 — Payment latency 950 ms: ĐÃ CHẠY THẬT

### Timeline thực tế

| Thời điểm UTC ngày 19/11/2026 | Thao tác / quan sát thực tế | Bằng chứng |
| --- | --- | --- |
| **02:04:28** | Baseline fault `healthy`. p95 Checkout **46,37 ms**, Payment **5,74 ms**, Inventory **4,83 ms**; `slo_latency_breach` cả ba bằng **0** | LIVE-19 |
| **02:04:55** | Tiêm lỗi vào `payment-service`: `POST /internal/faults`, payload `{"mode":"latency","delay_ms":950}` | LIVE-19; handler: `services/payment/app.py:97` |
| **02:06:57** | p95 Checkout **968,08 ms**, Payment **963,82 ms**, Inventory **4,83 ms**. Latency breach tương ứng **1 / 1 / 0**. Request rate tương ứng **2,51 / 5,02 / 4,91 req/s** | LIVE-19 |
| **Khoảng 02:11–02:12** | Incident worker phát các trạng thái **`ENRICHED` / `QUEUED` / `PUBLISHED`**; tạo incident có ID ghi bên dưới. Lifecycle đã quan sát **`RCA_READY` rồi `ESCALATED`**; không có timestamp giây riêng cho từng bước | LIVE-19 |
| **Trong thời gian theo dõi KB1; không có mốc giây riêng** | Controller in **`[]`** suốt thời gian quan sát; store **`/artifacts/remediation` có 0 file**. **Không có đề xuất remediation** | LIVE-19 |
| **02:14:08** | Gỡ lỗi, đặt lại fault `mode=healthy` | LIVE-19 |
| **Xác minh bổ sung 19/11/2026; không gán giờ đóng incident** | KB1 có **severity=`high`, state=`recovered`, lifecycle_stage=`CLOSED`, revision=`4`, affected_services=`[checkout-service, payment-service]`** | Xác minh live 19/11/2026 — worker `v2-store` |

Incident đã tạo, theo **LIVE-19**:

```text
INC-67897f56c9363e25d70174ab861fd1df7b38515504813d38fa66d9f5dc641399
```

### Kết quả incident KB1 đã xác minh

Các trường dưới đây thuộc đúng incident ID nêu trên, được đọc từ Postgres `sentinelops_control` bằng worker `v2-store`.

| Trường | Giá trị thực tế | Bằng chứng |
| --- | --- | --- |
| `severity` | `high` | Xác minh live 19/11/2026 |
| `state` | `recovered` | Xác minh live 19/11/2026 |
| `lifecycle_stage` | `CLOSED` | Xác minh live 19/11/2026 |
| `revision` | `4` | Xác minh live 19/11/2026 |
| `affected_services` | `[checkout-service, payment-service]` | Xác minh live 19/11/2026 |

**Severity `high` là ĐÚNG, không phải `medium`:** Payment chậm kéo Checkout vào `affected_services`; policy trả `high` khi Checkout bị ảnh hưởng **hoặc** có ít nhất hai service bị ảnh hưởng (`src/sentinelops/incidents/severity.py:75`). `medium` là nhánh dự phòng sau điều kiện này (`src/sentinelops/incidents/severity.py:77`), không phải nhãn suy ra chỉ từ việc tiêm latency vào một service. Giá trị `high` thực tế và hai service bị ảnh hưởng đã được **xác minh live 19/11/2026**.

### Bảng số đo đối chiếu

Mọi giá trị trong bảng là **LIVE-19**; thứ tự service được giữ nhất quán với timeline.

| Service | p95 baseline 02:04:28 (ms) | p95 khi lỗi 02:06:57 (ms) | Latency breach baseline → khi lỗi | Request rate 02:06:57 (req/s) |
| --- | ---: | ---: | --- | ---: |
| `checkout-service` | 46,37 | 968,08 | 0 → 1 | 2,51 |
| `payment-service` | 5,74 | 963,82 | 0 → 1 | 5,02 |
| `inventory-service` | 4,83 | 4,83 | 0 → 0 | 4,91 |

Operator ghi nhận lưu lượng **trên 150 request mỗi bucket** trong lượt này (**LIVE-19**). Các rate đã bàn giao phù hợp với mức đó khi quy đổi theo 60 giây, nhưng tài liệu chưa có bản đếm request và coverage từng bucket để kiểm chứng độc lập; không dùng phép quy đổi rate làm bằng chứng thay cho journal health.

### Kết luận KB1 và cách hiểu việc không có proposal

**Đã chứng minh được chuỗi lỗi thật → số liệu Grafana → incident worker → bản ghi sự cố cho UI**, với Payment/Checkout vượt ngưỡng và Inventory giữ p95 4,83 ms ở hai snapshot (**LIVE-19**). Kiểm tra bổ sung xác nhận KB1 đã `recovered`/`CLOSED` tại revision 4; **tại snapshot trước KB2/KB3**, Postgres có đúng **2 incident**, cả hai `high`/`recovered`, và UI không áp dụng bộ lọc liệt kê **2 sự cố** (**xác minh live 19/11/2026**). Đây không phải tổng số incident hiện tại. Chưa có timestamp riêng cho lúc incident lần đầu hiển thị trên màn hình UI.

**Không có đề xuất rollback ở KB1 là hành vi ĐÚNG theo điều kiện kịch bản.** `payment-bad-deploy-rollback` bắt buộc `root_service=payment-service`, **`deployment_change=true`** và `rca_abstained=false` (`configs/runbook_catalog.json:11`). Fault API chỉ cập nhật `FaultState` trong bộ nhớ (`services/payment/app.py:98`, `services/common/faults.py:31`); lượt tiêm này không đổi Deployment (**LIVE-19**), nên không cung cấp điều kiện deployment change để rollback.

Về hướng xử lý, KB1 thuộc nhánh **chuyển kỹ sư / runbook thủ công**. Catalog có `checkout-latency-downstream` với `executable=false`, yêu cầu không có deployment change và Checkout bị ảnh hưởng (`configs/runbook_catalog.json:80`). Matcher mặc định quyết định `engineer`, giữ quyết định này khi RCA abstain, và có reason `manual-runbook` khi match đủ điều kiện vào runbook không executable (`src/sentinelops/remediation/matching.py:223`, `src/sentinelops/remediation/matching.py:236`, `src/sentinelops/remediation/matching.py:244`). Controller chỉ bổ sung trường proposal khi decision là `oncall` (`src/sentinelops/remediation/controller.py:325`).

**Không gán một decision artifact `manual-runbook` cụ thể cho lượt này:** bằng chứng live là log `[]` và store 0 file, chưa có artifact quyết định để trích reason thực tế. Việc thiếu deployment change giải thích tại sao KB1 không đủ điều kiện rollback; các hạn chế RCA/H4 bên dưới vẫn phải được ghi riêng. Nguồn trạng thái store/log: **LIVE-19**; controller ghi artifact quyết định khi xử lý snapshot tại `src/sentinelops/remediation/controller.py:330`.

Mốc **02:14:08** xác nhận thao tác gỡ fault (**LIVE-19**). Kiểm tra bổ sung đã xác nhận **kết quả hồi phục/đóng incident** qua `state=recovered`, `lifecycle_stage=CLOSED`, `revision=4` (**xác minh live 19/11/2026**). Chưa có snapshot p95 sau gỡ lỗi hoặc timestamp đóng chính xác trong dữ liệu được ghi ở đây, nên chưa tính thời gian hồi phục; kết quả đóng incident cũng không phải bằng chứng đã rollback.

## 3. KB2 — Payment 503: ĐÃ CHẠY THẬT

| Thời điểm UTC ngày 19/11/2026 | Thao tác / quan sát thực tế | Số đo / incident / quyết định thực tế | Bằng chứng |
| --- | --- | --- | --- |
| **04:31:39.36** | Tiêm lỗi 503 vào Payment | Bắt đầu lượt KB2 | LIVE-19-FX3 |
| **04:31:58.60** | `slo_error_breach` của Payment và Checkout lên **1** | Sau **19,2 giây** kể từ lúc tiêm | LIVE-19-FX3 |
| **Trong lượt tiêm; không có mốc riêng cho từng snapshot** | Tỷ lệ lỗi tăng **5% → 13% → 29% → 45% → 61% → 78%** | Inventory giữ **0%** suốt thời gian quan sát | LIVE-19-FX3 |
| **04:36:35.26** | Incident **`PUBLISHED`**, khoảng **4,9 phút** sau tiêm | Severity **`critical`**; lifecycle đã quan sát **`OPEN` → `RCA_READY` → `ESCALATED`**, không gán giờ riêng cho từng bước | LIVE-19-FX3 |
| **04:36:40.64** | Gỡ lỗi | Store `/artifacts/remediation` có **0 file cả trước và sau** lượt chạy | LIVE-19-FX3 |

Incident KB2 (**LIVE-19-FX3**):

```text
INC-f9ce14a02f6a7f9851f87118081959861dd0caa4fbe2860d197134598f90b93f
```

**Bằng chứng cho severity `critical`:** Checkout có **2 bucket liền kề**, cả hai đều `error_rate=1.0`, `eligible=true`, `coverage=1.0`; `request_count` lần lượt **118,9** và **120**. Trace **`bb7d9431fbc64b1dbd8125f5ca4bb7f7`** có Payment trả **503** và Checkout trả **502**. Nguồn: **LIVE-19-FX3**.

KB2 đã chứng minh lỗi Payment lan tới Checkout và tạo incident `critical`; store remediation vẫn rỗng tại lượt KB2. Thao tác gỡ fault không phải bằng chứng rollback; chưa có số đo sau gỡ để tính thời gian hồi phục. Nguồn: **LIVE-19-FX3**.

## 4. KB3 — Deploy image regression: ĐÃ CHẠY THẬT

| Thời điểm UTC ngày 19/11/2026 | Thao tác / quan sát thực tế | Kết quả thực tế | Bằng chứng |
| --- | --- | --- | --- |
| **04:47:19.79** | Đổi image sang digest `sha256:766dd5f5a32eba9950c4854451f6f6891483d82df735ac93c7046597af7584e0` | Deployment generation **2 → 3** | LIVE-19-FX3 |
| **04:49:58.60** | Latency breach đầu tiên | Sau **158,8 giây** kể từ lúc đổi image | LIVE-19-FX3 |
| **04:53:31.78** | Có incident KB3 | Severity **`high`**, sau khoảng **372 giây** kể từ lúc đổi image | LIVE-19-FX3 |
| **04:54:36.75** | Incident **`ESCALATED`** | Reason: `source-verified RCA abstention; no action authority` | LIVE-19-FX3 |
| **05:01:20.60** | Khôi phục image gốc | Deployment generation **3 → 4** | LIVE-19-FX3 |
| **05:01:25.60** | Rollout image gốc hoàn tất | Chưa phải bằng chứng đã chạy chuỗi proposal → approval → executor rollback | LIVE-19-FX3 |

Incident KB3 (**LIVE-19-FX3**):

```text
INC-eddd71f93589069d11c665a05c8ba08b367dc6c953c190d0b74eed2ce4bec2db
```

**RCA abstained** với các reason **`insufficient-coverage`**, **`insufficient-score`**, **`unobserved-dependency-candidates`**; Payment có score **0,399024**, coverage **0,4**. Capture của KB3 là **v5**, **`deployment_changes=[]`**, feed **`/artifacts/deployment-changes` có 0 file**. Đổi image thật không tự sinh deployment-change receipt. Nguồn: **LIVE-19-FX3**; đường recorder đã được kiểm chứng ở B7, mục 5 và 8.

**KB3 đã chạy đến incident và escalation, chưa chạy được rollback.** Việc khôi phục image gốc và rollout hoàn tất là kết quả đã ghi nhận; riêng lượt KB3 không có proposal, approval, action được executor thực thi hoặc xác minh hồi phục nhờ rollback. B7 đã có proposal; coverage và phần còn lại được cập nhật tại mục 7–8.

## 5. B7 — Kịch bản thứ tư sau di trú epoch: ĐÃ CÓ ĐỀ XUẤT ĐẦU TIÊN

Lượt B7 chạy thật **09:47–09:56 UTC** dùng epoch mới có model và recorder deployment-change. Mọi số đo trong mục này lấy từ **LIVE-19-B7**; lịch sử tạo stream/consumer được ghi trong [tài liệu di trú epoch](EPOCH_MIGRATION_2026-11-19.md).

### Timeline và số đo

| Thời điểm UTC ngày 19/11/2026 | Thao tác / quan sát | Kết quả thực tế |
| --- | --- | --- |
| **09:48:50.005** | Mở baseline deployment-change | Snapshot `generation=4`, image gốc `sha256:e520e70255f4763de7c7ad4bb9053452e5b93ec0716fc5a5e1ea7a24caabb28c` |
| **09:48:57.029** | PATCH image regression | `sha256:766dd5f5a32eba9950c4854451f6f6891483d82df735ac93c7046597af7584e0`; generation **4 → 5** |
| **09:48:57.468** | Receipt `recorded_at` | Receipt đã được ghi, ref đầy đủ ở bảng dưới |
| **09:48:57.478** | `CHANGE_OBSERVED` | Khoảng cách từ PATCH theo số đo được bàn giao **0,438842 giây**, trong cửa sổ cho phép **2,75 giây**; baseline → after **7,462369 giây** |
| **Sau khi ghi receipt; không có mốc kiểm feed riêng** | Feed `/artifacts/deployment-changes` | **4 file: 2 snapshot + 1 baseline + 1 receipt** |
| **09:51:00** | Onset theo bucket health | Đây là onset của bucket, không phải timestamp mẫu Prometheus đầu tiên |
| **09:51:43.606** | Mẫu `slo_latency_breach=1` đầu tiên | **Payment và Checkout** đều breach latency; **không có error breach** |
| **09:55:30.908** | Tạo incident trong DB | Severity **`high`**, ID đầy đủ bên dưới |
| **09:55:30.913** | `OPEN` | Revision **1** |
| **09:55:32.987** | Capture `analyzed_at` | **`continuous-incident-evidence/v6`**, có **`model_evidence` với 3 signal `AVAILABLE`**; `deployment_changes` **không rỗng, chứa đúng 1 receipt** |
| **09:55:37.573** | `RCA_READY` | Revision **2**; `abstained=false`, `abstention_reasons=[]`; **Payment đứng đầu**: score **0,7974433166482036**, coverage **0,8**, margin **0,39658798795773287** |
| **09:55:51.010** | Đề xuất runbook đầu tiên được tạo | `decision=oncall`, `reason=oncall-approval-required` |
| **09:55:52.928** | `B7_EVIDENCE_PASS` | v6, model, receipt, RCA không abstain và proposal đã được chứng minh |
| **09:55:59.088** | Khôi phục image gốc thành công | Generation **5 → 6**, **Ready 1/1**; chưa phải rollback tự động theo đề xuất |
| **09:58:23** | Owner bật `--policy-enabled` cho controller | **Policy BẬT** |
| **09:58:36** | Rollout controller xong | Chưa có phê duyệt hoặc executor rollback |

### Incident và artifact B7

| Đối tượng | ID / ref đầy đủ |
| --- | --- |
| Incident | `INC-2bf8f369a77128a520a1e98848c9b0c7f631bae2a3033a2205337696b8b44aab` |
| Deployment-change receipt | `artifact://manifests/d9c8836592de0dce8a2d896dc41cefbf8636028e4282cbddbb613b0dccac84fa.json` |
| Capture v6 | `artifact://manifests/a597a3455c3185960210fb408acf88ccb70706217c04c06624d174292c5aa2d6.json` |
| Model release | `092290cedcea07578f664aa670eabf236f66ce7db27ebf159c2c386c3ee5216b` |
| RCA | `artifact://manifests/67ab23b2f27a92e27ac34821bc74ceef685565e443055dd9a0758555e049b1f4.json` |
| Proposal ID | `5b21bc47e39d4a94af9b965b4c3103cfc9e033a220edce2162995764f2b12931` |
| Proposal artifact | `artifact://manifests/cf56840e8ab07932b99ccaa14085855048e4de1c6ca29b8d92c11bc6c6d9ec3b.json` |

**Giới hạn kết quả:** đề xuất **CHƯA ĐƯỢC DUYỆT**, owner chưa có mã MFA nên chưa bấm **Duyệt** trên website; **rollback tự động CHƯA CHẠY**. TTL đề xuất là **15 phút**, hết hạn khoảng **10:10:52Z ngày 19/11/2026**. Phải chạy lại B7 để tạo đề xuất mới cho lượt duyệt/rollback; policy hiện **đang bật**. Ready 1/1 sau khôi phục image không chứng minh SLO đã hồi phục do một action của executor.

### Đối chiếu bốn kịch bản đã chạy thật

| Kịch bản | Cách gây lỗi / incident | Bằng chứng model và deployment-change | RCA | Đề xuất | Kết thúc lượt / rollback tự động |
| --- | --- | --- | --- | --- | --- |
| **KB1** — 02:04–02:14 | Fault API trễ Payment **950 ms**; severity **high** | Không đổi Deployment; không có bằng chứng capture v6/model gắn với incident trong số đo KB1 | Đã đến `RCA_READY` → `ESCALATED`; không gán score/coverage riêng khi chưa có số đo | **Không có**; remediation store **0 file** | Gỡ fault; sau đó xác minh `recovered`/`CLOSED`, revision 4; **không rollback tự động** |
| **KB2** — 04:31–04:36 | Fault API **503**; Payment/Checkout error breach sau **19,2 giây**; severity **critical** | Không có receipt hoặc capture v6/model được bàn giao cho lượt KB2 | `OPEN` → `RCA_READY` → `ESCALATED`; không gán score/coverage riêng khi chưa có số đo | **Không có**; remediation store **0 file trước và sau** | Gỡ fault 04:36:40.64; **không rollback tự động** |
| **KB3** — 04:47–05:01 | Deploy image regression; generation **2 → 3**; severity **high** | Capture **v5**, `deployment_changes=[]`; feed deployment-change **0 file** | **Abstain**; Payment score **0,399024**, coverage **0,40**; `ESCALATED` | **Không có** | Khôi phục image gốc, generation **3 → 4**; **không rollback tự động** |
| **B7** — 09:47–09:56 | Deploy cùng image regression với recorder; generation **4 → 5**; severity **high**, latency breach, không error breach | Capture **v6**, `model_evidence` **3 signal `AVAILABLE`**; **1 receipt trong capture**, feed **4 file** | **Không abstain**, reasons rỗng; Payment score **0,7974433166482036**, coverage **0,80**, margin **0,39658798795773287** | **Đề xuất đầu tiên**, `oncall` / `oncall-approval-required`; **chưa duyệt** | Khôi phục image gốc, generation **5 → 6**, Ready **1/1**; **rollback tự động chưa chạy** |

Nguồn đối chiếu: KB1 — **LIVE-19** và **xác minh live 19/11/2026**; KB2/KB3 — **LIVE-19-FX3**; B7 — **LIVE-19-B7**. Khác biệt đã đo giữa KB3 và B7 là đường bằng chứng sau di trú có model và receipt, coverage tăng từ **0,40 lên đúng 0,80**, RCA hết abstain và sinh proposal. **Đây là thành công của di trú và tạo đề xuất; phê duyệt/rollback còn phải kiểm chứng.**

## 6. Lỗi thật đã gặp và cách sửa

### 6.1. Sidecar thiếu CA cho asyncpg — ĐÃ SỬA, ĐÃ XÁC MINH

| Lỗi đã quan sát | Nguyên nhân xác định | Cách sửa đã thực hiện | Bằng chứng / giới hạn xác nhận |
| --- | --- | --- | --- |
| UI báo **`incident store unavailable`** | Sidecar **`operator-ui` thiếu env `PGSSLROOTCERT`**, khiến asyncpg không kết nối được Postgres | Đã thêm `PGSSLROOTCERT` cho đúng sidecar và rollout lại; lỗi kết nối store đã sửa xong | Lỗi/cách sửa: **LIVE-19**; kết quả kiểm tra sau sửa: **xác minh live 19/11/2026** |

| Kiểm tra sau sửa CA, trước khi nạp model | Kết quả tại snapshot cũ | Bằng chứng |
| --- | --- | --- |
| `GET /ready` qua port-forward | `dependencies.incident_store=true`, `dependencies.authentication=true`, `dependencies.model=false` | Xác minh live 19/11/2026 |
| HTTP của `/ready` qua port-forward | **503 chỉ vì `model=false`**; store và authentication đều `true` | Xác minh live 19/11/2026; logic tổng hợp dependency: `src/sentinelops/api.py:301` |
| Kết nối asyncpg → Postgres | Kết nối thành công qua **TLSv1.3** | Xác minh live 19/11/2026 |
| Database `sentinelops_control` | Tại snapshot trước KB2/KB3 có đúng **2 incident**; cả hai **severity=`high`, state=`recovered`** | Xác minh live 19/11/2026 |
| UI không áp dụng bộ lọc | Liệt kê **2 sự cố** tại cùng snapshot trước KB2/KB3 | Xác minh live 19/11/2026 |

Readiness tổng chỉ trả 200 khi **mọi** dependency đều `true`; nếu một dependency còn `false` thì trả 503 (`src/sentinelops/api.py:305`). Do đó, HTTP 503 trong snapshot cũ không phải bằng chứng incident store bị hỏng: trường `incident_store=true` và kết nối TLS đã xác nhận store hoạt động (**xác minh live 19/11/2026**). **Sau khi nạp model, backend `GET /ready` đã trả HTTP 200 với `dependencies.model=true`** (**LIVE-19-FX3**); bảng trên chỉ giữ lịch sử chẩn đoán CA.

Cấu hình sidecar trong source tại lúc đọc có `SSL_CERT_FILE` và mount CA ở `/etc/sentinelops/tls`, nhưng danh sách env chưa có `PGSSLROOTCERT` (`scripts/render_worker_manifests.py:356`, `scripts/render_worker_manifests.py:374`). Vì vậy, việc đã sửa env trên live không tự chứng minh renderer trong repo đã giữ được bản sửa. Cần kiểm tra và lưu cấu hình bền vững trước lần render/triển khai tiếp theo; đây là việc còn lại, không phải thay đổi được thực hiện khi soạn tài liệu này.

### 6.2. Lỗi đã ghi nhận: CloudFront chặn readiness, UI public hiển thị Unknown

| Nội dung | Kết quả / nguyên nhân | Bằng chứng |
| --- | --- | --- |
| Phản hồi public `/ready` | **HTTP 403**, header **`X-Cache: FunctionGeneratedResponse from cloudfront`** | Xác minh live 19/11/2026 |
| Nguyên nhân | Edge function chỉ cho qua `/ui`, `/v1/`, `/grafana`; `/ready` không nằm trong danh sách nên bị từ chối ngay tại CloudFront | `infra/aws/edge.tf:205`, `infra/aws/edge.tf:206` |
| Hậu quả trên UI public | Hai chấm trạng thái **Incident store** và **Model readiness** luôn hiện **`Unknown`** khi readiness bị chặn; chúng không hiển thị được dependency mà backend thực sự báo cáo | Xác minh live 19/11/2026; UI gọi readiness tại `src/sentinelops/ui/assets/client.js:236`, hiển thị dependency tại `src/sentinelops/ui/assets/app.js:265`, fallback `Unknown` tại `src/sentinelops/ui/assets/app.js:248` |
| Danh sách sự cố | Tại snapshot trước KB2/KB3, UI không lọc liệt kê **2 sự cố** qua `/v1/incidents`, đường được edge cho qua | Xác minh live 19/11/2026; `src/sentinelops/ui/assets/client.js:209`, `infra/aws/edge.tf:205` |

**Đây là lệch giữa UI và edge policy, không phải hỏng dữ liệu.** Backend qua port-forward đã xác nhận `incident_store=true`, `authentication=true`; model là `false` tại snapshot cũ (**xác minh live 19/11/2026**), sau đó đã thành **`true`**, backend `/ready` trả **HTTP 200** (**LIVE-19-FX3**). CloudFront 403 trong lần kiểm tra public khiến UI không đọc được dependency thực; chưa có bằng chứng mới xác nhận đã sửa edge.

**Cách sửa đề nghị: bổ sung `/ready` vào danh sách đường được phép trong edge function** (`infra/aws/edge.tf:205`). Khi triển khai bản sửa, cần đồng bộ cả `/ready/incidents` vì client gọi thêm endpoint này (`src/sentinelops/ui/assets/client.js:240`), và route readiness đến API ở ALB: danh sách `edge_paths.api` hiện cũng chưa có readiness (`infra/aws/edge.tf:10`, `infra/aws/edge.tf:161`), trong khi mặc định ALB trả 403 (`infra/aws/edge.tf:132`). Đây là đề nghị sửa; phiên cập nhật tài liệu không thay đổi edge policy. Sau sửa cần kiểm tra public readiness phản ánh đúng backend đã đo: **HTTP 200**, `incident_store=true`, `authentication=true`, **`model=true`** (**LIVE-19-FX3**); endpoint vẫn tổng hợp mọi dependency theo `src/sentinelops/api.py:305`.

## 7. Các chốt đã gỡ và rủi ro còn lại

### Model đã nạp thật

| Hạng mục | Trạng thái đã xác minh | Bằng chứng |
| --- | --- | --- |
| Bundle S3 | `s3://sentinelops-artifacts-dev-833925774773/models/alibaba-de38fc6b-20261114/` — **10 object, 4.429.363 byte** | LIVE-19-FX3 |
| Job / lưu trữ model | Job **`modelwire` Complete**; PVC **`model-de38fc6b` Bound, 4Gi, RWO** | LIVE-19-FX3 |
| Mount model | Controller và sidecar đều mount **`readOnly`** tại snapshot trước B7 | LIVE-19-FX3 |
| Model trong incident mới | Capture **v6**, có **`model_evidence` với 3 signal `AVAILABLE`** | LIVE-19-B7 |
| Readiness backend | `GET /ready`: **HTTP 200**, **`dependencies.model=true`**; trước khi nạp là 503 với `model=false` | LIVE-19-FX3 |
| Registry release | `092290cedcea07578f664aa670eabf236f66ce7db27ebf159c2c386c3ee5216b` | LIVE-19-FX3 |

### Coverage đã đúng dự đoán; score chỉ vừa vượt ngưỡng

Phép tính coverage dự đoán trước B7 (**LIVE-19-FX3**; trọng số/ngưỡng tại `configs/rca_policy.json:4`) đã được số đo **LIVE-19-B7** xác nhận:

| Evidence được thu nhận và chấp nhận | Phần coverage thêm | Coverage cộng dồn |
| --- | ---: | ---: |
| Trace | 0,25 | 0,25 |
| Log | 0,15 | **0,40** |
| Model | 0,25 | **0,65** |
| Receipt deployment-change | 0,15 | **0,80** |

Ngưỡng coverage cần **0,75** (`configs/rca_policy.json:12`). **Model một mình không đủ:** coverage chỉ lên **0,65**. Dự đoán **0,40 + model 0,25 + receipt 0,15 = 0,80** nay **đã đo đúng 0,80 ở B7**. KB3 vẫn giữ kết quả lịch sử coverage **0,40**, score Payment **0,399024** và ba reason abstain ở mục 4; B7 có **`abstained=false`, `abstention_reasons=[]`**. Coverage đủ không tự bảo đảm score đủ (`src/sentinelops/incidents/rca.py:120`).

| Gate RCA | Ngưỡng | B7 đo thực tế | Khoảng vượt ngưỡng |
| --- | ---: | ---: | ---: |
| Coverage | 0,75 | 0,80 | 0,05 |
| Score Payment | 0,75 | 0,7974433166482036 | **0,0474433166482036 ≈ 0,047** |
| Margin giữa các candidate | 0,15 | 0,39658798795773287 | 0,24658798795773287 |

**Biên score RẤT MỎNG:** score **0,7974** chỉ hơn ngưỡng 0,75 khoảng **0,047**, phụ thuộc điểm **Isolation Forest (IF)**. IF thấp hơn ở lượt tiếp theo có thể kéo score xuống dưới ngưỡng dù coverage vẫn 0,80. Không dùng margin **0,3966** giữa các candidate để suy rằng score có dư địa lớn; đó là hai đại lượng khác nhau. Bộ số đo bàn giao chưa nêu giá trị IF riêng của B7, nên không suy ngược IF từ tổng score. Phải kiểm lại các thành phần và gate cho từng lượt; không bảo đảm B7 chạy lại sẽ luôn sinh proposal.

**Rollback tự động CHƯA CHẠY; đề xuất đang ở bước cần owner phê duyệt.** Owner thiếu mã MFA, chưa bấm Duyệt. Policy đã bật nhưng không thay thế approval hoặc chứng minh executor đã được cấp `dispatch_authority` và đi qua đủ guard. Các chốt evidence cũ đã được B7 gỡ, không còn được mô tả như lỗi hiện tại:

| Chốt / trạng thái | Kết quả sau B7 | Nguồn / giới hạn |
| --- | --- | --- |
| **RCA từng abstain ở KB3** | B7 **không abstain**, reasons rỗng, vượt cả score/coverage/margin | LIVE-19-FX3 → LIVE-19-B7; biên score chỉ khoảng 0,047 |
| **Capture từng là v5, thiếu model** | B7 **v6**, `model_evidence` có **3 signal `AVAILABLE`** | LIVE-19-B7; `src/sentinelops/workers/model_signals.py:114` |
| **Consumer cũ đóng băng profile không model** | **Đã di trú epoch thành công**; consumer epoch mới sinh capture có model | B4/B7 trong [tài liệu di trú](EPOCH_MIGRATION_2026-11-19.md); không sửa identity cũ |
| **KB3 thiếu deployment-change receipt** | B7 recorder đã sinh receipt, feed **4 file**, capture chứa **đúng 1 receipt** | LIVE-19-B7; không hồi dựng receipt cho KB3 |
| **Chưa từng có proposal ở KB1–KB3** | B7 sinh proposal đầu tiên: **`oncall` / `oncall-approval-required`** | LIVE-19-B7; **chưa duyệt** vì owner chưa có MFA |
| **Policy controller** | **Đang BẬT**, owner bật 09:58:23, rollout xong 09:58:36 | LIVE-19-B7; bật policy không tự phê duyệt proposal |
| **Approval / executor rollback** | **Chưa duyệt, chưa chạy rollback tự động**; TTL 15 phút, hết hạn khoảng 10:10:52Z, phải chạy lại B7 sinh proposal mới | LIVE-19-B7; chưa chứng minh toàn bộ guard tại dispatch (`src/sentinelops/remediation/executor.py:66`) |
| **Khôi phục và SLO** | B7 đã trả image gốc, generation 6, Ready 1/1; chưa có số đo chứng minh hồi phục do executor rollback | LIVE-19-B7; giữ riêng các thiếu hụt số đo hồi phục của KB1/KB2 |
| **Readiness public từng bị edge chặn** | Giữ kết quả chẩn đoán lịch sử ở mục 6.2; số đo B7 không kèm lần kiểm public readiness mới | Xác minh live 19/11/2026; không suy từ B7 rằng edge đã thay đổi |

## 8. Việc còn lại

### Ba chốt evidence cũ đã được gỡ

Ước lượng **3–5 giờ** trong báo cáo LIVE-19-FX3 thuộc thời điểm trước di trú, không còn là danh sách công việc chưa làm. Kết quả B4/B7 đã xác nhận:

1. **Capture v6 và model — ĐÃ CHỨNG MINH:** B7 có `continuous-incident-evidence/v6`, `model_evidence` với 3 signal `AVAILABLE`.
2. **Health stream epoch mới — ĐÃ THÀNH CÔNG:** B4 tạo stream/consumer mới; B7 xác nhận đường bằng chứng trên incident mới. Không còn phải đổi profile trên stream cũ hoặc làm lại di trú.
3. **Recorder deployment-change — ĐÃ CHỨNG MINH:** B2 tìm đường hợp lệ qua **`Backend.deploy()` trong pod demo-runner**, gọi recorder mở baseline → PATCH → complete; B7 đã sinh receipt và ghép vào capture. Guard CLI `--in-cluster-worker` từ chối `open-change`/`complete-change` không phải chốt cần sửa để chạy đường này. Chi tiết B2/B7 trong [tài liệu di trú](EPOCH_MIGRATION_2026-11-19.md).

### Phê duyệt và kiểm chứng rollback còn lại

- **Owner cần mã MFA để duyệt trên website.** Proposal B7 chưa duyệt; TTL 15 phút, hết hạn khoảng **10:10:52Z**. Phải **chạy lại B7 để sinh proposal mới**, còn hạn tại thời điểm Duyệt; không dùng proposal/receipt cũ làm bằng chứng cho lượt mới.
- **Policy hiện đang bật.** Trước lượt B7 tiếp theo, đối chiếu baseline khỏe, known-good/ledger/approver, image/UID/generation, stream và quyền duyệt hiện tại. Số đo bàn giao chưa nêu ref/timestamp phê duyệt known-good mới, nên không suy thêm lịch sử approval từ việc có proposal.
- **Kiểm lại evidence và mọi gate trên incident mới**, nhất là score RCA và thành phần IF vì score RCA chỉ vượt ngưỡng khoảng **0,047** ở B7. Chỉ kết luận có proposal mới khi artifact thực tế xác nhận; không hạ ngưỡng hoặc ép authority. Có proposal chưa tự chứng minh executor có `dispatch_authority` tại thời điểm dispatch (`src/sentinelops/remediation/authority.py:62`, `src/sentinelops/remediation/authority.py:67`).
- **Ghi kết quả phê duyệt và action nếu thực sự chạy:** approval receipt, action ID, image trước/sau, generation, executor rollback và health/SLO hồi phục; nếu bị chặn thì ghi reason. B7 hiện chỉ chứng minh đến proposal và khôi phục image của lượt chạy, chưa chứng minh tự sửa (`src/sentinelops/remediation/authority.py:108`, `src/sentinelops/remediation/executor.py:59`).

### Các việc bổ sung từ báo cáo trước

- **Kiểm tra bản sửa `PGSSLROOTCERT` được giữ trong cấu hình triển khai**, tránh mất env của sidecar ở lần render tiếp theo. Đối chiếu kết quả sửa live **LIVE-19** với env renderer tại `scripts/render_worker_manifests.py:356`.
- **Bổ sung snapshot metric sau gỡ fault và timestamp đóng chính xác của KB1** khi có dữ liệu, để đo thời gian hồi phục. Trạng thái cuối `high`/`recovered`/`CLOSED`, revision 4 đã được **xác minh live 19/11/2026**; giữ nguồn live cho bằng chứng hiện tại, không dẫn artifact bị gitignore.
- **Đối chiếu tình trạng readiness public và bản sửa edge/route API** theo mục 6.2; kiểm `/ready` và `/ready/incidents` qua URL public để hai chấm trạng thái hiển thị dependency thực. Nguồn vấn đề public: **xác minh live 19/11/2026**; trạng thái backend sau nạp model: **LIVE-19-FX3**; B7 không cung cấp phép đo public readiness mới. Vị trí cần đối chiếu: `infra/aws/edge.tf:205`, `infra/aws/edge.tf:10`, `src/sentinelops/ui/assets/client.js:240`.
