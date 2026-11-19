# Kết quả demo SentinelOps — 19/11/2026

**KB1 đã chạy thật:** Payment bị tiêm trễ 950 ms, p95 Payment và Checkout vượt ngưỡng; Grafana có số liệu và incident worker đã tạo bản ghi sự cố cho UI. Xác minh bổ sung cho thấy incident có **severity `high`, state `recovered`, lifecycle `CLOSED`, revision `4`**, ảnh hưởng Checkout và Payment. **Không có đề xuất remediation** trong lượt này; đây là kết quả phù hợp với KB1 vì fault API không tạo deployment change mà runbook rollback yêu cầu. **KB2 và KB3: CHƯA CHẠY. Rollback thật: CHƯA thực hiện được.** Nguồn: **LIVE-19** và **xác minh live 19/11/2026**, định nghĩa dưới đây; điều kiện rollback: `configs/runbook_catalog.json:11`.

## 1. Phương pháp và nguồn bằng chứng

- **LIVE-19** là kết quả đo/quan sát live ngày **19/11/2026 do operator cung cấp trong yêu cầu giao việc ban đầu**, gồm timeline KB1, số liệu Prometheus/Grafana, log worker/controller, trạng thái incident, thay đổi hạ tầng và chẩn đoán H4. Nhãn này không có nghĩa người soạn đã chạy lại kịch bản.
- **Xác minh live 19/11/2026** là kết quả kiểm tra trực tiếp bổ sung của worker **`v2-store`**: readiness qua port-forward, kết nối Postgres, snapshot incident và phản hồi CloudFront. Dùng nhãn này để dẫn bằng chứng live; không đưa đường dẫn artifact bị gitignore vào tài liệu. Phân biệt trạng thái cuối đã xác minh với các lifecycle trung gian trong timeline ban đầu.
- **`path:line`** là bằng chứng source/config trong workspace tại lúc soạn, dùng giải thích cách tính metric và điều kiện hệ thống; không thay thế bằng chứng một thao tác đã chạy trên EKS.
- Tất cả mốc KB1 dùng **UTC ngày 19/11/2026**. Khoảng **02:11–02:12** chỉ chính xác đến phút; không gán timestamp giây riêng cho từng event, lifecycle hoặc controller poll. Nguồn: **LIVE-19**.
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

**Đã chứng minh được chuỗi lỗi thật → số liệu Grafana → incident worker → bản ghi sự cố cho UI**, với Payment/Checkout vượt ngưỡng và Inventory giữ p95 4,83 ms ở hai snapshot (**LIVE-19**). Kiểm tra bổ sung xác nhận KB1 đã `recovered`/`CLOSED` tại revision 4; Postgres có đúng **2 incident**, cả hai `high`/`recovered`, và UI không áp dụng bộ lọc sẽ liệt kê **2 sự cố** (**xác minh live 19/11/2026**). Chưa có timestamp riêng cho lúc incident lần đầu hiển thị trên màn hình UI.

**Không có đề xuất rollback ở KB1 là hành vi ĐÚNG theo điều kiện kịch bản.** `payment-bad-deploy-rollback` bắt buộc `root_service=payment-service`, **`deployment_change=true`** và `rca_abstained=false` (`configs/runbook_catalog.json:11`). Fault API chỉ cập nhật `FaultState` trong bộ nhớ (`services/payment/app.py:98`, `services/common/faults.py:31`); lượt tiêm này không đổi Deployment (**LIVE-19**), nên không cung cấp điều kiện deployment change để rollback.

Về hướng xử lý, KB1 thuộc nhánh **chuyển kỹ sư / runbook thủ công**. Catalog có `checkout-latency-downstream` với `executable=false`, yêu cầu không có deployment change và Checkout bị ảnh hưởng (`configs/runbook_catalog.json:80`). Matcher mặc định quyết định `engineer`, giữ quyết định này khi RCA abstain, và có reason `manual-runbook` khi match đủ điều kiện vào runbook không executable (`src/sentinelops/remediation/matching.py:223`, `src/sentinelops/remediation/matching.py:236`, `src/sentinelops/remediation/matching.py:244`). Controller chỉ bổ sung trường proposal khi decision là `oncall` (`src/sentinelops/remediation/controller.py:325`).

**Không gán một decision artifact `manual-runbook` cụ thể cho lượt này:** bằng chứng live là log `[]` và store 0 file, chưa có artifact quyết định để trích reason thực tế. Việc thiếu deployment change giải thích tại sao KB1 không đủ điều kiện rollback; các hạn chế RCA/H4 bên dưới vẫn phải được ghi riêng. Nguồn trạng thái store/log: **LIVE-19**; controller ghi artifact quyết định khi xử lý snapshot tại `src/sentinelops/remediation/controller.py:330`.

Mốc **02:14:08** xác nhận thao tác gỡ fault (**LIVE-19**). Kiểm tra bổ sung đã xác nhận **kết quả hồi phục/đóng incident** qua `state=recovered`, `lifecycle_stage=CLOSED`, `revision=4` (**xác minh live 19/11/2026**). Chưa có snapshot p95 sau gỡ lỗi hoặc timestamp đóng chính xác trong dữ liệu được ghi ở đây, nên chưa tính thời gian hồi phục; kết quả đóng incident cũng không phải bằng chứng đã rollback.

## 3. KB2 — CHƯA CHẠY

**CHƯA CHẠY — để trống, sẽ điền sau khi có kết quả thật.** Nguồn trạng thái: **LIVE-19**. Không dùng số liệu KB1 hoặc giá trị kỳ vọng làm kết quả KB2.

| Thời điểm UTC | Thao tác / quan sát thực tế | Số đo / incident / quyết định thực tế | Bằng chứng |
| --- | --- | --- | --- |
| | | | |
| | | | |
| | | | |

## 4. KB3 — CHƯA CHẠY

**CHƯA CHẠY — để trống, sẽ điền sau khi có kết quả thật.** Nguồn trạng thái: **LIVE-19**. Chưa có kết quả demo deploy lỗi → đề xuất → phê duyệt → rollback; tình trạng chặn chuẩn bị được ghi ở mục 6, không tính là KB3 đã chạy.

| Thời điểm UTC | Thao tác / quan sát thực tế | Proposal / approval / action / hồi phục thực tế | Bằng chứng |
| --- | --- | --- | --- |
| | | | |
| | | | |
| | | | |

## 5. Lỗi thật đã gặp và cách sửa

### 5.1. Sidecar thiếu CA cho asyncpg — ĐÃ SỬA, ĐÃ XÁC MINH

| Lỗi đã quan sát | Nguyên nhân xác định | Cách sửa đã thực hiện | Bằng chứng / giới hạn xác nhận |
| --- | --- | --- | --- |
| UI báo **`incident store unavailable`** | Sidecar **`operator-ui` thiếu env `PGSSLROOTCERT`**, khiến asyncpg không kết nối được Postgres | Đã thêm `PGSSLROOTCERT` cho đúng sidecar và rollout lại; lỗi kết nối store đã sửa xong | Lỗi/cách sửa: **LIVE-19**; kết quả kiểm tra sau sửa: **xác minh live 19/11/2026** |

| Kiểm tra sau sửa | Kết quả thực tế | Bằng chứng |
| --- | --- | --- |
| `GET /ready` qua port-forward | `dependencies.incident_store=true`, `dependencies.authentication=true`, `dependencies.model=false` | Xác minh live 19/11/2026 |
| HTTP của `/ready` qua port-forward | **503 chỉ vì `model=false`**; store và authentication đều `true` | Xác minh live 19/11/2026; logic tổng hợp dependency: `src/sentinelops/api.py:301` |
| Kết nối asyncpg → Postgres | Kết nối thành công qua **TLSv1.3** | Xác minh live 19/11/2026 |
| Database `sentinelops_control` | Đúng **2 incident**; cả hai **severity=`high`, state=`recovered`** | Xác minh live 19/11/2026 |
| UI không áp dụng bộ lọc | Liệt kê **2 sự cố** | Xác minh live 19/11/2026 |

Readiness tổng chỉ trả 200 khi **mọi** dependency đều `true`; nếu một dependency còn `false` thì trả 503 (`src/sentinelops/api.py:305`). Do đó, HTTP 503 trong lần kiểm tra này không phải bằng chứng incident store bị hỏng: trường `incident_store=true` và kết nối TLS đã xác nhận store hoạt động (**xác minh live 19/11/2026**).

Cấu hình sidecar trong source tại lúc đọc có `SSL_CERT_FILE` và mount CA ở `/etc/sentinelops/tls`, nhưng danh sách env chưa có `PGSSLROOTCERT` (`scripts/render_worker_manifests.py:356`, `scripts/render_worker_manifests.py:374`). Vì vậy, việc đã sửa env trên live không tự chứng minh renderer trong repo đã giữ được bản sửa. Cần kiểm tra và lưu cấu hình bền vững trước lần render/triển khai tiếp theo; đây là việc còn lại, không phải thay đổi được thực hiện khi soạn tài liệu này.

### 5.2. Lỗi mới: CloudFront chặn readiness, UI public hiển thị Unknown

| Nội dung | Kết quả / nguyên nhân | Bằng chứng |
| --- | --- | --- |
| Phản hồi public `/ready` | **HTTP 403**, header **`X-Cache: FunctionGeneratedResponse from cloudfront`** | Xác minh live 19/11/2026 |
| Nguyên nhân | Edge function chỉ cho qua `/ui`, `/v1/`, `/grafana`; `/ready` không nằm trong danh sách nên bị từ chối ngay tại CloudFront | `infra/aws/edge.tf:205`, `infra/aws/edge.tf:206` |
| Hậu quả trên UI public | Hai chấm trạng thái **Incident store** và **Model readiness** luôn hiện **`Unknown`** khi readiness bị chặn; chúng không hiển thị được dependency mà backend thực sự báo cáo | Xác minh live 19/11/2026; UI gọi readiness tại `src/sentinelops/ui/assets/client.js:236`, hiển thị dependency tại `src/sentinelops/ui/assets/app.js:265`, fallback `Unknown` tại `src/sentinelops/ui/assets/app.js:248` |
| Danh sách sự cố | Vẫn hiển thị đúng; UI không lọc sẽ liệt kê **2 sự cố** qua `/v1/incidents`, đường được edge cho qua | Xác minh live 19/11/2026; `src/sentinelops/ui/assets/client.js:209`, `infra/aws/edge.tf:205` |

**Đây là lệch giữa UI và edge policy, không phải hỏng dữ liệu.** Backend qua port-forward đã xác nhận `incident_store=true`, `authentication=true`; model hiện `false` chứ không phải `Unknown`. CloudFront 403 khiến UI không đọc được những giá trị này (**xác minh live 19/11/2026**).

**Cách sửa đề nghị: bổ sung `/ready` vào danh sách đường được phép trong edge function** (`infra/aws/edge.tf:205`). Khi triển khai bản sửa, cần đồng bộ cả `/ready/incidents` vì client gọi thêm endpoint này (`src/sentinelops/ui/assets/client.js:240`), và route readiness đến API ở ALB: danh sách `edge_paths.api` hiện cũng chưa có readiness (`infra/aws/edge.tf:10`, `infra/aws/edge.tf:161`), trong khi mặc định ALB trả 403 (`infra/aws/edge.tf:132`). Đây là đề nghị sửa; phiên cập nhật tài liệu không thay đổi edge policy. Sau sửa cần kiểm tra public readiness phản ánh đúng `incident_store=true` và `model=false`; HTTP 503 vẫn hợp lệ khi model chưa ready (`src/sentinelops/api.py:305`).

## 6. Hạn chế hiện tại

**Rollback thật CHƯA chạy được vì H4 chưa cấp `dispatch_authority`.** Các trạng thái live sau là **LIVE-19**; cột source giải thích hợp đồng kiểm tra, không khẳng định một action đã đi qua mọi guard.

| Hạn chế / trạng thái live hôm nay | Ý nghĩa đối với demo | Bằng chứng source hoặc nguồn live |
| --- | --- | --- |
| **RCA đang abstain** | Có lifecycle `RCA_READY` không đồng nghĩa RCA đã đủ điều kiện cấp quyền; authority trả reason `rca-abstained` khi result abstain | LIVE-19; `src/sentinelops/remediation/authority.py:62` |
| **Capture hiện là v5, trong khi H4 cần v6** | H4 yêu cầu `continuous-incident-evidence/v6` cùng model registry khả dụng khi xét deployment-change evidence | LIVE-19; `src/sentinelops/remediation/authority.py:67` |
| **Consumer live đã đóng băng profile không model** | Không thể chỉ thay profile tại chỗ rồi dùng lại consumer cũ; phương án chuẩn bị đã xác định là tạo health stream mới, gắn consumer có model ngay từ đầu | LIVE-19; identity gắn stream/profile tại `src/sentinelops/workers/incident_journal.py:41`, từ chối cấu hình khác tại `src/sentinelops/workers/incident_journal.py:69` |
| **Nạp model riêng cho controller không đủ** | Controller dùng lại evidence profile đã lưu của consumer; cần model evidence được tạo và chấp nhận từ incident pipeline, không chỉ model hiện diện ở controller | LIVE-19; `scripts/run_remediation_controller.py:103`; bước gắn model evidence nâng capture v5 → v6 tại `src/sentinelops/workers/model_signals.py:114` |
| **Chưa có authority đủ điều kiện** | Executor chặn trước hành động khi `dispatch_authority` không phải `true`; chưa có bằng chứng PATCH rollback hay xác minh hồi phục của rollback | LIVE-19; `src/sentinelops/remediation/executor.py:66` |
| **KB1 không tạo deployment change** | Không dùng KB1 để chứng minh rollback sau deploy; cần một lượt có thay đổi Deployment thật và bằng chứng gắn đúng incident/target | LIVE-19; `configs/runbook_catalog.json:11`, `src/sentinelops/remediation/authority.py:108` |
| **Backend model chưa ready** | `/ready` qua port-forward trả 503 chỉ vì `model=false`; store và authentication đều `true` | Xác minh live 19/11/2026; `src/sentinelops/api.py:301` |
| **Readiness bị edge chặn trên URL public** | UI hiển thị Incident store/Model readiness là `Unknown` dù danh sách sự cố vẫn hiển thị đúng; xem lỗi riêng ở mục 5.2 | Xác minh live 19/11/2026; `infra/aws/edge.tf:205` |
| **Chưa có kết quả KB2/KB3 và snapshot p95 sau gỡ fault** | KB1 đã xác minh `high`/`recovered`/`CLOSED`, revision 4; KB2/KB3 vẫn để trống. Chưa tính được thời gian hồi phục hoặc kết luận rollback thành công | LIVE-19 và xác minh live 19/11/2026 |

## 7. Việc còn lại

Các mục dưới đây là **việc phải làm tiếp**, chưa phải kết quả đã hoàn thành:

1. **Chuẩn bị health stream mới và consumer có model**, đồng bộ stream được chọn giữa health worker, incident worker và controller; kiểm tra identity/profile được chấp nhận. Consumer ID gắn với health stream và profile đã lưu (`src/sentinelops/workers/incident_journal.py:41`, `src/sentinelops/workers/incident_journal.py:69`).
2. **Kiểm chứng evidence v6 và RCA đủ điều kiện**, có model registry hợp lệ và không abstain trước khi kỳ vọng H4 cấp quyền. Không coi chỉ nạp model ở controller là hoàn tất bước này (`src/sentinelops/remediation/authority.py:62`, `src/sentinelops/remediation/authority.py:67`, `src/sentinelops/workers/model_signals.py:114`).
3. **Chuẩn bị KB3 bằng deployment change thật**, có known-good được phê duyệt trước thay đổi, image/generation/target và thời điểm change khớp incident; kiểm policy, kill switch và quyền duyệt trước thao tác. Điều kiện nằm ở `src/sentinelops/remediation/authority.py:108`, `src/sentinelops/remediation/executor.py:59`; proposal `oncall` vẫn cần phê duyệt theo `src/sentinelops/remediation/matching.py:220`.
4. **Kiểm tra bản sửa `PGSSLROOTCERT` được giữ trong cấu hình triển khai**, tránh mất env của sidecar ở lần render tiếp theo. Đối chiếu kết quả sửa live **LIVE-19** với env renderer tại `scripts/render_worker_manifests.py:356`.
5. **Chạy KB2 và KB3 sau khi đủ điều kiện**, rồi điền đúng bảng tương ứng bằng dữ liệu live: timestamp UTC, fault/deployment change, metric, incident ID/revision, reason của decision/H4; nếu có action thì thêm proposal, approval receipt, action ID, image trước/sau và kết quả xác minh. Nếu bị chặn, ghi nguyên reason và bước bị chặn; không ghi rollback thành công khi chưa có bằng chứng.
6. **Bổ sung snapshot metric sau gỡ fault và timestamp đóng chính xác của KB1** khi có dữ liệu, để đo thời gian hồi phục. Trạng thái cuối `high`/`recovered`/`CLOSED`, revision 4 đã được **xác minh live 19/11/2026**; giữ nguồn live cho bằng chứng hiện tại, không dẫn artifact bị gitignore.
7. **Sửa danh sách cho phép readiness tại edge function và đồng bộ route API**, theo mục 5.2; kiểm lại `/ready` và `/ready/incidents` qua URL public để hai chấm trạng thái hiển thị dependency thực. Nguồn vấn đề: **xác minh live 19/11/2026**; vị trí cần đối chiếu: `infra/aws/edge.tf:205`, `infra/aws/edge.tf:10`, `src/sentinelops/ui/assets/client.js:240`.
