# Grafana 12.2.0 trên EKS, sau CloudFront/Cognito

Renderer chạy offline, không gọi AWS, Terraform hay kubectl, không đọc credential
hoặc state. Các lệnh apply/kiểm tra cluster dưới đây dành cho operator thực hiện
sau; lane này chỉ render và kiểm thử local. Namespace dùng chung đã tồn tại:
`sentinelops-b-actions`. Tên Deployment/Service: `grafana`.

## Image và nguồn

Nguồn Docker Hub: `docker.io/grafana/grafana:12.2.0`, resolve ngày **2026-11-18**
bằng lệnh trong WSL Ubuntu:

```powershell
wsl.exe -d Ubuntu -- docker buildx imagetools inspect docker.io/grafana/grafana:12.2.0
```

| Manifest | Digest SHA-256 |
| --- | --- |
| Manifest list đa kiến trúc, được pin trong renderer | `74144189b38447facf737dfd0f3906e42e0776212bf575dc3334c3609183adf7` |
| `linux/amd64`, tương thích node m7i-flex.large | `60794dc17e6a58d28539cfd3dd496a7f2da62e13ff54a4055431385eec5d6639` |

Image phát ra là `docker.io/grafana/grafana:12.2.0@sha256:74144189b38447facf737dfd0f3906e42e0776212bf575dc3334c3609183adf7`.
Runtime chọn manifest con theo kiến trúc; tag không thể làm thay đổi digest đã pin.

## Render và giao diện với lane edge

Cài dependency từ repo (`python -m pip install '.[dev,ml]'`). Ví dụ Bash/WSL với
**giá trị minh họa**, thay bằng thông tin public do lane edge cấp:

```bash
CF_DOMAIN=demo.cloudfront.net
COGNITO_ISSUER=https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_Example
COGNITO_LOGIN=https://operators.auth.ap-southeast-1.amazoncognito.com
GRAFANA_CLIENT_ID=grafanaclient123
# Khớp edge_grafana_node_port trong infra/aws/variables.tf (mặc định 30300).
GRAFANA_NODE_PORT=30300

python scripts/aws_demo/render_grafana.py \
  --cloudfront-domain "$CF_DOMAIN" \
  --cognito-issuer "$COGNITO_ISSUER" \
  --authorize-url "$COGNITO_LOGIN/oauth2/authorize" \
  --token-url "$COGNITO_LOGIN/oauth2/token" \
  --userinfo-url "$COGNITO_LOGIN/oauth2/userInfo" \
  --client-id "$GRAFANA_CLIENT_ID" \
  --service-type NodePort --node-port "$GRAFANA_NODE_PORT" \
  --oauth-secret grafana-oauth --oauth-secret-key client-secret \
  --admin-secret grafana-admin --admin-password-key admin-password \
  --encryption-key-key secret-key \
  --output /tmp/grafana.yaml

/home/nvtank/.local/bin/kubeconform -strict -summary \
  -kubernetes-version 1.34.0 /tmp/grafana.yaml
```

`--output` chỉ ghi file mới; bỏ tùy chọn này để xuất YAML ra stdout. Renderer
không tự chạy kubeconform. Kubeconform có thể tải schema Kubernetes công khai
lần đầu; dùng cache/schema local đã chuẩn bị nếu cần ngắt mạng hoàn toàn.

- Mặc định `--service-type ClusterIP`, port `3000`, không có `nodePort`. Nếu ALB
  dùng target instance không qua controller, chọn `NodePort` và cấp port rõ ràng
  trong `30000..32767`. Không có giá trị port tự chọn ngầm. `externalTrafficPolicy:
  Cluster` cho phép cả hai node chuyển request tới replica duy nhất.
- Edge phải forward **giữ nguyên** `/grafana/` và `/grafana/*`, không thêm origin
  path `/grafana`, không strip prefix. `root_url=https://<domain>/grafana/`,
  `serve_from_sub_path=true`. Domain truyền vào là DNS hostname trần, không có
  scheme, port hay path; cũng chấp nhận hostname riêng do CloudFront phục vụ.
- Target group health check: HTTP, path **`/grafana/api/health`**, matcher `200`.
  Probes của Pod dùng cùng path. Chỉ mở NodePort từ SG của ALB nội bộ theo lane edge.
- CloudFront: tắt cache, forward cookie và query string (bao gồm OAuth `code`,
  `state`), các header cần cho auth và POST; cho phép các HTTP method Grafana
  cần. Không cache redirect hay `Set-Cookie`. Giữ public HTTPS origin nhất quán
  để cookie Secure và CSRF hoạt động. Không cần WebSocket: Grafana Live bị tắt.
- Cognito cần **app client riêng cho Grafana**, confidential client có secret,
  authorization code + PKCE, scope `openid email profile`. Callback chính xác:
  **`https://<domain>/grafana/login/generic_oauth`**. Không dùng public client của
  UI hoặc callback ALB `/oauth2/idpresponse` làm callback Grafana. User phải có
  email; MFA và hạn chế tạo user do lane edge/user pool quản lý.
- Giá trị public tương ứng từ lane edge: `edge_domain`,
  `operator_oidc_binding.issuer`, `operator_login_domain`,
  `edge_grafana_client_id`. App client `grafana` trong `operator_identity.tf`
  dùng `generate_secret=true`; lấy client secret từ kênh cấp phát riêng của
  operator, không đưa vào output public hoặc renderer. Không dùng secret của
  client khác. Renderer không sửa Terraform.
- Issuer là URL user pool `https://cognito-idp.<region>.amazonaws.com/<pool-id>`,
  khác hosted-login domain. Grafana 12.2.0 Generic OAuth không có cấu hình
  `issuer_url`/OIDC discovery: renderer dùng endpoint tường minh và điều kiện
  `iss` trong `role_attribute_path`, `role_attribute_strict=true`, chỉ cấp
  **Viewer**. Đây không phải bổ sung một bộ xác minh chữ ký JWT riêng.
  Đối chiếu [defaults v12.2.0](https://github.com/grafana/grafana/blob/v12.2.0/conf/defaults.ini)
  và [Generic OAuth](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/generic-oauth/).
- Grafana tự thực hiện flow Cognito Generic OAuth. Nếu edge cũng đặt
  `authenticate-cognito` trước Grafana, cần app client/callback ALB riêng và kiểm
  chứng cả hai flow; header ALB không tự đăng nhập Grafana. Không mở Prometheus,
  Jaeger hoặc port quản trị qua CloudFront.

## Secret qua stdin và apply (operator thực hiện sau)

Namespace phải tồn tại trước. Không truyền password/client secret bằng
`--from-literal`, argv, biến shell hoặc file trong repo. Chạy pipeline sau trong
terminal tương tác, **không bật `set -x`**. Python hỏi client secret qua terminal
ẩn ký tự, sinh mật khẩu admin và khóa mã hóa độc lập bằng CSPRNG 48 byte, rồi
đưa JSON trực tiếp vào stdin của kubectl:

```bash
python - <<'PY' | kubectl apply -f -
import base64
import getpass
import json
import secrets

client_secret = getpass.getpass('Cognito Grafana client secret: ')
if not client_secret:
    raise SystemExit('Client secret must not be empty')

def secret(name, data):
    return {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
            'metadata': {'name': name, 'namespace': 'sentinelops-b-actions'},
            'data': {key: base64.b64encode(value.encode()).decode() for key, value in data.items()}}

print(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': [
    secret('grafana-oauth', {'client-secret': client_secret}),
    secret('grafana-admin', {'admin-password': secrets.token_urlsafe(48),
                             'secret-key': secrets.token_urlsafe(48)}),
]}))
PY

kubectl apply -f /tmp/grafana.yaml
kubectl -n sentinelops-b-actions rollout status deployment/grafana --timeout=300s
kubectl -n sentinelops-b-actions get pods -l app=grafana
kubectl -n sentinelops-b-actions get service grafana
kubectl -n sentinelops-b-actions get pvc grafana-data
```

Pipeline Secret này dùng lúc bootstrap. Khi rotate client secret, chỉ cập nhật
`grafana-oauth`, giữ `grafana-admin.secret-key` ổn định. Không chạy lại pipeline
để đổi mật khẩu admin đang có: `GF_SECURITY_ADMIN_PASSWORD` chỉ bootstrap DB
lần đầu; DB trên PVC giữ mật khẩu hiện tại. Mật khẩu ngẫu nhiên được giữ trong
Secret, operator lấy qua kênh quản lý Secret khi cần khẩn cấp. Không đưa Secret
vào log/báo cáo. Sau khi đổi Secret, restart Pod để đọc lại env:

```bash
kubectl -n sentinelops-b-actions rollout restart deployment/grafana
```

Mặc định tắt anonymous, HTTP Basic và form local; tự chuyển tới Cognito.
`--emergency-admin-login` tạm bật HTTP Basic/form local và tắt OAuth auto-login,
vẫn tắt anonymous và local sign-up. Chỉ dùng trong cửa sổ khôi phục có kiểm soát,
qua đường quản trị riêng, dùng admin với password trong Secret; render/apply lại
không có flag để đóng sau khi xong. Renderer không phát ra mật khẩu mặc định.

## Storage, tài nguyên và provisioning

- Một replica, strategy `Recreate`: tránh hai process SQLite và lỗi multi-attach
  RWO. Có gián đoạn ngắn khi thay Pod. PVC `grafana-data`, **1Gi**, RWO,
  StorageClass **`sentinelops-gp3-kms`**. EBS CSI/StorageClass/KMS phải được chuẩn
  bị riêng. Replica có thể phải chờ node cùng AZ của EBS khi reschedule.
- `--storage empty-dir` dành cho demo không cần giữ session/DB; mất user/session
  khi thay Pod, dashboard được provision lại. Không dùng khi đổi từ PVC nếu cần
  giữ dữ liệu. Xóa Deployment không tự xóa PVC; teardown storage phải xử lý riêng.
- Request **100m CPU / 128Mi**, limit **500m / 384Mi**, không sidecar/init root.
  UID/GID/fsGroup 472, seccomp RuntimeDefault, bỏ ALL capabilities, không tự mount
  service-account token; root filesystem chỉ đọc. `/var/lib/grafana` và `/tmp`
  có volume ghi được; log ra console, tắt plugin preinstall, telemetry và alerting.
- Ba ConfigMap chứa INI, provisioning và dashboard gốc từ
  `observability/grafana/`. Chỉ thay URL datasource thành
  `http://prometheus.sentinelops-b-actions.svc.cluster.local:9090`; giữ UID
  `sentinelops-prometheus`, provider read-only và nguyên dashboard
  `sentinelops-workers.json`. SHA-256 ConfigMap trên Pod template làm rollout
  khi render/apply cấu hình hoặc dashboard mới. Không sửa file observability gốc.

## Kiểm tra sau triển khai (operator)

```bash
# Health path phải trả 200 qua edge (không có vòng redirect).
curl --fail --silent --show-error "https://$CF_DOMAIN/grafana/api/health"
# Trang dashboard phải yêu cầu Cognito khi chưa có session.
curl --silent --show-error --output /dev/null --dump-header - \
  "https://$CF_DOMAIN/grafana/d/sentinelops-workers/"
kubectl -n sentinelops-b-actions logs deployment/grafana --tail=80
```

Trong browser: đăng nhập Cognito + MFA, mở
`https://<domain>/grafana/d/sentinelops-workers/`, kiểm tra role Viewer, datasource
Prometheus và panel không lỗi 404/subpath. Khi chưa có telemetry, panel phải giữ
UNKNOWN/NOT DECIDED. Kiểm tra reload asset, POST truy vấn datasource, logout/login,
callback không vòng lặp và không cache session giữa hai user. Sau restart Pod,
session/DB phải còn nếu dùng PVC. Schema/test offline không xác nhận những điều
kiện runtime này.

## Kiểm thử offline

```bash
python -m pytest -q tests/test_aws_demo_grafana.py
# Render cả ClusterIP/NodePort và pvc/empty-dir với giá trị thử rồi kiểm schema:
/home/nvtank/.local/bin/kubeconform -strict -summary \
  -kubernetes-version 1.34.0 /tmp/grafana.yaml
python -m pytest -q
python scripts/validate_contracts.py
```

Chạy Python trong `sentinelops-tools:py311` bằng `docker run --rm` trong WSL,
mount repo `/source:ro`, copy sang `/tmp`, loại `infra/aws/terraform.tfstate*`,
`infra/aws/.terraform`, `.aws`, `.env*`, `.venv*`, `node_modules`; `chown -R
root:root` bản sao rồi `pip install '.[dev,ml]'`. Không mount Docker socket hay
credential vào container. Kubeconform binary WSL có thể mount read-only vào
`/usr/local/bin/kubeconform`. Không có thay đổi UI JS trong lane này.
