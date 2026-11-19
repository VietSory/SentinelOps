# Tư thế bảo mật SentinelOps — 19/11/2026

**Kết luận: có một rủi ro CAO có điều kiện cần xử lý ưu tiên: credential `payment-rollback` bị chiếm có thể được dùng để PATCH toàn bộ pod template của Deployment `payment-service`, không chỉ image.** Hệ thống đã public qua CloudFront và snapshot cuối xác nhận `--policy-enabled` **ĐANG BẬT**, file `KILL_SWITCH` không tồn tại. Đây là rủi ro của quyền đang có hiệu lực, không phải quyền dự kiến. Chưa có bằng chứng khai thác thành công, chiếm credential hay vượt Cognito.

Tài liệu tổng hợp rà soát chỉ đọc của s5 trên EKS `sentinelops-dev`, namespace `sentinelops-b-actions`, đối chiếu mã nguồn ở branch `feat/grafana-incident-dashboard`. Snapshot đầu ghi **09:57:32 UTC / 16:57:32 ICT**, snapshot cuối **10:02:41 UTC / 17:02:41 ICT ngày 19/11/2026**. Phép kiểm gồm cấu hình AWS/Kubernetes, quyền file và HTTP GET; không thử khai thác, POST hoặc PATCH. Số liệu live được ghi trực tiếp dưới đây; các tham chiếu `path:line` chỉ vị trí mã nguồn đối chiếu, không thay thế bằng chứng live.

**Chênh lệch cần đối soát:** thông tin giao việc nêu EKS API giới hạn **3 CIDR**, nhưng snapshot cuối s5 ghi **5 CIDR `/32`**, không có `0.0.0.0/0`. Chưa có bằng chứng mới hơn xác nhận đã thu về 3; tài liệu giữ số đo 5 và ghi rõ việc cần xác nhận, không trình bày 3 là kết quả s5 đã kiểm chứng.

## 1. CAO có điều kiện — quyền PATCH vượt phạm vi image

Role `payment-rollback` cho `get,patch` trên đúng Deployment `payment-service` trong `sentinelops-b-actions`; các quyền còn lại là đọc Pod, ReplicaSet và HPA. ClusterRole liên quan chỉ cho `get` hai namespace `kube-system`, `sentinelops-b-actions`. Không có quyền trực tiếp đọc Secret hay PATCH Deployment khác trong các role này. Cấu hình tương ứng: `infra/kubernetes/payment-rollback-rbac.yaml:21`, `infra/kubernetes/payment-rollback-rbac.yaml:54`.

**Điều kiện phát sinh:** kẻ tấn công chiếm được credential còn hiệu lực của `payment-rollback` và có đường truy cập Kubernetes API. RBAC giới hạn tên tài nguyên, nhưng không giới hạn trường được sửa. Snapshot admission không thấy chính sách giới hạn image-only cho `payment-service`; namespace cũng chưa có nhãn `pod-security.kubernetes.io/enforce`. Vì vậy, ngoài image, credential có thể sửa command/args, env, containers/initContainers, volumes, `serviceAccountName` và `securityContext` trong pod template.

Hệ quả có điều kiện gồm chạy mã trong workload Payment, gắn Secret hoặc ServiceAccount khác tồn tại trong cùng namespace, và nguy cơ tạo pod đặc quyền hoặc dùng `hostPath` khi không có lớp kiểm soát khác chặn. Quyền không trực tiếp đọc Secret không loại bỏ đường truy cập gián tiếp qua pod mới. Mức ảnh hưởng đến node hay tài nguyên khác còn phụ thuộc cấu hình thực tế; rà soát chưa chứng minh leo thang tới node hoặc toàn cluster.

`--policy-enabled` đang bật làm yêu cầu bảo vệ đường thực thi trở nên cấp thiết. Tuy nhiên, **tắt policy hoặc bật kill switch chỉ chặn luồng ứng dụng; không thu hồi quyền PATCH của credential bị chiếm**. Rủi ro dùng credential trực tiếp vẫn tồn tại nếu RBAC và admission chưa thay đổi.

Luồng hợp lệ đã có kiểm soát tốt: phê duyệt on-call, scope `sentinelops/remediation.approve`, TTL và dùng approval một lần; ràng buộc target theo UID; known-good ledger được duyệt trước incident; image cùng repository, pin SHA-256; dry-run rồi JSON Patch kiểm UID/resourceVersion/generation/image. Các kiểm soát này giảm lỗi trong luồng controller, không phải ranh giới bảo vệ Kubernetes API khi dùng credential trực tiếp. Đối chiếu `src/sentinelops/remediation/controller.py:222`, `src/sentinelops/remediation/domain.py:152`, `src/sentinelops/remediation/executor.py:63`, `src/sentinelops/remediation/executor.py:116`.

**Giảm thiểu cụ thể:** áp admission phía Kubernetes, giới hạn đúng danh tính `payment-rollback` và đúng Deployment; chỉ cho thay image của container `payment` bằng digest đã duyệt, từ chối mọi thay đổi ngoài trường cho phép. Phải khóa cả SA, volumes, env, command, securityContext, danh sách container và các trường Deployment ngoài phạm vi rollback. Nguồn digest được duyệt phải nằm ngoài quyền ghi của API/controller. Bổ sung Pod Security Admission mức **Restricted**, kiểm tương thích workload trước khi enforce. Hai lớp này bổ sung nhau: PSA không thay thế chính sách image-only. Khi nghi credential bị lộ, thu hồi quyền/credential liên quan và cấp lại sau xử lý; không chỉ dựa vào kill switch.

## 2. TRUNG BÌNH — chưa có WAF ở cả hai lớp

Snapshot CloudFront distribution `E2FPNNTBGXZE4C` trả **`WebACLId=""`**; truy vấn WebACL của ALB trả **`WebACL=null`**. Cả CloudFront lẫn ALB đều **không gắn WebACL**. Biến `edge_waf_enabled` trong mã hiện tạo WAF **REGIONAL gắn ALB**, không gắn CloudFront (`infra/aws/edge.tf:306`, `infra/aws/edge.tf:338`).

**Không gắn WAF KHÔNG đồng nghĩa vượt được Cognito.** API vẫn kiểm token; phép GET không token tới `/v1/incidents` trả 401. Khoảng trống là mất một lớp lọc trước ứng dụng đối với flood tầng HTTP, dò endpoint và khai thác lỗi trước xác thực; đường được cho qua vẫn có thể đến parser/auth/backend. CloudFront function giới hạn path không thay thế rate limiting hoặc managed rules của WAF. Mức đánh giá là **TRUNG BÌNH**, không phải bằng chứng bypass xác thực.

Ưu tiên gắn WebACL tại CloudFront với rate-based rules và managed rules phù hợp, theo dõi trước khi chuyển các rule dễ gây false positive sang chặn. Nếu chọn WAF tại ALB, xác định cách lấy viewer IP từ nguồn/header đáng tin cậy; rule hiện dùng `aggregate_key_type = "IP"` (`infra/aws/edge.tf:320`), cần tránh gom nhiều người dùng theo IP CloudFront. Phải đánh giá bảo vệ Cognito riêng, không mặc định WebACL của ứng dụng bao phủ cả endpoint đăng nhập Cognito.

## 3. Bề mặt HTTP public đã kiểm thật

GET qua `https://dr76usfhbe2w8.cloudfront.net`, **không bearer token, không cookie, không theo redirect**, trong lượt rà soát s5:

| Đường dẫn | HTTP thực đo | Diễn giải |
| --- | --- | --- |
| `/ui/` | **200** | Tải được giao diện public; không chứng minh đọc được dữ liệu incident. |
| `/v1/incidents` | **401** | Request đến được API nhưng cần token hợp lệ. |
| `/ready` | **403** | CloudFront function chặn; **lỗi chưa sửa** đối với readiness của UI. |
| `/metrics` | **403** | CloudFront function chặn. |
| `/internal` | **403** | CloudFront function chặn. |
| `/docs` | **403** | CloudFront function chặn. |
| `/openapi.json` | **403** | CloudFront function chặn. |
| `/grafana/` | **302** | Chuyển sang `/grafana/login`. |
| `/grafana/api/health` | **200** | Health Grafana hiện công khai. |
| `/grafana/api/search` | **401** | Truy vấn Grafana cần xác thực. |

Function LIVE chỉ cho qua `/ui`, `/ui/*`, `/v1/*`, `/grafana`, `/grafana/*`; riêng `/v1` không được cho qua. Function còn chặn các segment `internal`, `docs`, `redoc`, `openapi.json`, `metrics`, `prometheus`, `jaeger`, kể cả dưới prefix hợp lệ; chặn encoding/path mơ hồ và traversal. Cả bốn behavior dùng function và HTTPS-only. Mã đối chiếu: `infra/aws/edge.tf:193`, `infra/aws/edge.tf:254`, `infra/aws/edge.tf:267`. Được cho qua chỉ có nghĩa request có thể đi tiếp tới origin, không có nghĩa bỏ xác thực hay luôn trả 200.

**Lỗi CHƯA SỬA: `/ready` bị chặn làm hai chấm “Incident store” và “Model readiness” của UI luôn hiện `Unknown` ở trạng thái hiện tại.** UI gọi `/ready` và `/ready/incidents` (`src/sentinelops/ui/assets/client.js:234`); allowlist hiện thiếu readiness (`infra/aws/edge.tf:205`). Đây là lỗi tích hợp đường truy cập, không phải bằng chứng backend store/model bị hỏng.

**Cách sửa:** thêm `/ready` vào allowlist trong `infra/aws/edge.tf`, đồng thời cho phép chính xác `/ready/incidents` mà UI cần; chỉ thêm `/ready/actions` nếu luồng Actions cần. Đồng bộ `edge_paths.api` tại `infra/aws/edge.tf:10` và listener rule tại `infra/aws/edge.tf:161`, vì ALB mặc định cũng trả 403. Giữ các chặn internal/metrics/traversal và giữ xác thực cho readiness được bảo vệ. Sau triển khai, `/ready` phải phản ánh dependency thực: 200 khi tất cả sẵn sàng, 503 khi có dependency chưa sẵn sàng; `/ready/incidents` không token vẫn 401 và trả 200 với phiên hợp lệ khi store sẵn sàng (`src/sentinelops/api.py:293`, `src/sentinelops/api.py:311`). Các thay đổi này mới là khuyến nghị, chưa được thực hiện trong bản tài liệu.

## 4. TRUNG BÌNH — MFA của người dùng chưa hoàn tất

Cognito pool có **`MfaConfiguration=ON`**, chỉ admin được tạo user; mật khẩu tối thiểu 16 ký tự, đủ chữ hoa, chữ thường, số và ký hiệu. Cấu hình tương ứng ở `infra/aws/operator_identity.tf:29`.

Tuy nhiên, tài khoản **`nvtank`** đang `CONFIRMED`, `Enabled=true` nhưng **`UserMFASettingList=null`**, `PreferredMfaSetting=null`, `MFAOptions=null`. Tại snapshot này, tài khoản **chưa có MFA được đăng ký thực sự**; bật MFA ở pool không phải bằng chứng user đã enroll, cũng không phải bằng chứng đã vượt được MFA.

**Việc phải hoàn tất:** đăng ký và xác minh TOTP cho `nvtank`, kiểm metadata user đã có `SOFTWARE_TOKEN_MFA`, rồi thử một phiên đăng nhập mới phải hoàn thành thử thách MFA. Không hạ MFA của pool để vượt bước thiết lập. Không ghi mã TOTP, seed, password, token hoặc giá trị Secret vào tài liệu hay log kiểm tra.

Hai Cognito client operator/Grafana đã có `PreventUserExistenceErrors=ENABLED`, access/ID token **15 phút**, refresh token **8 giờ**, token revocation bật và OAuth code. Operator không có client secret, phù hợp SPA/PKCE; Grafana có client secret. Grafana tắt anonymous/basic/login form, dùng OAuth PKCE, mặc định Viewer và không cho tự nâng lên GrafanaAdmin. Các điểm này không thay thế việc hoàn tất MFA cho người dùng.

## 5. Những kiểm soát ĐẠT và giới hạn còn lại

| Kiểm soát | Kết quả s5 đã kiểm | Ý nghĩa và giới hạn |
| --- | --- | --- |
| Quyền file credential | Secret/projected credential volumes trong `sentinelops-b-actions` là **0440**; file credential được `stat` trong controller/health/incident worker là **0600**, không world-readable. | ĐẠT trong phạm vi đã kiểm. `/credentials` là directory 03777 nhưng main container mount read-only; không suy ra file 0600 đọc được bởi mọi UID. Đối chiếu `scripts/render_worker_manifests.py:204`, `scripts/render_worker_manifests.py:339`, `scripts/render_worker_manifests.py:351`. |
| S3 artifacts | **4/4 PublicAccessBlock=true**, `IsPublic=false`, **SSE-KMS**, BucketKey bật, versioning Enabled. Policy Deny `s3:*` khi `aws:SecureTransport=false`, áp cả bucket và object. | ĐẠT: bucket private, mã hóa KMS và bắt buộc TLS; `infra/aws/storage.tf:19`, `infra/aws/storage.tf:28`, `infra/aws/storage.tf:40`. |
| EKS API allowlist | Public/private endpoint đều bật; snapshot cuối là **5 CIDR `/32`**, không có `0.0.0.0/0`. Thông tin giao việc nêu **3 CIDR**, chưa có bằng chứng xác nhận mốc này. | ĐẠT về giới hạn nguồn truy cập, **chưa xác nhận đã giới hạn còn 3 CIDR**. Cần đối soát và bỏ IP tạm; `infra/aws/eks.tf:6`, `infra/aws/variables.tf:120`. Không đưa địa chỉ IP cá nhân vào báo cáo. |
| Phạm vi tài nguyên của `payment-rollback` | Quyền ghi chỉ `patch` đúng **một Deployment `payment-service`**; không có quyền ghi Deployment khác hay trực tiếp đọc Secret trong role đã kiểm. | ĐẠT ở mức tài nguyên; chưa đạt giới hạn trường image-only, như mục 1. `infra/kubernetes/payment-rollback-rbac.yaml:21`. |
| Model/review/evidence của controller | Các PVC model, review, health và incident evidence mount read-only; model release và health stream được pin. | Giảm khả năng controller sửa nguồn bằng chứng. PVC remediation vẫn cần bảo vệ riêng; `scripts/render_worker_manifests.py:345`, `scripts/render_worker_manifests.py:354`. |

Những phát hiện bổ sung của s5 cần giữ trong kế hoạch xử lý:

- **TRUNG BÌNH, có điều kiện — ranh giới API/controller:** `operator-ui` public và controller cùng pod, cùng UID 10001, cùng quyền ghi toàn PVC remediation; namespace chưa có NetworkPolicy. Nếu API bị RCE, có thể sửa approval/kill-switch/artifact trong vùng ghi chung dù API không mount token controller. Chưa chứng minh RCE. Cần tách pod/UID, thu hẹp vùng API được ghi, bảo vệ receipt/journal và thêm NetworkPolicy (`scripts/render_worker_manifests.py:356`, `scripts/render_worker_manifests.py:397`, `scripts/render_worker_manifests.py:419`).
- **TRUNG BÌNH — token observer dài hạn:** `incident-observer-credentials` có type `kubernetes.io/service-account-token`. Nếu bị lộ, thời gian sử dụng credential kéo dài; chuyển sang projected bound token TTL ngắn, có cơ chế xoay và đọc lại token, rồi thu hồi token tĩnh sau chuyển đổi.
- **THẤP — ingress ALB còn bootstrap:** ALB vẫn internal/VPC origin, nhưng SG cho TCP80 từ prefix-list CloudFront toàn cục. Cần chuyển sang `CloudFront-VPCOrigins-Service-SG` chuyên dụng như `infra/aws/edge.tf:42`; chưa có bằng chứng origin trực tiếp public.
- **THẤP — bề mặt thừa:** operator client còn callback `http://localhost:8000/ui/callback`, Grafana health public trả 200. Bỏ callback sau debug nếu không dùng; cân nhắc chặn health ở viewer, giữ health check ALB nội bộ.

Controller còn giới hạn một replica Payment, `maxSurge=0`, không HPA; dữ liệu RCA/health phải tươi tối đa 180 giây, change window 600 giây, cooldown 600 giây, xác minh tối đa 600 giây và quarantine khi không rõ kết quả. Có khóa target và giới hạn một dispatch/incident-target. Đây là kiểm soát thực thi, không bảo đảm không gián đoạn: thay pod Payment duy nhất có thể ảnh hưởng Payment/Checkout. Các giới hạn cố định đối chiếu tại `src/sentinelops/remediation/domain.py:72`.

## 6. Khuyến nghị theo thứ tự ưu tiên

Các mục dưới đây là việc cần làm, không phải thay đổi đã triển khai.

| Ưu tiên | Làm gì | Vì sao và tiêu chí xác nhận |
| --- | --- | --- |
| **1 — ngay** | Khóa quyền ở admission: đúng SA/Deployment, chỉ image của `payment` với digest đã duyệt; từ chối mọi sửa đổi ngoài phạm vi. Kiểm workload rồi enforce PSA Restricted. Trong thời gian chưa có lớp này, hạn chế thời gian cấp quyền thực thi; nếu cần cô lập credential phải thu hồi quyền/credential, không chỉ tắt policy. | Xử lý rủi ro CAO đang có hiệu lực. Kiểm ở môi trường thử nghiệm: đổi image hợp lệ được phép; đổi SA, Secret volume, env/command, securityContext, thêm container hoặc sửa trường khác phải bị từ chối ngay tại API server, kể cả dùng credential trực tiếp. |
| **2 — ngay** | Hoàn tất TOTP cho `nvtank`; xác minh metadata và đăng nhập mới có thử thách MFA. | Đưa yêu cầu MFA của pool thành bảo vệ thực tế cho người dùng; hoàn tất trước phiên phê duyệt remediation tiếp theo. |
| **3 — sớm** | Tách API public khỏi controller về pod/UID và quyền ghi; giới hạn API vào dữ liệu quyết định cần thiết, bảo vệ journal/receipt; thêm NetworkPolicy cho các kết nối cần dùng. | Giảm hậu quả có điều kiện của API RCE và đường sửa artifact chung. Kiểm API không ghi được vùng riêng controller; các luồng phê duyệt hợp lệ vẫn hoạt động. |
| **4 — sớm** | Gắn WAF tại CloudFront với rate limit/managed rules, kiểm false positive; nếu gắn ALB phải xử lý viewer IP đáng tin cậy. Đánh giá Cognito riêng. | Bổ sung lớp chống lạm dụng HTTP sau khi public. Xác minh WebACL thật sự gắn tài nguyên, rule/metric hoạt động, login/API/Grafana hợp lệ không bị chặn nhầm. |
| **5 — sớm** | Thêm `/ready` và đúng các readiness route cần dùng vào allowlist `infra/aws/edge.tf`, đồng bộ route ALB, cập nhật kiểm tra edge rồi triển khai. | Sửa lỗi hai chấm UI luôn `Unknown`, giúp operator quan sát đúng trạng thái. Kiểm JSON 200/503 theo backend; route bảo vệ vẫn 401 khi thiếu token; internal/metrics/docs vẫn 403. |
| **6 — tiếp theo** | Thay token observer tĩnh bằng projected bound token TTL ngắn, thực hiện xoay/đọc lại tự động và thu hồi bản cũ sau chuyển đổi. | Giảm thời gian credential bị lộ còn sử dụng được; kiểm observer tiếp tục chạy sau vòng xoay và token cũ không còn được chấp nhận. |
| **7 — tiếp theo** | Đối soát chênh lệch **3/5 CIDR** EKS bằng snapshot mới; gắn chủ sở hữu/hạn dùng và bỏ IP tạm. Chuyển SG ALB từ prefix-list bootstrap sang SG VPC origin; bỏ callback localhost và public Grafana health nếu không cần. | Thu hẹp bề mặt dư thừa mà không khóa đường quản trị hợp lệ. Chỉ ghi “3 CIDR đã xác nhận” sau khi có số đo mới; ưu tiên VPN/private endpoint khi phù hợp. |

Khi đóng từng mục, cần ghi thời điểm và kết quả kiểm mới; không dùng các biện pháp mới được đề xuất để hạ mức rủi ro của snapshot ngày 19/11/2026.
