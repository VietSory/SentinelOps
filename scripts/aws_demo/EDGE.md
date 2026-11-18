# Edge demo CloudFront → ALB nội bộ

Quyết định owner **nvtank, 18/11/2026** thay quyết định `f7244d2` trong phạm vi demo:
UI + API luôn chạy trên EKS và truy cập internet qua CloudFront, đăng nhập Cognito
bắt buộc, kèm Grafana. Lane edge chỉ cung cấp code và kiểm thử offline; các lệnh
ghi AWS dưới đây là runbook để người được giao triển khai sau review.

## Ranh giới và điều kiện trước khi publish

- `edge_enabled = false` mặc định; khi bật phải đặt `operator_identity_enabled = true`.
- CloudFront viewer chỉ HTTPS; origin dùng HTTP **trong VPC** qua VPC origin tới ALB
  `internal`, private subnets IPv4. IGW hiện có là yêu cầu của VPC origin, không
  tạo route internet cho ALB. Không có ALB public, DNS riêng hay certificate ACM
  mới. Dùng certificate mặc định cho `*.cloudfront.net`.
- ALB nhận cổng 80 chỉ từ `CloudFront-VPCOrigins-Service-SG` trong đúng VPC.
  Bootstrap lần đầu dùng tạm managed prefix list CloudFront origin-facing;
  distribution bị tắt suốt bootstrap. Không CIDR ingress vào ALB.
- Không cần AWS Load Balancer Controller. Hai target group `instance` trỏ NodePort
  API/UI `30080`, Grafana `30300`; Terraform gắn cả hai vào ASG của managed node
  group. Node mới tự được đăng ký qua ASG. Nếu thay node group, apply lại binding;
  cấu hình này yêu cầu node group có đúng một ASG, không chọn tùy ý một ASG.
- Service NodePort cần `externalTrafficPolicy: Cluster` để mọi node nhận được
  health check kể cả khi không có pod cục bộ. Selector chỉ chọn API/UI control
  plane hoặc Grafana ở `sentinelops-b-actions`, không chọn pod commerce.
  Health check lần lượt `/health` và `/grafana/api/health`, HTTP 200. Node security
  group thực tế là EKS cluster SG; chỉ thêm hai NodePort từ SG ALB.
- ALB mặc định 403; chỉ `/ui`, `/ui/*`, `/v1/*` tới API/UI và `/grafana`,
  `/grafana/*` tới Grafana. Rule deny ưu tiên 1 chặn các segment `internal`,
  `docs`, `redoc`, `openapi.json`, `metrics`, `prometheus`, `jaeger` kể cả dưới
  prefix được phép. CloudFront Function chặn thêm encoding `%`, backslash,
  `//`, `.`/`..` và các đường ngoài allowlist. Không public Prometheus/Jaeger,
  không proxy trực tiếp `/internal/faults` hay docs của service.
- Mọi behavior dùng managed `CachingDisabled` + `AllViewer`: forward Host,
  Authorization, cookies và query OAuth nguyên vẹn. Không cache API/UI/Grafana.
  Các mã lỗi CloudFront hỗ trợ cũng có error-cache TTL bằng 0; response policy
  trả `Cache-Control: no-store` để không lưu dữ liệu phiên trong browser cache.
  VPC origin không hỗ trợ WebSocket/gRPC: Grafana Live không dùng trong demo;
  dashboard dùng HTTP polling. Không có CloudFront/Lambda@Edge JWT authorizer.

**Điểm kiểm tra bắt buộc trước publish:** Terraform không cài pod hay cấu hình
OAuth của ứng dụng. Lane runtime/UI phải cung cấp callback `/ui/callback` thực
hiện code + PKCE S256, kiểm tra state/nonce; API kiểm JWT issuer/JWKS, `token_use`
và `client_id`, scope `sentinelops/incidents.read`, từ chối request thiếu token.
Resource server `sentinelops` định nghĩa đúng hai custom scope `incidents.read`
và `remediation.approve`. UI public client nhận đúng `openid`,
`sentinelops/incidents.read`, `sentinelops/remediation.approve`; quyết định proposal
cần cả read và approve. Scope approve chỉ duyệt một proposal runbook đã được đề
xuất, không cấp dispatch tùy ý; mọi guard executor vẫn áp dụng. Grafana chỉ nhận
`openid email profile`, không có scope đọc API hoặc `remediation.approve`.
Không dùng chế độ demo token/static token hoặc producer credentials ở edge.
Grafana tắt anonymous/basic login, bật generic OAuth + PKCE; datasource chỉ dùng
URL nội bộ của Prometheus/Jaeger. Nếu các điều kiện này chưa đạt, giữ distribution
trong trạng thái bootstrap disabled. Cognito callback resource tự lấy domain của
distribution, không cần tạo vòng phụ thuộc với ALB authenticate-cognito.

## Apply theo giai đoạn (không chạy trong lane offline)

Chạy tại `infra/aws` trên đúng local state đã apply EKS `sentinelops-dev` 1.34 ở
`ap-southeast-1`. Sao lưu state có quyền truy cập hạn chế; không đổi region,
backend hay các biến baseline hiện có. Thêm vào **file biến local đang dùng**:

```hcl
edge_enabled              = true
operator_identity_enabled = true
edge_vpc_origin_bootstrap = true
edge_waf_enabled          = false # tùy chọn; bật trước publish nếu cần
```

1. Bootstrap origin để AWS tạo service SG; targeted apply là ngoại lệ có chủ ý
   để tránh vòng bootstrap, không phải cách vận hành Terraform thường xuyên:

   ```sh
   terraform plan -var-file=demo.tfvars -target=aws_cloudfront_vpc_origin.edge -out=edge-origin.tfplan
   terraform apply edge-origin.tfplan
   ```

   Target kéo theo ALB/listener, IGW hiện có, ingress prefix list; **không** tạo
   public distribution hay enroll user. Nếu plan định thay EKS/VPC/RDS, dừng
   và kiểm tra file biến/state; không áp dụng một baseline mặc định mới.

2. Tạo distribution **disabled**, identity, NodePort/ASG binding. Giữ bootstrap
   `true`; plan toàn bộ bằng đúng file biến, review rồi apply:

   ```sh
   terraform plan -var-file=demo.tfvars -out=edge-disabled.tfplan
   terraform apply edge-disabled.tfplan
   terraform output -raw edge_domain
   terraform output -json operator_oidc_binding
   terraform output -raw operator_login_domain
   terraform output -raw edge_grafana_client_id
   ```

   Lấy `operator_oidc_binding.issuer`, `.jwks_url`, `.audience` (UI/API client ID),
   `.audience_claim` (`client_id`), `.operator_scope`, `.approval_scope`. Truyền
   `.approval_scope` (`sentinelops/remediation.approve`) vào renderer API qua
   `--approval-scope`; ConfigMap dùng `SENTINELOPS_OIDC_APPROVAL_SCOPE`, còn
   `.operator_scope` là `SENTINELOPS_OIDC_OPERATOR_SCOPE`. Issuer không phải hosted
   login domain. Pool ID là segment cuối issuer. `edge_grafana_client_id` là
   public ID riêng của **confidential client**, Grafana cần `use_pkce = true` và
   `auth_style = InHeader` (HTTP Basic tại token endpoint). Client UI public
   không có secret; chỉ client Grafana sinh secret nhạy cảm. Terraform không
   output secret, token/password và không tạo user. **Local state và bản backup
   chứa client secret Grafana**: hạn chế quyền file, không đưa vào git/log/artifact
   công khai. Operator lấy secret qua Cognito console trong phiên riêng rồi tạo
   Kubernetes Secret `grafana-oauth` key `client-secret` theo pipeline stdin ở
   [README.grafana.md](README.grafana.md#secret-qua-stdin-và-apply-operator-thực-hiện-sau);
   không in `describe-user-pool-client` ra transcript hay truyền secret bằng argv.

3. Triển khai NodePort Services + API/UI OIDC và Grafana, qua lane runtime.
   Kiểm thử qua private network/port-forward trước. Với domain lấy ở bước 2:

   ```ini
   [server]
   root_url = https://<cloudfront-domain>/grafana/
   serve_from_sub_path = true
   [auth.anonymous]
   enabled = false
   [auth.basic]
   enabled = false
   [auth]
   disable_login_form = true
   [auth.generic_oauth]
   enabled = true
   name = Cognito
   client_id = <edge_grafana_client_id>
   client_secret = $__env{GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET}
   scopes = openid email profile
   auth_url = <operator_login_domain>/oauth2/authorize
   token_url = <operator_login_domain>/oauth2/token
   api_url = <operator_login_domain>/oauth2/userInfo
   use_pkce = true
   auth_style = InHeader
   allow_sign_up = true
   role_attribute_path = 'Viewer'
   role_attribute_strict = true
   allow_assign_grafana_admin = false
   [security]
   cookie_secure = true
   cookie_samesite = lax
   ```

   Pool chỉ admin-create nên `allow_sign_up` ở Grafana chỉ provision user đã
   xác thực Cognito. Không ánh xạ người đăng nhập thành Admin. Không rewrite path
   `/grafana` ở ALB. Renderer Grafana 12.2.0 dùng confidential client + PKCE +
   InHeader và lấy secret từ `grafana-oauth`; không ghi giá trị secret vào YAML,
   Terraform output hay báo cáo. Rotate secret qua quy trình riêng, không đổi
   client UI thành confidential hoặc dùng chung client giữa hai ứng dụng.

4. Khi NodePort healthy và auth đã kiểm tra, đổi bootstrap về `false`. Có thể
   chuyển SG trước bằng target, sau đó publish bằng plan toàn bộ:

   ```sh
   terraform plan -var-file=demo.tfvars \
     -target=aws_vpc_security_group_ingress_rule.edge_from_cloudfront \
     -target=aws_vpc_security_group_ingress_rule.edge_bootstrap -out=edge-lock.tfplan
   terraform apply edge-lock.tfplan
   terraform plan -var-file=demo.tfvars -out=edge-publish.tfplan
   terraform apply edge-publish.tfplan
   ```

   Review thấy xóa prefix-list ingress, thay bằng SG origin-specific; mọi
   CIDR ingress vẫn vắng mặt. Distribution chỉ enable ở plan cuối. Kiểm tra
   HTTP viewer bị từ chối; HTTPS `/ui/` và `/grafana/` yêu cầu login; thiếu JWT
   `/v1/incidents` trả 401/403; docs/faults/Prometheus/Jaeger trả 403; ALB không
   truy cập được từ internet. Đăng nhập UI và Grafana hoàn thành TOTP.

## Tạo operator và MFA (người triển khai thực hiện)

Trong Cognito console chọn đúng pool từ issuer, **Create user** cho operator với
email được chủ repo xác nhận và temporary password mạnh >=16 ký tự; không đưa
password lên command line, Terraform, log hay git. Có thể dùng
`aws cognito-idp admin-create-user --cli-input-json file://<file-0600>` với nội
dung credential cấp qua kênh riêng, sau đó xóa file. Không gọi lệnh này trong
kiểm thử offline. Không bật self sign-up hay tắt `mfa_configuration = ON`.

Operator mở hosted login qua UI, đổi temporary password, quét QR trong app TOTP
và xác minh mã để hoàn thành enrollment. Pool bắt buộc software-token MFA;
không dùng SMS để tránh chi phí và cấu hình SNS. Thử đăng nhập lại với TOTP và
thử sai mã. Admin-only recovery: chủ repo xử lý reset MFA/disable user khi mất
thiết bị; không tạo account/password cố định cho demo.

## Ước tính chi phí tăng thêm

USD/ngày, tải demo thấp tại Singapore, chưa VAT/credit, là dự trù để kiểm tra
lại bảng giá khi triển khai; không phải báo giá hay số liệu Billing thực tế.

| Thành phần | Dự trù / ngày |
| --- | ---: |
| ALB khoảng $0.0252/giờ | $0.60 |
| 0–1 LCU khoảng $0.008/LCU-giờ | $0–0.20 |
| CloudFront: ví dụ 1 GB ra internet, 10k HTTPS requests | $0.15–0.25 |
| VPC origin, function vài chục nghìn lượt, Cognito vài operator TOTP | $0–0.02 ngoài quota/credit |
| WAF tùy chọn: ACL $5/tháng + 1 rule $1/tháng, ít request | khoảng $0.20–0.22 |
| Tổng edge, chưa WAF | khoảng $0.75–1.10 |

Grafana chạy trên node hiện có, không thêm node. Chưa gồm EKS/NAT/EC2/RDS baseline
(kế hoạch demo dự trù $10–12/ngày), log lưu trữ hay lưu lượng lớn. ALB internal
không có phí public IPv4. WAF regional rate-limit theo IP origin nên các viewer
có thể chung bucket qua CloudFront; đây là giới hạn tải origin, không cam kết
rate-limit chính xác từng viewer. Credit Free plan có thể từ chối tạo dịch vụ:
không chuyển sang ALB public để lách giới hạn.

## Teardown

1. Ngừng demo, disable user/revoke sessions, gỡ NodePort Services và cấu hình
   runtime sau khi đóng edge. Nếu cần đóng truy cập trước khi destroy, đặt
   bootstrap `true` và target **chỉ distribution** để disable; origin prefix
   ingress chỉ phục hồi nếu apply các resource bootstrap tương ứng.
2. Giữ identity để còn truy cập quản trị; đặt `edge_enabled = false`, bootstrap
   `false`, giữ `operator_identity_enabled = true` và cung cấp
   `operator_callback_urls` HTTPS quản trị còn hiệu lực (không dùng domain edge
   sắp xóa). Review plan toàn bộ với file biến hiện có rồi apply. Terraform xóa
   distribution trước VPC origin/ALB, detach target groups khỏi ASG, xóa rule
   NodePort và WAF nếu có; không xóa EKS/VPC/RDS. Đợi CloudFront deploy/delete
   hoàn tất, không xóa state hoặc ép xóa service-managed SG.
3. Nếu bỏ Cognito luôn: pool giữ `deletion_protection = ACTIVE`. Theo runbook
   identity, review một thay đổi riêng sang `INACTIVE` trước khi disable identity;
   không cố destroy pool đang ACTIVE. Không sửa/xóa baseline state để né bảo vệ.
4. Kiểm tra không còn ALB, target group, distribution, VPC origin, WAF ACL tính
   phí và không còn hai ingress NodePort. Tiếp tục checklist teardown baseline
   trong AWS_DEMO_PLAN để dọn tài nguyên còn lại.

## Kiểm tra offline

```sh
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
python scripts/check_aws_iac.py
pytest -q infra/aws/tests/test_edge_guards.py
node --test infra/aws/tests/edge_paths.test.cjs
pytest -q
python scripts/validate_contracts.py
```

`terraform test` chỉ dùng mock AWS/TLS provider và `command = plan`; không chạy
`terraform plan` độc lập, không refresh state và không gọi AWS. Container kiểm
thử phải copy source vào `/tmp`, loại `infra/aws/terraform.tfstate*`, `.terraform`
và credentials, chown root, cài `.[dev,ml]`. UI JS kiểm bằng `npm run test:ui`
khi lane UI thay đổi; edge không sửa UI assets.
