# Kết quả demo SentinelOps — 19/11/2026

**Cả KB1, KB2 và KB3 đã chạy thật.** KB1 tiêm trễ Payment 950 ms, incident đã `high`/`recovered`/`CLOSED`, revision `4`. KB2 tiêm lỗi Payment 503, error breach xuất hiện sau **19,2 giây**, incident **`critical`**. KB3 đổi image thật, có incident **`high`** nhưng RCA abstain và sự cố được chuyển kỹ sư. **Model đã nạp, backend `/ready` trả HTTP 200 với `dependencies.model=true`; rollback thật vẫn CHƯA chạy.** Capture còn v5, thiếu deployment-change receipt; model một mình không đủ để RCA hết abstain hoặc H4 cấp quyền. Nguồn: **LIVE-19**, **xác minh live 19/11/2026** và **LIVE-19-FX3**, định nghĩa dưới đây.

## 1. Phương pháp và nguồn bằng chứng

- **LIVE-19** là kết quả đo/quan sát live ngày **19/11/2026 do operator cung cấp trong yêu cầu giao việc ban đầu**, gồm timeline KB1, số liệu Prometheus/Grafana, log worker/controller, trạng thái incident, thay đổi hạ tầng và chẩn đoán H4. Nhãn này không có nghĩa người soạn đã chạy lại kịch bản.
- **Xác minh live 19/11/2026** là kết quả kiểm tra trực tiếp bổ sung của worker **`v2-store`**: readiness qua port-forward, kết nối Postgres, snapshot incident và phản hồi CloudFront. Dùng nhãn này để dẫn bằng chứng live; không đưa đường dẫn artifact bị gitignore vào tài liệu. Phân biệt trạng thái cuối đã xác minh với các lifecycle trung gian trong timeline ban đầu.
- **LIVE-19-FX3** là kết quả đo thật bổ sung ngày **19/11/2026**, được bàn giao trong yêu cầu cập nhật tài liệu: timeline KB2/KB3, model đã nạp và readiness mới, coverage RCA, lưu trữ Prometheus và ba chốt còn lại. Nhãn này không có nghĩa người soạn đã chạy lại kịch bản. Snapshot cũ có 2 incident và `model=false` được giữ kèm bối cảnh lịch sử; chúng không mô tả trạng thái mới sau KB2/KB3 và nạp model.
- **`path:line`** là bằng chứng source/config trong workspace tại lúc soạn, dùng giải thích cách tính metric và điều kiện hệ thống; không thay thế bằng chứng một thao tác đã chạy trên EKS.
- Tất cả mốc KB1/KB2/KB3 dùng **UTC ngày 19/11/2026**. Khoảng **02:11–02:12** của KB1 chỉ chính xác đến phút; không gán timestamp giây riêng cho từng event, lifecycle hoặc controller poll. Nguồn: **LIVE-19**, **LIVE-19-FX3**.
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
| Request rate | Tổng `rate(sentinelops_http_requests_total[1m])` theo service, đơn vị req/s | `observability/rules/sentinelops-business.yml:7` |
| p95 latency | `histogram_quantile(0.95, sum by (service, le) (rate(sentinelops_http_request_duration_ms_milliseconds_bucket[5m])))`; histogram đã dùng ms, không nhân thêm 1000 | `observability/rules/sentinelops-business.yml:21` |
| `sentinelops:slo_latency_breach` | `1` khi p95 lớn hơn 750 ms, `0` khi có dữ liệu trong ngưỡng; lọc dữ liệu không hợp lệ trước so sánh | `observability/rules/sentinelops-business.yml:30` |
| Error ratio / error breach | Tỷ lệ 5xx theo cửa sổ 5 phút; ngưỡng error breach lớn hơn 0,05 | `observability/rules/sentinelops-business.yml:10`, `observability/rules/sentinelops-business.yml:36` |
| Health policy | Tối thiểu 30 request, coverage 0,8; 2 bucket vào trạng thái breach, 3 bucket để hồi phục | `configs/slo_health_policy.json:5` |

Recording rule mô tả cửa sổ metric hiện tại, không phải toàn bộ trạng thái SLO có lưu của health worker (`observability/rules/sentinelops-business.yml:2`). Request rate tại một snapshot cũng không thay thế bằng chứng eligibility/coverage của từng bucket; bảng KB1 ghi đúng rate đã được bàn giao và không suy thêm kết quả eligibility chưa có.

### Hạ tầng đã làm trong ngày

| Hạng mục | Kết quả đã bàn giao | Bằng chứng |
| --- | --- | --- |
| CloudFront | Đã bật distribution, có hai URL public thật nêu trên | LIVE-19 |
| Recording rules nghiệp vụ | Thêm **5** rule: request rate, error ratio, p95 latency, latency breach và error breach | `observability/rules/sentinelops-business.yml:7`, `observability/rules/sentinelops-business.yml:10`, `observability/rules/sentinelops-business.yml:21`, `observability/rules/sentinelops-business.yml:33`, `observability/rules/sentinelops-business.yml:36`; triển khai hôm nay: LIVE-19 |
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

KB2 đã chứng minh lỗi Payment lan tới Checkout và tạo incident `critical`; store remediation vẫn rỗng. Thao tác gỡ fault không phải bằng chứng rollback; chưa có số đo sau gỡ để tính thời gian hồi phục. Nguồn: **LIVE-19-FX3**.

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

**RCA abstained** với các reason **`insufficient-coverage`**, **`insufficient-score`**, **`unobserved-dependency-candidates`**; Payment có score **0,399024**, coverage **0,4**. Capture vẫn **v5**, **`deployment_changes=[]`**, feed **`/artifacts/deployment-changes` có 0 file**. Đổi image thật không tự sinh deployment-change receipt. Nguồn: **LIVE-19-FX3**; recorder được giải thích tại mục 7.

**KB3 đã chạy đến incident và escalation, chưa chạy được rollback.** Việc khôi phục image gốc và rollout hoàn tất là kết quả đã ghi nhận; chưa có bằng chứng proposal, approval, action được executor thực thi hoặc xác minh hồi phục nhờ rollback. Coverage và các chốt còn lại được ghi tại mục 6–7.

## 5. Lỗi thật đã gặp và cách sửa

### 5.1. Sidecar thiếu CA cho asyncpg — ĐÃ SỬA, ĐÃ XÁC MINH

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

### 5.2. Lỗi mới: CloudFront chặn readiness, UI public hiển thị Unknown

| Nội dung | Kết quả / nguyên nhân | Bằng chứng |
| --- | --- | --- |
| Phản hồi public `/ready` | **HTTP 403**, header **`X-Cache: FunctionGeneratedResponse from cloudfront`** | Xác minh live 19/11/2026 |
| Nguyên nhân | Edge function chỉ cho qua `/ui`, `/v1/`, `/grafana`; `/ready` không nằm trong danh sách nên bị từ chối ngay tại CloudFront | `infra/aws/edge.tf:205`, `infra/aws/edge.tf:206` |
| Hậu quả trên UI public | Hai chấm trạng thái **Incident store** và **Model readiness** luôn hiện **`Unknown`** khi readiness bị chặn; chúng không hiển thị được dependency mà backend thực sự báo cáo | Xác minh live 19/11/2026; UI gọi readiness tại `src/sentinelops/ui/assets/client.js:236`, hiển thị dependency tại `src/sentinelops/ui/assets/app.js:265`, fallback `Unknown` tại `src/sentinelops/ui/assets/app.js:248` |
| Danh sách sự cố | Tại snapshot trước KB2/KB3, UI không lọc liệt kê **2 sự cố** qua `/v1/incidents`, đường được edge cho qua | Xác minh live 19/11/2026; `src/sentinelops/ui/assets/client.js:209`, `infra/aws/edge.tf:205` |

**Đây là lệch giữa UI và edge policy, không phải hỏng dữ liệu.** Backend qua port-forward đã xác nhận `incident_store=true`, `authentication=true`; model là `false` tại snapshot cũ (**xác minh live 19/11/2026**), sau đó đã thành **`true`**, backend `/ready` trả **HTTP 200** (**LIVE-19-FX3**). CloudFront 403 trong lần kiểm tra public khiến UI không đọc được dependency thực; chưa có bằng chứng mới xác nhận đã sửa edge.

**Cách sửa đề nghị: bổ sung `/ready` vào danh sách đường được phép trong edge function** (`infra/aws/edge.tf:205`). Khi triển khai bản sửa, cần đồng bộ cả `/ready/incidents` vì client gọi thêm endpoint này (`src/sentinelops/ui/assets/client.js:240`), và route readiness đến API ở ALB: danh sách `edge_paths.api` hiện cũng chưa có readiness (`infra/aws/edge.tf:10`, `infra/aws/edge.tf:161`), trong khi mặc định ALB trả 403 (`infra/aws/edge.tf:132`). Đây là đề nghị sửa; phiên cập nhật tài liệu không thay đổi edge policy. Sau sửa cần kiểm tra public readiness phản ánh đúng backend đã đo: **HTTP 200**, `incident_store=true`, `authentication=true`, **`model=true`** (**LIVE-19-FX3**); endpoint vẫn tổng hợp mọi dependency theo `src/sentinelops/api.py:305`.

## 6. Hạn chế hiện tại

### Model đã nạp thật

| Hạng mục | Trạng thái đã xác minh | Bằng chứng |
| --- | --- | --- |
| Bundle S3 | `s3://sentinelops-artifacts-dev-833925774773/models/alibaba-de38fc6b-20261114/` — **10 object, 4.429.363 byte** | LIVE-19-FX3 |
| Job / lưu trữ model | Job **`modelwire` Complete**; PVC **`model-de38fc6b` Bound, 4Gi, RWO** | LIVE-19-FX3 |
| Mount model | Controller và sidecar đều mount **`readOnly`**; việc này chưa chứng minh incident worker đã có model evidence | LIVE-19-FX3 |
| Readiness backend | `GET /ready`: **HTTP 200**, **`dependencies.model=true`**; trước khi nạp là 503 với `model=false` | LIVE-19-FX3 |
| Registry release | `092290cedcea07578f664aa670eabf236f66ce7db27ebf159c2c386c3ee5216b` | LIVE-19-FX3 |

### Vì sao RCA vẫn abstain

Phép tính coverage đã truy được (**LIVE-19-FX3**; trọng số/ngưỡng tại `configs/rca_policy.json:4`):

| Evidence được thu nhận và chấp nhận | Phần coverage thêm | Coverage cộng dồn |
| --- | ---: | ---: |
| Trace | 0,25 | 0,25 |
| Log | 0,15 | **0,40** |
| Model | 0,25 | **0,65** |
| Receipt deployment-change | 0,15 | **0,80** |

Ngưỡng coverage cần **0,75** (`configs/rca_policy.json:12`). **Model một mình không đủ:** kể cả khi incident pipeline nhận model evidence, coverage mới đạt **0,65**, vẫn dưới ngưỡng. Mốc **0,80** chỉ là tổng khi có thêm receipt hợp lệ, chưa phải coverage đã đo của lượt KB3; lượt đó vẫn **0,40**, score Payment **0,399024**, với ba reason abstain nêu ở mục 4 (**LIVE-19-FX3**). Đạt coverage cũng chưa tự bảo đảm đạt score hay hết các reason abstain (`src/sentinelops/incidents/rca.py:120`).

**Rollback thật CHƯA chạy được vì H4 chưa cấp `dispatch_authority`.** Model ready không chứng minh RCA đủ điều kiện hoặc sắp thực thi được rollback. Các trạng thái dưới đây dùng **LIVE-19-FX3** để cập nhật dữ liệu mới; cột source giải thích hợp đồng kiểm tra, không khẳng định một action đã đi qua mọi guard.

| Hạn chế / trạng thái live hôm nay | Ý nghĩa đối với demo | Bằng chứng source hoặc nguồn live |
| --- | --- | --- |
| **RCA đang abstain** | KB3 có coverage 0,40 và score Payment 0,399024; lifecycle `RCA_READY` không đồng nghĩa RCA đủ điều kiện cấp quyền | LIVE-19-FX3; `src/sentinelops/remediation/authority.py:62` |
| **Capture hiện là v5, trong khi H4 cần v6** | Incident worker phải mount model và tạo evidence v6; model ready ở controller/sidecar chưa đáp ứng điều này | LIVE-19-FX3; `src/sentinelops/remediation/authority.py:67`, `src/sentinelops/workers/model_signals.py:114` |
| **Consumer live đã đóng băng profile không model** | Đổi profile trên stream cũ ném `ValueError: consumer configuration changed; explicit migration is required`; phải tạo **health stream epoch mới** | LIVE-19-FX3; `src/sentinelops/workers/incident_journal.py:71` |
| **Thiếu deployment-change receipt dù đã đổi image** | Capture `deployment_changes=[]`, feed `/artifacts/deployment-changes` 0 file; cần recorder riêng, không thể suy receipt từ image/generation | LIVE-19-FX3; `src/sentinelops/telemetry/deployment_changes.py:95` |
| **Chưa có authority đủ điều kiện** | KB3 escalation có reason `source-verified RCA abstention; no action authority`; chưa có bằng chứng executor chạy rollback | LIVE-19-FX3; `src/sentinelops/remediation/executor.py:66` |
| **KB1 không tạo deployment change** | Không dùng KB1 để chứng minh rollback sau deploy; cần một lượt có thay đổi Deployment thật và bằng chứng gắn đúng incident/target | LIVE-19; `configs/runbook_catalog.json:11`, `src/sentinelops/remediation/authority.py:108` |
| **Backend model đã ready, RCA vẫn bị chặn** | `/ready` đã trả HTTP 200 với `dependencies.model=true`; riêng model evidence chỉ nâng coverage từ 0,40 lên 0,65, dưới ngưỡng 0,75 | LIVE-19-FX3; `src/sentinelops/api.py:301`, `configs/rca_policy.json:12` |
| **Readiness bị edge chặn trên URL public** | UI hiển thị Incident store/Model readiness là `Unknown` dù danh sách sự cố vẫn hiển thị đúng; xem lỗi riêng ở mục 5.2 | Xác minh live 19/11/2026; `infra/aws/edge.tf:205` |
| **KB2/KB3 đã có kết quả; còn thiếu số đo hồi phục** | KB2 `critical`, KB3 `high`/`ESCALATED`; chưa có snapshot p95 sau gỡ fault của KB1 hoặc bằng chứng hồi phục do executor rollback | LIVE-19-FX3; KB1: LIVE-19 và xác minh live 19/11/2026 |

## 7. Việc còn lại

### Ba chốt còn lại của đường RCA/rollback

Theo **LIVE-19-FX3**, ước lượng xử lý ba chốt là **3–5 giờ**; đây là ước lượng công việc, không phải cam kết thời điểm chạy được rollback. Cả ba đều **chưa hoàn thành**:

1. **Capture v6:** incident worker phải mount model và đưa model evidence vào capture; controller/sidecar đã mount `readOnly` chưa đủ. Đổi profile trên stream cũ đang gặp `ValueError: consumer configuration changed; explicit migration is required` (`src/sentinelops/workers/incident_journal.py:71`, `src/sentinelops/workers/model_signals.py:114`).
2. **Health stream epoch mới:** tạo stream mới, gắn consumer có model ngay từ đầu, đồng bộ stream được chọn giữa health worker, incident worker và controller rồi kiểm tra identity/profile được chấp nhận (`src/sentinelops/workers/incident_journal.py:41`, `src/sentinelops/workers/incident_journal.py:69`).
3. **Recorder deployment-change riêng:** gọi **`DeploymentChangeReview.open/complete`** để ghi baseline trước đổi image và receipt sau đổi; đổi image không tự sinh receipt. CLI **`--in-cluster-worker` hiện từ chối `open-change`/`complete-change`**, chỉ cho `open`/`complete`/`collect` để thu image evidence (`scripts/review_payment_image.py:95`, `scripts/review_payment_image.py:129`; recorder: `src/sentinelops/telemetry/deployment_changes.py:114`, `src/sentinelops/telemetry/deployment_changes.py:131`).

### Kiểm chứng sau khi gỡ chốt và các việc bổ sung

- **Kiểm chứng evidence v6, coverage, score và các reason RCA**, rồi mới kiểm H4. Có model hoặc đạt coverage riêng lẻ không tự cấp `dispatch_authority` (`src/sentinelops/remediation/authority.py:62`, `src/sentinelops/remediation/authority.py:67`).
- **Chuẩn bị lượt kiểm chứng rollback sau KB3**, có known-good được phê duyệt trước thay đổi, receipt đúng image/generation/target và health stream; kiểm policy, kill switch và quyền duyệt. Ghi proposal, approval receipt, action ID, image trước/sau và kết quả xác minh nếu thực sự có action; nếu bị chặn thì ghi reason. KB2/KB3 đã có kết quả ở mục 3–4, không còn là kịch bản chưa chạy (`src/sentinelops/remediation/authority.py:108`, `src/sentinelops/remediation/executor.py:59`, `src/sentinelops/remediation/matching.py:220`).
- **Kiểm tra bản sửa `PGSSLROOTCERT` được giữ trong cấu hình triển khai**, tránh mất env của sidecar ở lần render tiếp theo. Đối chiếu kết quả sửa live **LIVE-19** với env renderer tại `scripts/render_worker_manifests.py:356`.
- **Bổ sung snapshot metric sau gỡ fault và timestamp đóng chính xác của KB1** khi có dữ liệu, để đo thời gian hồi phục. Trạng thái cuối `high`/`recovered`/`CLOSED`, revision 4 đã được **xác minh live 19/11/2026**; giữ nguồn live cho bằng chứng hiện tại, không dẫn artifact bị gitignore.
- **Sửa danh sách cho phép readiness tại edge function và đồng bộ route API**, theo mục 5.2; kiểm lại `/ready` và `/ready/incidents` qua URL public để hai chấm trạng thái hiển thị dependency thực, trong đó backend model nay là `true`. Nguồn vấn đề public: **xác minh live 19/11/2026**; trạng thái backend mới: **LIVE-19-FX3**; vị trí cần đối chiếu: `infra/aws/edge.tf:205`, `infra/aws/edge.tf:10`, `src/sentinelops/ui/assets/client.js:240`.
