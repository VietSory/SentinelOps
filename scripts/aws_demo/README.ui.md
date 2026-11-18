# UI + API Cognito qua CloudFront

Quyết định mới của owner ngày 18/11 thay `f7244d2` cho demo: browser truy cập
CloudFront, đăng nhập Cognito Authorization Code + PKCE, API xác minh JWT OIDC.
Lane edge sở hữu CloudFront → VPC origin → ALB internal → NodePort. Lane UI chỉ
render manifest và kiểm thử offline; không gọi AWS/kubectl hoặc publish.

## Triển khai: giữ API trong pod controller

Approval phải ghi bền vào PVC RWO **của controller**. Không mount PVC này sang
một Deployment API khác vì pod có thể ở node khác và bị Multi-Attach. Lựa chọn
hiện tại giữ `operator-ui` trong pod `remediation-controller`, cùng volume
`remediation-artifacts`, cùng đường dẫn `/artifacts/remediation` với `--store`.
API chỉ ghi receipt qua `remediation.approvals.record_approval()`; controller
duy nhất thực thi runbook và kiểm lại mọi guard.

1. Lane edge chuẩn bị public client Cognito **không client secret**, grant `code`,
   callback chính xác `https://<edge_domain>/ui/callback`, MFA/enrollment theo
   runbook edge. Resource server `sentinelops` phải định nghĩa và app client phải
   được cấp `openid` và **cả** `sentinelops/incidents.read` và
   **`sentinelops/remediation.approve`**. Hai scope không được trùng nhau.
   Terraform định nghĩa đúng hai custom scope này; client Grafana chỉ nhận
   `openid email profile`, không có quyền API hoặc scope duyệt. Scope duyệt chỉ
   cho quyết định một proposal runbook đã được đề xuất, không cấp dispatch tùy ý;
   mọi guard executor vẫn áp dụng. Custom scope được cấp theo app client;
   không tự coi scope là phân quyền theo từng user/group. Chỉ enroll operator
   được phép duyệt vào client này hoặc thiết kế issuance riêng cho người chỉ đọc.
2. Dùng các output public `operator_oidc_binding` và `operator_login_domain`:

   Binding có `.operator_scope = sentinelops/incidents.read` và
   `.approval_scope = sentinelops/remediation.approve`. Truyền `.approval_scope`
   vào `--approval-scope`; renderer ghi đúng `SENTINELOPS_OIDC_APPROVAL_SCOPE`
   cho API (cùng `SENTINELOPS_OIDC_OPERATOR_SCOPE` cho quyền đọc). Giữ
   `.audience_claim = client_id` và `.audience` là ID public client UI.

   ```powershell
   python scripts/render_api_manifests.py --image <control-plane@sha256:digest> --environment aws-dev --control-secret control-runtime --issuer <issuer> --audience <public-client-id> --audience-claim client_id --jwks-url <jwks-url> --login-domain <https://cognito-domain> --approval-scope sentinelops/remediation.approve --controller-sidecar --service-type NodePort --node-port 30080 --output api-ui.yaml
   ```

   Chế độ `--controller-sidecar` chỉ sinh ConfigMap `sentinelops-api-runtime` và
   Service `sentinelops-api`, selector `app: remediation-controller`. Không tạo
   Deployment API mới hoặc gắn thêm PVC. Tham số `control-secret` dùng
   `control-runtime` đúng prerequisite của worker renderer.
3. Render worker bằng tham số đã ghim của môi trường và thêm `--demo-ui` vào
   `scripts/render_worker_manifests.py`. Flag mặc định tắt; khi bật sẽ thêm
   `operator-ui` dùng OIDC ConfigMap ở trên. Triển khai ConfigMap trước pod và
   apply/restart controller theo quy trình review AWS hiện hành. Sau khi đổi
   issuer/client/domain/scopes trong ConfigMap phải **rollout restart controller**:
   envFrom không tự reload, worker renderer không đọc nội dung ConfigMap để hash.
4. Sidecar bind `0.0.0.0:8000` trong pod, Service NodePort **30080** cho ALB target
   group `instance` của lane edge. `externalTrafficPolicy` mặc định Cluster cho
   phép node khác proxy về pod controller. Không tạo Service LoadBalancer hoặc
   Ingress. ALB/SG/VPC origin do Terraform edge kiểm soát; NodePort chỉ được mở
   từ SG của ALB, không mở world ingress.
   Nếu chuyển edge sang target `ip`, dùng `--service-type ClusterIP` và bỏ
   `--node-port`; không đổi type Service thủ công thành LoadBalancer.
5. Giữ distribution **disabled** trong bootstrap đến khi pod/NodePort healthy,
   Cognito callback/scope đã kiểm tra và luồng approve/reject hoạt động. Mở
   `https://<edge_domain>/ui/`, bấm **Đăng nhập Cognito**, hoàn tất MFA, rồi chọn
   incident để đọc IER/evidence/giải thích và duyệt proposal.

Renderer API mặc định vẫn có thể sinh Deployment riêng chỉ đọc với OIDC, không
mount approval PVC. Deployment đó **không** phải phương án ghi receipt cho RWO
store. Chỉ dùng nó cho đọc khi cần; đường demo duyệt dùng `--controller-sidecar`.

## Luồng đăng nhập và token

- `GET /ui/auth-config` công khai chỉ trả issuer, public client ID, Cognito domain,
  JWKS URL, scopes và callback path. Không trả DSN, Secret, token hoặc store path.
  CloudFront phải forward query/Authorization và tắt cache như cấu hình edge.
- Browser tạo verifier, state, nonce bằng CSPRNG; gửi challenge S256 tới
  `/oauth2/authorize`. Chỉ transaction PKCE nằm trong **sessionStorage**, tối đa
  10 phút và dùng một lần. Không có access/ID/refresh token trong storage.
- `/ui/callback` phục vụ cùng UI. JavaScript lấy code/state vào bộ nhớ và xóa query
  khỏi URL trước request cấu hình. Callback sai state, trùng tham số, quá hạn,
  đổi client/issuer/domain/redirect URI hoặc error đều bị chặn; không tự retry code.
- Browser POST trực tiếp `/oauth2/token` qua HTTPS với verifier, không client
  secret và không cookie API. ID token được xác minh RS256 bằng JWKS, kiểm
  issuer/client/nonce/time/subject. Access token được giữ trong private client
  của workspace, **chỉ trong RAM**; API mới là nơi xác minh chữ ký và cấp quyền
  cho từng request. Refresh token được bỏ, không silent refresh. Hết hạn hoặc
  Disconnect/pagehide sẽ bỏ token và dữ liệu riêng; refresh tab cần đăng nhập lại.
- Nút Disconnect chỉ quên phiên trong tab, không phải revoke token hoặc logout
  toàn bộ Cognito SSO. Hosted-login session có thể vẫn còn theo chính sách Cognito.
- CSP chỉ cho kết nối same-origin, đúng Cognito domain và origin issuer/JWKS đã
  cấu hình; không wildcard/unsafe-inline. API server không access-log URL callback
  có code. Cấu hình logging của edge cũng cần tránh ghi query OAuth vào log chia sẻ.

## Quyền đọc và quyền duyệt

- Mọi request protected vẫn cần bearer access token được OIDC verifier kiểm
  chữ ký, issuer, client_id, token_use=access, expiry và scope. Không tin header
  identity tùy ý của ALB/CloudFront hoặc ID token làm quyền API.
- GET proposal yêu cầu `sentinelops/incidents.read`. POST quyết định yêu cầu
  **cả read và `sentinelops/remediation.approve`**. Read hoặc producer scope một
  mình không đủ; thiếu approve trả `403 approval_scope_required`. UI khóa hai nút
  ở phiên chỉ đọc. Actor receipt là `oidc-approver:<verified-subject>`, do server
  suy ra; không nhận actor từ browser.
- EKS sidecar giữ `SENTINELOPS_ENV=aws-dev` (hoặc environment non-local đã chọn),
  `SENTINELOPS_ENABLE_DEMO=false`, bắt buộc OIDC + hosted-login config. **Không có
  token tĩnh/operator Secret** trong manifest; `api_settings` vẫn cấm static
  token ngoài local/test. Sidecar dừng nếu thiếu OIDC, có static credential hoặc
  chọn profile local. Không dùng Secret `remediation-ui` của phương án cũ.
- Sidecar không nhận action-writer DSN hoặc mount token Kubernetes. DB incident
  vẫn dùng `control-runtime/database-url` và password đã URL-encode, TLS CA mount.
  Repository hiện kiểm identity `control_writer`, rộng hơn SELECT-only ở DB;
  HTTP operator không được import/sửa IER. Compromise process vẫn là rủi ro của
  shared PVC và DB credential; giữ image digest/non-root/read-only rootfs đã review.

## Proposal và quyết định

Panel **Đề xuất runbook** hiển thị severity, RCA top-3, runbook/score/margin,
image hiện tại/rollback, hạn duyệt và đếm ngược, cùng hai nút **Duyệt/Từ chối**.
Hai trường `current_image`, `rollback_image` phải là registry image digest nằm
trong proposal bất biến do controller tạo. Producer hiện chưa xuất chúng;
proposal thiếu image chỉ đọc/từ chối được, approve bị chặn
`409 proposal_review_incomplete`. Không điền image từ client hoặc mutable lookup.

```http
POST /v1/runbook-proposals/{id}/decision
Authorization: Bearer <Cognito-access-token>
Content-Type: application/json
If-Match: "<proposal_sha256>"

{"decision":"reject","reason":"Cần engineer kiểm tra thêm"}
```

GET `/v1/runbook-proposals` và GET `/v1/runbook-proposals/{id}` trả hash proposal
để review; ETag không phải revision số của IER. Chỉ nhận `approve|reject` và reason
1–2000 ký tự; `approved_by`, `approved=true`, image/target do client gửi bị 422
(C-R03). Thiếu hash → 428, sai dạng → 422, khác hash → 412,
stale/expired/handled → 409. Receipt cùng định dạng CLI, create-only + fsync trên
đúng controller store; hai quyết định đồng thời chỉ một thắng. Mất reply thì đọc
lại, không tự POST lại. Reject → engineer; tick controller ghi
`escalated/approval-rejected` và không dispatch. Handoff này chưa có notification
hoặc hàng đợi engineer bên ngoài.

Approval không chứng minh đã rollback/recovered. Controller vẫn kiểm target,
revision/digest, H4, known-good, freshness 180 giây, kill switch và các guard khác.
TTL 15 phút không gia hạn evidence. Action-reader SELECT-only chưa cấu hình cho
sidecar; tab action có thể unavailable, không cấp action writer cho API để sửa nó.

## Port-forward để kiểm tra nội bộ

```powershell
kubectl -n sentinelops-b-actions port-forward deployment/remediation-controller --address 127.0.0.1 8000:8000
```

`http://127.0.0.1:8000/health` phục vụ debug. Đăng nhập browser qua port-forward
chỉ hoạt động nếu Cognito đã đăng ký **đúng callback localhost** tương ứng;
client edge mặc định chỉ đăng ký CloudFront. Không dùng token từ Secret để bypass
OIDC. Local/test phát triển vẫn có thể dùng token form trong UI profile manual,
không phải cấu hình EKS. Kết thúc demo thu hồi enrollment/client/runtime credentials
và teardown edge theo runbook, không để distribution/pod demo chạy ngoài phiên.

Nghiệm thu lane này là code/test offline; không thay receipt đăng nhập và
operator bấm thật trên EKS/CloudFront.
