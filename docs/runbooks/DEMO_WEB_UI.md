# Demo qua trang web operator

> **[LIVE] UI đã deploy và phục vụ qua port-forward:** xác minh live ngày **18/11/2026**, `GET /ui/` trả 200, `GET /health` trả `status=ok`, `GET /ui/auth-config` trả `mode=oidc`; `GET /v1/incidents` không token trả 401.
> **Snapshot 18/11, đã lỗi thời: từ 19/11 operator `nvtank` đã enroll TOTP và CloudFront đang phục vụ `https://dr76usfhbe2w8.cloudfront.net/ui/` (HTTP 200), xem mục 2a.** 18/11: đăng nhập UI chưa hoàn tất (chưa đặt mật khẩu permanent và enroll MFA TOTP), CloudFront `Enabled=false` (xác minh live ngày 18/11/2026 qua Cognito `admin-get-user`/`describe-user-pool` và CloudFront `get-distribution`).
> **Snapshot 18/11, đã lỗi thời: nay policy đang BẬT, xem [mục 2a](#2a-trạng-thái-live-hiện-hành-kiểm-19112026-tối-và-thứ-tự-chạy-trên-dev-833925774773).** 18/11: policy tắt, bấm Duyệt chưa chạy rollback; chưa có incident để hiển thị. Xác minh live ngày 18/11/2026: `kubectl get deployment remediation-controller` (kiểm args) và `kubectl logs deployment/remediation-controller -c remediation-controller` (poll trả `[]`) với context `sentinelops-demo`, namespace `sentinelops-b-actions`; lệnh đầy đủ ở mục 2 và 5.

## 1. Phạm vi bằng chứng

Tài liệu được tổng hợp từ đối chiếu source, báo cáo trong `docs/delivery` và kết quả xác minh live ngày 18/11/2026. Các ghi chú [LIVE] chép lại kết quả phiên đó kèm lệnh/endpoint đối chiếu; lần sửa tài liệu này không chạy lại triển khai.

| Mức | Ý nghĩa và giới hạn |
| --- | --- |
| [A] | Có bằng chứng lịch sử đã apply/chạy AWS; không xác nhận trạng thái hôm nay. Hạ tầng và demo phát hiện Payment được báo cáo tại `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:91`. |
| [B] | Có IaC/manifest nhưng chưa có bằng chứng apply cho cấu hình cụ thể; renderer/plan riêng không chứng minh đã triển khai. Không còn xếp controller, sidecar/API/UI, Cognito, edge hay Grafana vào [B] sau xác minh [LIVE] bên dưới. `scripts/aws_demo/README.ui.md:5`, `scripts/aws_demo/README.ui.md:156`, `scripts/aws_demo/EDGE.md:149`. |
| [C] | Tooling/mã/local: renderer, driver, CLI duyệt; không chứng minh vòng AWS thành công. `scripts/run_runbook_demo.py:76`, `scripts/approve_runbook.py:23`. |
| [LIVE] | Đã xác minh trực tiếp trên cluster/AWS ngày 18/11/2026, kèm lệnh/endpoint tại từng mục. **Mạnh hơn [A] vì [A] chỉ là báo cáo lịch sử**; chỉ xác nhận điều đã kiểm tra, không suy ra đăng nhập hay rollback thành công từ việc pod chạy. |

**Cạm bẫy tên:** Terraform mặc định `environment=prod`, nhưng đợt triển khai được báo cáo là `dev`, cluster `sentinelops-dev`, RDS `sentinelops-control-dev`.
Không lấy tên suy từ mặc định IaC làm tên live; runtime demo dùng `aws-dev`, fault API dùng `DEPLOYMENT_ENVIRONMENT=dev`.
Căn cứ: `infra/aws/variables.tf:32`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:9`, `scripts/aws_demo/README.ui.md:99`, `services/common/faults.py:17`.

Các khối lệnh dưới đây dành cho operator kiểm tra/chạy tiếp ở môi trường đã chuẩn bị; kết quả đã xác minh ngày 18/11/2026 được ghi riêng bằng [LIVE]. Không thay placeholder bằng giá trị đoán.

## 2. Checklist ĐIỀU KIỆN TIÊN QUYẾT

- [x] [LIVE] `remediation-controller` cùng sidecar `operator-ui` đã deploy trong `sentinelops-b-actions`, pod 2/2 Running, 0 restart, cùng PVC `remediation-artifacts`. Xác minh live ngày 18/11/2026: `kubectl --context sentinelops-demo -n sentinelops-b-actions get deployment remediation-controller` và `kubectl --context sentinelops-demo -n sentinelops-b-actions get pods -l app=remediation-controller`.
- [ ] [C] Trước demo rollback, kiểm lại action-runtime/control_action_writer, known-good và policy. [LIVE 18/11, đã lỗi thời; nay policy BẬT, xem mục 2a] Policy khi đó **tắt**: args Deployment không có `--policy-enabled`; không dispatch dù có receipt duyệt. Xác minh live ngày 18/11/2026: `kubectl --context sentinelops-demo -n sentinelops-b-actions get deployment remediation-controller -o jsonpath='{.spec.template.spec.containers[?(@.name=="remediation-controller")].args}'`; yêu cầu runtime: `scripts/render_worker_manifests.py:277`, `scripts/run_remediation_controller.py:59`, `scripts/run_remediation_controller.py:51`.
- [ ] [B/C] Nạp model/release được review, thỏa H4; không dùng bundle local làm chứng cứ AWS đã sẵn sàng. `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162`, `src/sentinelops/remediation/authority.py:65`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:17`.
- [ ] [C] Health stream, environment, policy và artifact-root binding đúng; health/review/change/remediation stores đọc đúng dữ liệu controller, target chứa đúng cluster/namespace/deployment UID. `scripts/run_runbook_demo.py:426`, `scripts/run_runbook_demo.py:433`, `scripts/run_runbook_demo.py:436`.
- [ ] [C] Payment đang healthy ở known-good; review open/complete đạt `AWAITING_OPERATOR_REVIEW`, owner duyệt known-good **TRƯỚC khi tiêm lỗi**. Không tự gán PASS. `docs/runbooks/CONTROLLED_REMEDIATION.md:76`, `docs/runbooks/CONTROLLED_REMEDIATION.md:118`, `scripts/aws_demo/README.review.md:36`.
- [ ] [C] Credential tách biệt: deploy PATCH, observer đọc, fault-token riêng cho verify lỗi; action reader SELECT-only, DB `sentinelops_control`, TLS `sslmode=verify-full`. `scripts/run_runbook_demo.py:420`, `scripts/run_runbook_demo.py:442`, `scripts/run_runbook_demo.py:445`.
- [ ] [C] Kill switch dùng chung với controller; đường normal phải không kích hoạt, người vận hành biết cách bật để dừng. `scripts/run_runbook_demo.py:296`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:17`.
- [ ] [C] Nếu dùng fault API, cấu hình `ENABLE_FAULT_INJECTION=true`, `DEPLOYMENT_ENVIRONMENT=dev`, `ALLOW_DEV_FAULT_INJECTION=true`; local/test chỉ cần bật cờ đầu, ngoài allowlist trả 404. `services/common/faults.py:15`.
- [x] [LIVE] Sidecar dùng OIDC; client Cognito đã có callback `http://localhost:8000/ui/callback`. Xác minh live ngày 18/11/2026: `GET /ui/auth-config`, `aws --profile console --region ap-southeast-1 cognito-idp describe-user-pool-client --user-pool-id "$USER_POOL_ID" --client-id "$UI_CLIENT_ID"`.
- [x] [LIVE 19/11] Operator `nvtank` đã đặt mật khẩu permanent, enroll TOTP và đăng nhập (hosted login hỏi mã 6 số). Operator mới làm theo mục 4. Không dùng token tĩnh trên EKS. `scripts/aws_demo/EDGE.md:167`, `scripts/aws_demo/README.ui.md:99`.

Duyệt known-good sau khi đã đọc evidence, tại nơi nhìn thấy đúng review store [C]:

```sh
python -m scripts.approve_known_good --review-store "$REVIEW_STORE" \
  --evidence-ref "$EVIDENCE_REF" --approved-by "$OWNER" \
  --environment aws-dev --confirm-operator-approval
```

Căn cứ: `docs/runbooks/CONTROLLED_REMEDIATION.md:118`; ledger kết quả được truyền vào `LEDGER_REF`, không lấy ref minh họa.

## 2a. Trạng thái live hiện hành (kiểm 19/11/2026 tối) và thứ tự chạy trên dev 833925774773

> **Policy đang BẬT.** Controller live có `--policy-enabled --model-store /models --model-release-ref artifact://manifests/092290ce….json`. Bấm **Duyệt** trên một proposal hợp lệ sẽ để controller **PATCH Payment thật** sau khi kiểm guard. Các ghi chú "policy tắt" ở mục 2 và các mục sau là snapshot 18/11, đã lỗi thời.

Đã có [LIVE 19/11]:
- Operator `nvtank` đã enroll TOTP (hosted login hỏi mã 6 số; `admin-get-user` vẫn trả `UserMFASettingList` rỗng sau khi enroll qua hosted UI, đó là hành vi của Cognito).
- Model `de38fc6b` (release `092290ce…`) trên PVC `model-de38fc6b` (gp2 RWO); incident worker và controller đã chọn release; health epoch mới root `/artifacts/health/e1-model-de38fc6b-20261119`, stream `806d288a…` ([EPOCH_MIGRATION_2026-11-19.md](../delivery/EPOCH_MIGRATION_2026-11-19.md)).
- Secret `remediation-read` (key `action-read-database-url`) do `control.py action-read` tạo; role `control_action_reader` chỉ SELECT.
- Incident worker tạo incident thật; 8 incident đều `recovered` lúc kiểm. Hai proposal B7 đã **hết hạn** (`approval-expired` → `escalated`) vì không ai duyệt trong 15 phút; bảng `sentinel_remediation.dispatches` có 0 dòng, tức **chưa có rollback nào chạy trên EKS**.
- Grafana chỉ đăng nhập qua Cognito OAuth, đi qua CloudFront.

Còn thiếu:
- **Một lượt duyệt trọn vòng:** tạo regression có receipt, operator canh UI và **Approve trong 15 phút** kể từ lúc proposal được tạo; freshness evidence 900 giây.
- **RBAC `demo-fault`** cho lượt failed-verify: cần ClusterAdmin; namespace AdminPolicy bị RBAC escalation check chặn. Xem `scripts/aws_demo/demo-fault-rbac.yaml`.

Thứ tự một buổi tập dượt bằng runner, mỗi bước ghi AWS/kubectl đều qua hai lượt review:
1. Tạo runner theo lệnh ở đầu `scripts/aws_demo/demo-runner.yaml`: stream lấy từ ConfigMap `sentinelops-workers-runtime`, root lấy từ `--source-store` của controller, nên runner luôn khớp epoch hiện hành.
2. Nếu chạy failed-verify: người có ClusterAdmin tạo RBAC `demo-fault` theo UID của Job vừa tạo.
3. Tập dượt bằng `kubectl exec job/demo-runner`: 3 lượt duyệt trên UI, `--reject-run`, `--kill-switch-run`, `--failed-verify-run --fault-token-file /tmp/fault-token`. Driver PATCH image regression trước khi chờ proposal và không tự khôi phục khi hết thời gian: nếu một lượt dừng giữa chừng, khôi phục image known-good theo quy trình review trước lượt kế.
4. Dọn: xoá `job/demo-runner` và `configmap/demo-runner-script` (RBAC `demo-fault` tự xoá theo owner), chạy `control.py disable-read` rồi xoá `secret/remediation-read`.

## 3. Dùng UI đã deploy; chỉ render lại khi cần

### Cấu hình chung cho cả hai đường [LIVE/B/C]

[LIVE] Không cần build/render từ đầu để mở UI: ECR `sentinelops/control-plane` đã có image `git-b4b416f`, digest `sha256:bb40c73ad3c17e585e36f6e49d99f491371e971b3f730ee9d0d341de949a4859`; Deployment `remediation-controller`, ConfigMap `sentinelops-api-runtime` và Service `sentinelops-api` đã tồn tại. Xác minh live ngày 18/11/2026: `aws --profile console --region ap-southeast-1 ecr describe-images --repository-name sentinelops/control-plane --image-ids imageTag=git-b4b416f` và `kubectl --context sentinelops-demo -n sentinelops-b-actions get deployment/remediation-controller configmap/sentinelops-api-runtime service/sentinelops-api`.

Kiểm Deployment hiện có, rồi đi thẳng tới đường A để port-forward:

```sh
kubectl --context sentinelops-demo -n sentinelops-b-actions get deployment remediation-controller
kubectl --context sentinelops-demo -n sentinelops-b-actions rollout status deployment/remediation-controller --timeout=180s
```

Nếu cần render lại, lấy binding đã review từ ConfigMap hiện có hoặc `GET /ui/auth-config` sau port-forward: `ISSUER`, `JWKS_URL`, `UI_CLIENT_ID`, `LOGIN_DOMAIN` lần lượt là `.issuer`, `.jwks_url`, `.client_id`, `.domain`; pool ID là segment cuối issuer. Không cần in Secret để lấy cấu hình login. Căn cứ: `src/sentinelops/api.py:169`, `scripts/render_api_manifests.py:72`.

Phần render/apply sau **chỉ dùng khi cần triển khai lại** [B/C], dùng `CONTROL_IMAGE` ghim digest đã kiểm ở trên và các binding hiện hành. Bản live hiện đã bật policy (mục 2a); khi render lại phải giữ `--policy-enabled` cùng model claim/release, nếu bỏ sẽ tắt policy trên live. Chỉ bỏ cờ khi chủ đích dừng rollback (`scripts/render_worker_manifests.py:294`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:142`):

```sh
python scripts/render_api_manifests.py --image "$CONTROL_IMAGE" \
  --environment aws-dev --control-secret control-runtime \
  --issuer "$ISSUER" --audience "$UI_CLIENT_ID" --audience-claim client_id \
  --jwks-url "$JWKS_URL" --login-domain "$LOGIN_DOMAIN" \
  --approval-scope sentinelops/remediation.approve --controller-sidecar \
  --service-type NodePort --node-port 30080 --output "$API_UI_MANIFEST"
python scripts/render_worker_manifests.py --image "$CONTROL_IMAGE" \
  --environment aws-dev --control-database "$CONTROL_DATABASE" \
  --database-ca-config "$DATABASE_CA_CONFIG" \
  --health-store-claim "$HEALTH_CLAIM" --incident-store-claim "$INCIDENT_CLAIM" \
  --health-stream "$HEALTH_STREAM" --kubernetes-server "$KUBE_SERVER" \
  --cluster-uid "$CLUSTER_UID" --namespace-uid "$NAMESPACE_UID" \
  --checkout-deployment-uid "$CHECKOUT_UID" --payment-deployment-uid "$PAYMENT_UID" \
  --inventory-deployment-uid "$INVENTORY_UID" --observer-secret "$OBSERVER_SECRET" \
  --model-claim "$MODEL_CLAIM" --model-release-ref "$MODEL_RELEASE_REF" \
  --remediation-store-claim "$REMEDIATION_CLAIM" --known-good-store-claim "$REVIEW_CLAIM" \
  --deployment-change-store-claim "$CHANGE_CLAIM" \
  --demo-ui --output "$WORKER_UI_MANIFEST"
```

Căn cứ flags: `scripts/render_worker_manifests.py:437`, `scripts/aws_demo/README.ui.md:37`.
**Chặn trước apply:** API renderer xuất namespace `sentinelops`, controller ở `sentinelops-b-actions`; operator phải sửa metadata namespace của ConfigMap/Service trong manifest vừa render sang namespace controller và review lại selector/port.
Không coi hai renderer tự nối end-to-end; `render_all` cũng không tự bật sidecar/policy.
Căn cứ: `scripts/render_api_manifests.py:71`, `scripts/render_worker_manifests.py:31`, `scripts/aws_demo/render_all.py:107`.

**Bắt buộc khi render lại:** thêm vào `env` của container **`remediation-controller`** trong manifest vừa render (giữ nguyên các env khác):

```yaml
- name: PGSSLROOTCERT
  value: /etc/sentinelops/tls/ca-bundle.crt
```

[LIVE] Ngày 18/11/2026, log controller đã gặp `asyncpg.exceptions.ClientConfigurationError` với `sslmode=verify-full`; thêm `PGSSLROOTCERT` ở trên mới chạy được. `SSL_CERT_FILE` đơn thuần không đủ với nhánh này của asyncpg; không thêm query `sslrootcert` vào DSN vì validator chỉ nhận đúng `sslmode=verify-full`. Đối chiếu live: `kubectl --context sentinelops-demo -n sentinelops-b-actions logs deployment/remediation-controller -c remediation-controller` và env Deployment sau sửa; đối chiếu source: `scripts/run_remediation_controller.py:59`, `scripts/render_worker_manifests.py:315`, `scripts/render_worker_manifests.py:323` (renderer hiện chỉ thêm `SSL_CERT_FILE`).
Giữ tham chiếu Secret `remediation-runtime` với các key `action-database-url`, `known-good-ledger-ref`, `approved-by`; không chép giá trị ra tài liệu. Thiếu ledger/approver sẽ chặn khởi động (`scripts/render_worker_manifests.py:326`, `scripts/run_remediation_controller.py:68`).

Chỉ sau review binding/namespace, env CA và policy, operator triển khai ConfigMap trước worker rồi kiểm sidecar:

```sh
kubectl --context sentinelops-demo apply -f "$API_UI_MANIFEST"
kubectl --context sentinelops-demo apply -f "$WORKER_UI_MANIFEST"
kubectl --context sentinelops-demo -n sentinelops-b-actions rollout status deployment/remediation-controller --timeout=180s
```

Sidecar dùng cùng PVC `remediation-artifacts` tại `/artifacts/remediation`; không mount RWO này sang Deployment API riêng.
Khi đổi ConfigMap OIDC, phải `kubectl --context sentinelops-demo -n sentinelops-b-actions rollout restart deployment/remediation-controller`.
Căn cứ: `scripts/aws_demo/README.ui.md:10`, `scripts/aws_demo/README.ui.md:44`.

### Đường A — port-forward sidecar

```sh
kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward \
  deployment/remediation-controller --address 127.0.0.1 8000:8000
```

Mở **`http://localhost:8000/ui/`**; debug qua `http://localhost:8000/health`. `--address 127.0.0.1` ở lệnh trên chỉ là địa chỉ bind của port-forward.
**Cạm bẫy [LIVE]:** Cognito chỉ chấp nhận callback HTTP với hostname **`localhost`**, không chấp nhận **`127.0.0.1`**. Callback `http://localhost:8000/ui/callback` đã được owner thêm, giữ callback CloudFront; không cần đăng ký lại để dùng đường A. Xác minh live ngày 18/11/2026: Cognito `update-user-pool-client`/`describe-user-pool-client`; trước khi sửa, hosted login trả `redirect_mismatch`. Mở UI bằng `localhost` để browser tạo đúng callback; operator mới vẫn phải hoàn tất mật khẩu và TOTP ở mục 4 (`nvtank` đã xong từ 19/11).
Thay đổi callback bằng CLI tạo drift: Terraform hiện chỉ nhận HTTPS và khi bật edge chỉ khai báo callback CloudFront; lần apply sau có thể hoàn nguyên callback localhost. Review trước khi apply (`infra/aws/operator_identity.tf:19`, `infra/aws/operator_identity.tf:86`).
Không dùng token từ Secret để bypass OIDC; dự phòng CLI ở mục 8 vẫn chỉ ghi receipt và chịu guard. Căn cứ: `scripts/aws_demo/README.ui.md:149`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:169`.

### Đường B — CloudFront edge

[LIVE 18/11, đã lỗi thời; từ 19/11 distribution đã bật và `/ui/` trả 200] Edge **đã apply**, khi đó distribution `dr76usfhbe2w8.cloudfront.net` có `Status=Deployed`, **`Enabled=false`**, nên chưa có URL public dùng được. ALB internal và VPC origin đã tồn tại; state ghi cấu hình bootstrap bên dưới. Xác minh live ngày 18/11/2026: AWS CloudFront `get-distribution`, `list-vpc-origins`, ELBv2 `describe-load-balancers` và đối chiếu Terraform state. Chỉ mở edge theo [EDGE.md](../../scripts/aws_demo/EDGE.md), giữ đúng baseline/state `dev`:

```hcl
edge_enabled              = true
operator_identity_enabled = true
edge_vpc_origin_bootstrap = true # bootstrap; distribution disabled
```

[LIVE] Không apply từ bản backup state chưa đồng bộ: `terraform plan` ngày 18/11/2026 với `operator_identity_enabled=true`, `edge_enabled=false` đã đề xuất xóa edge. Phải đồng bộ state và review plan trước khi chạy tiếp.
Khi sidecar/NodePort/auth đã kiểm, đổi `edge_vpc_origin_bootstrap=false`, review/publish theo EDGE rồi lấy `edge_domain` từ `terraform -chdir=infra/aws output -raw edge_domain` trên state đã đồng bộ (`scripts/aws_demo/EDGE.md:149`, `infra/aws/outputs.tf:107`).
Mở `https://<edge_domain>/ui/` (mẫu ghép, **không phải URL live**); callback là `https://<edge_domain>/ui/callback`.
`edge_enabled=false` mặc định làm output null; bootstrap=true làm distribution disabled ngay cả khi có domain.
Đường dự kiến: CloudFront → VPC origin → ALB internal → NodePort 30080 → sidecar:8000.
Căn cứ: `infra/aws/variables.tf:283`, `infra/aws/edge.tf:242`, `infra/aws/outputs.tf:107`, `scripts/aws_demo/EDGE.md:58`, `scripts/aws_demo/EDGE.md:149`.

## 4. Đăng nhập [LIVE/C] — còn bước con người

1. **Admin/owner tạo operator nếu chưa có.** [LIVE] Pool `sentinelops-dev-operators` đã tồn tại, MFA `ON`; owner đã tạo user `nvtank` ngày 18/11/2026; từ 19/11 user này đã đặt mật khẩu, enroll TOTP và đăng nhập được. Đối chiếu live: Cognito `describe-user-pool`/`admin-get-user`. Không tạo lại user này. Với operator mới, admin chọn đúng pool trong Cognito console, **Create user**, dùng email được owner xác nhận; hoặc chạy `aws --profile console --region ap-southeast-1 cognito-idp admin-create-user --cli-input-json "file://$OPERATOR_ENROLLMENT_INPUT"` với file quyền 0600 cấp qua kênh riêng, xóa sau dùng. Không đưa nội dung file hay mật khẩu lên command line/tài liệu (`scripts/aws_demo/EDGE.md:169`).
2. **Con người đặt mật khẩu permanent.** Operator mở `http://localhost:8000/ui/`, bấm **Đăng nhập Cognito** và đổi mật khẩu tạm theo hosted login; nếu cần đặt lại, admin xử lý trong Cognito theo kênh bảo mật. Với `nvtank` việc này đã xong từ 19/11 (mục 2a); bước này chỉ còn áp dụng cho operator mới. Mật khẩu phải tối thiểu 16 ký tự, đủ hoa/thường/số/ký hiệu (`infra/aws/operator_identity.tf:37`, `scripts/aws_demo/EDGE.md:176`; xác minh live ngày 18/11/2026: Cognito `describe-user-pool`/`admin-get-user`).
3. **Con người enroll MFA TOTP:** quét QR bằng authenticator, nhập mã 6 số để xác minh, rồi thử đăng nhập lại với TOTP. IAM admin không thể hoàn tất thay bước quét QR/nhập mã của operator; không tắt MFA để demo. Bước này **chưa hoàn tất** trong phiên live ngày 18/11/2026 (đối chiếu Cognito `admin-get-user`); quy trình: `scripts/aws_demo/EDGE.md:176`, MFA bắt buộc: `infra/aws/operator_identity.tf:29`.
4. **Khi login lỗi, tra trạng thái user:** [LIVE] `PreventUserExistenceErrors=ENABLED` khiến các nguyên nhân đăng nhập hỏng hiện cùng thông báo “Incorrect username or password”; không suy ra user đã tồn tại hay mật khẩu sai từ thông báo đó. Xác minh live ngày 18/11/2026: Cognito `describe-user-pool-client`/`admin-get-user`; cấu hình source: `infra/aws/operator_identity.tf:79`. Lấy `USER_POOL_ID` từ issuer, chọn đúng `OPERATOR_USER`, rồi chỉ đọc các trường trạng thái:

   ```sh
   aws --profile console --region ap-southeast-1 cognito-idp admin-get-user \
     --user-pool-id "$USER_POOL_ID" --username "$OPERATOR_USER" \
     --query '{Username:Username,Enabled:Enabled,UserStatus:UserStatus,UserMFASettingList:UserMFASettingList}'
   ```

5. Browser chạy Authorization Code + PKCE S256: lấy `/ui/auth-config`, tạo verifier/state/nonce, authorize, callback kiểm state rồi đổi code/verifier lấy token.
6. Cần `openid`, `sentinelops/incidents.read` và `sentinelops/remediation.approve`; API kiểm access JWT, issuer, `client_id`, expiry, `token_use=access`, scope.
7. Token chỉ trong RAM, refresh tab mất phiên và cần login lại; chỉ transaction PKCE ở sessionStorage tối đa 10 phút. Disconnect không revoke toàn bộ Cognito SSO.

Căn cứ: `scripts/aws_demo/EDGE.md:167`, `scripts/aws_demo/README.ui.md:68`, `scripts/aws_demo/README.ui.md:71`, `scripts/aws_demo/README.ui.md:77`, `scripts/aws_demo/README.ui.md:91`.

## 5. Kịch bản chính — traffic → lỗi → proposal → Duyệt → xác nhận

**Snapshot 18/11, đã lỗi thời (nay có 8 incident và 2 proposal B7 đã hết hạn, xem mục 2a):** khi đó UI chưa có incident để hiển thị; log controller poll trả `[]` (xác minh live ngày 18/11/2026: `kubectl --context sentinelops-demo -n sentinelops-b-actions logs deployment/remediation-controller -c remediation-controller`). Sau khi đăng nhập, danh sách sẽ rỗng cho tới khi có incident thật; chưa thể chọn incident/proposal để demo. `GET /v1/incidents` không token chỉ trả 401, không phải bằng chứng đã đọc danh sách (live cùng ngày; endpoint đọc: `src/sentinelops/api.py:447`).
**Rollback [LIVE 19/11]:** policy đang **bật** (mục 2a); bấm **Duyệt** trên proposal hợp lệ sẽ để controller PATCH Payment thật sau khi kiểm guard. Chưa có lượt nào chạy trọn trên EKS. Kịch bản thực thi và hai kịch bản phụ chỉ chạy sau khi review đủ prerequisite và bật policy tường minh; không coi hướng dẫn dưới đây là kết quả live (`scripts/run_remediation_controller.py:51`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:142`, `scripts/aws_demo/README.ui.md:14`).

**Luật hiện hành [C]:** mọi `low/medium/high/critical` đều cần operator duyệt. Proposal oncall cần executable, đủ evidence, score ≥ `max(0.80,catalog)` và margin ≥ `max(0.15,catalog)`; còn lại engineer, không auto.
Không dùng mô tả LOW tự chạy/v2 cũ trong báo cáo triển khai. Căn cứ: `docs/design/AUTO_RUNBOOK_INTERFACE.md:9`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:14`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:23`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:33`.

1. Bắt đầu/duy trì traffic trên workload đã render, kiểm counts từng service:

```sh
kubectl --context sentinelops-demo -n sentinelops-b-actions scale deployment/sentinelops-traffic --replicas=1
kubectl --context sentinelops-demo -n sentinelops-b-actions logs -f deployment/sentinelops-traffic --tail=30
```

Driver riêng cho mỗi service, cửa sổ 30 giây; giữ traffic suốt review và verification. Căn cứ: `scripts/aws_demo/render_all.py:47`, `scripts/aws_demo/traffic_loop.py:22`, `scripts/aws_demo/README.review.md:15`.

2. Điền biến từ môi trường đã review: `ACTION_READ_DSN`, `REGRESSION_IMAGE`, `KUBE_SERVER`, `HEALTH_STREAM`, `LEDGER_REF`, `OWNER`, các store/path/token/CA và `TARGET_JSON`.
`CONTROLLER_STORE_MOUNT` là đường nhìn thấy đúng artifact controller từ nơi chạy driver; `--controller-store` là đường trong pod. Không tùy tiện copy store làm mất binding.
`REGRESSION_IMAGE` phải là digest khác known-good **trong cùng repo** với known-good (executor chỉ rollback về digest cùng repo, `src/sentinelops/remediation/domain.py` `rollback_patch`); trên AWS dev là `833925774773.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/application@sha256:766dd5f5a32eba9950c4854451f6f6891483d82df735ac93c7046597af7584e0` (cùng tiền tố registry với known-good `…/sentinelops/application@sha256:e520e702…`) (tag `git-d200fc0-payment-regression`). Driver từ chối trước khi deploy nếu khác repo hoặc trùng digest known-good.
Căn cứ: `scripts/run_runbook_demo.py:417`, `scripts/run_runbook_demo.py:436`, `scripts/run_runbook_demo.py:486`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:75`.

```sh
run_demo() {
  SENTINELOPS_ACTION_READ_DATABASE_URL="$ACTION_READ_DSN" \
  python -m scripts.run_runbook_demo --regression-image "$REGRESSION_IMAGE" \
    --server "$KUBE_SERVER" --health-stream "$HEALTH_STREAM" --environment aws-dev \
    --ledger-ref "$LEDGER_REF" --approved-by "$OWNER" \
    --source-store "$HEALTH_STORE" --review-store "$REVIEW_STORE" \
    --store "$CONTROLLER_STORE_MOUNT" --deployment-change-store "$CHANGE_STORE" \
    --target "$TARGET_JSON" --token-file "$DEPLOY_TOKEN" --ca-file "$CA_FILE" \
    --observer-token-file "$OBSERVER_TOKEN" --observer-ca-file "$OBSERVER_CA" \
    --kill-switch "$SHARED_KILL_SWITCH" --health-policy configs/slo_health_policy.json \
    --controller-store /artifacts/remediation --timeout 1200 --poll-seconds 10 "$@"
}
run_demo --output "$FRESH_SUCCESS_DIR"
```

Flags theo `scripts/run_runbook_demo.py:470`; output phải mới, mặc định 3 lượt và cooldown 600 giây giữa lượt; không dùng `--approve-as` khi operator bấm UI (`scripts/run_runbook_demo.py:87`, `scripts/run_runbook_demo.py:166`).
Driver mở baseline deployment-change, dry-run PATCH rồi PATCH image regression, đợi `CHANGE_OBSERVED` (150 giây, poll 2 giây), sau đó đợi incident/proposal mới; không dùng ambient AWS/kubeconfig (`scripts/run_runbook_demo.py:3`, `scripts/run_runbook_demo.py:99`, `scripts/run_runbook_demo.py:284`).

3. Chọn incident trên UI, đọc IER/evidence/giải thích rồi panel **Đề xuất runbook**:

| Trường/nút đúng trên UI | Operator kiểm gì |
| --- | --- |
| Incident / revision, Severity, RCA top-3 | Đúng incident mới, revision hiện tại và Payment là RCA phù hợp. |
| Runbook đề xuất, Score, Margin | Runbook executable, đạt sàn 0.80/0.15 và ngưỡng catalog. |
| Image hiện tại, Image sẽ rollback về | Hai digest khớp regression và known-good đã duyệt trước onset. |
| SHA-256, Trạng thái, Hạn duyệt | Đúng proposal hash, pending; xem countdown “Còn … để duyệt”. |
| Tải lại đề xuất, Lý do quyết định | Tải lại khi trạng thái chưa chắc; nhập lý do có nội dung. |
| Duyệt, Từ chối | Thiếu quyền/hết hạn/không pending thì khóa; thiếu image khóa Duyệt. |

Căn cứ nhãn/hành vi: `src/sentinelops/ui/assets/app.js:550`, `src/sentinelops/ui/assets/app.js:569`, `src/sentinelops/ui/assets/app.js:587`, `src/sentinelops/ui/assets/app.js:606`.
**Điểm chặn đã biết:** nếu producer chưa xuất đủ image digest, UI có thể chỉ đọc/từ chối, approve trả `409 proposal_review_incomplete`. Phải xác minh proposal đủ digest; không điền từ browser (`scripts/aws_demo/README.ui.md:114`).

4. Nhập **Lý do quyết định**, bấm **Duyệt** một lần. API chỉ ghi receipt; **chỉ khi policy đã bật và đủ guard**, tick controller sau mới claim approval một lần, kiểm guard và PATCH rollback known-good. Bản live hiện **bật** policy (mục 2a), nên Duyệt là thao tác có hiệu lực thật.
5. Chỉ xác nhận thành công sau `recovered` + `telemetry_verified`, Payment ready ở known-good, dispatch tăng đúng 1 và 3 bucket Payment eligible/clear sau action; deadline verify 1.200 giây.
Căn cứ: `docs/design/AUTO_RUNBOOK_INTERFACE.md:131`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:172`, `scripts/run_runbook_demo.py:236`, `docs/runbooks/CONTROLLED_REMEDIATION.md:215`.

Fault RAM `POST /internal/faults` với `{"mode":"latency","delay_ms":950}` chỉ demo phát hiện, không thay deployment nên không đủ điều kiện rollback bad-deploy; Inventory chỉ có runbook thủ công.
Căn cứ: `services/payment/app.py:97`, `services/common/faults.py:31`, `configs/runbook_catalog.json:11`, `configs/runbook_catalog.json:28`.

## 6. Hai kịch bản phụ [C]

```sh
run_demo --reject-run --output "$FRESH_REJECT_DIR"
run_demo --failed-verify-run --fault-token-file "$FAULT_TOKEN" --output "$FRESH_FAILED_VERIFY_DIR"
```

Chạy riêng từng lệnh với output mới, Payment trở lại known-good trước mỗi lượt; negative flags loại trừ nhau.
**Từ chối:** 1 lượt, operator bấm **Từ chối**; phải `escalated/approval-rejected`, không action/dispatch, image/generation không đổi suốt deadline. Image lỗi không tự rollback; khôi phục có review trước lượt kế tiếp.
**Verify thất bại:** 1 lượt, vẫn bấm **Duyệt**; driver chờ `verifying`, chọn pod known-good mới theo UID rồi tiêm `mode=error` qua pod proxy. Phải escalated/quarantined, không `telemetry_verified`; finally dọn `healthy`, kiểm receipt cleanup.
Căn cứ: `scripts/run_runbook_demo.py:87`, `scripts/run_runbook_demo.py:172`, `scripts/run_runbook_demo.py:221`, `scripts/run_runbook_demo.py:257`, `scripts/run_runbook_demo.py:350`.

## 7. API và GUARD khi demo bị chặn [C]

| Endpoint UI gọi | Header/quyền và ý nghĩa |
| --- | --- |
| GET `/ui/auth-config` | Công khai, cấu hình login; không trả token. `src/sentinelops/api.py:169`. |
| GET `/v1/incidents`; GET `/v1/incidents/{id}` | Bearer/read; danh sách/chi tiết. `src/sentinelops/api.py:447`, `src/sentinelops/api.py:463`. |
| GET `/v1/incidents/{id}/explanation`; GET `/v1/incidents/{id}/context` | Bearer/read; giải thích/evidence. `src/sentinelops/api.py:473`, `src/sentinelops/api.py:503`. |
| GET `/v1/runbook-proposals?incident_id=...`; GET `/v1/runbook-proposals/{id}` | Bearer/incidents.read; lấy proposal/hash. `src/sentinelops/ui/assets/client.js:177`, `src/sentinelops/ui/assets/client.js:186`. |
| POST `/v1/runbook-proposals/{id}/decision` | Bearer, read + approve, Content-Type và If-Match như mẫu dưới; chỉ ghi quyết định. `scripts/aws_demo/README.ui.md:94`, `scripts/aws_demo/README.ui.md:119`. |
| GET `/v1/incidents/{id}/actions`; GET `/v1/actions/{id}`; GET `/v1/actions/{id}/quarantine` | Audit action; sidecar chưa có SELECT-only action-reader nên tab có thể unavailable. `src/sentinelops/api.py:335`, `src/sentinelops/api.py:319`, `src/sentinelops/api.py:350`, `scripts/aws_demo/README.ui.md:140`. |

```http
POST /v1/runbook-proposals/{id}/decision
Authorization: Bearer <access-token>
Content-Type: application/json
If-Match: "<proposal_sha256>"

{"decision":"approve","reason":"Đã kiểm tra bằng chứng và runbook"}
```

Đổi `approve` thành `reject` để từ chối; không có endpoint `/approve` hay `/reject` riêng. Không gửi actor/image/target từ client (`scripts/aws_demo/README.ui.md:128`).

| GUARD có thể chặn | Đọc nguyên nhân / giải thích khi demo |
| --- | --- |
| Disabled/kill switch, RCA abstain/UNKNOWN, model/H4/change thiếu | Xem decision engineer, outcome và controller log; `runbook-disabled-or-kill-switch`. `docs/design/AUTO_RUNBOOK_INTERFACE.md:9`, `src/sentinelops/remediation/authority.py:65`. |
| Scope/hash/review image | 403 thiếu approve; 428 thiếu hash, 422 sai dạng, 412 hash khác; 409 stale/expired/handled hoặc review incomplete. Tải lại trước quyết định, không tự POST lại khi mất reply. `scripts/aws_demo/README.ui.md:94`, `scripts/aws_demo/README.ui.md:114`, `scripts/aws_demo/README.ui.md:128`. |
| Reject/đã dùng/TTL/revision/severity đổi | Outcome `approval-rejected` hoặc `approval-expired`; approval dùng một lần, TTL 15 phút. Đọc outcome/claim, không sửa receipt. `docs/design/AUTO_RUNBOOK_INTERFACE.md:147`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:170`. |
| Target/UID/image/ledger drift; RCA >900 giây với policy mới | `approval-target-changed`, `approval-after-evidence-freshness`; TTL chưa hết vẫn có thể bị chặn. Policy 180 giây giữ để đọc action lịch sử. Đọc action journal reason gốc. `docs/design/AUTO_RUNBOOK_INTERFACE.md:174`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:176`. |
| Known-good sau onset, change generation không khớp | Đối chiếu ledger/onset và deployment-change artifact; không duyệt lại để hợp thức hóa. `docs/runbooks/CONTROLLED_REMEDIATION.md:25`, `docs/runbooks/CONTROLLED_REMEDIATION.md:172`. |
| 1 attempt, target lock/quarantine, cooldown 600 giây | Đọc action/outcome và quarantine; không retry mutation để ép demo. `docs/runbooks/CONTROLLED_REMEDIATION.md:25`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:17`. |
| Replica/pod/surge/HPA/paused; PATCH test/dry-run | Phải 1 replica/pod, zero surge, không HPA/paused; test resourceVersion/UID/image/generation và dryRun=All pass. Đọc preflight/action reason. `docs/runbooks/CONTROLLED_REMEDIATION.md:172`. |

## 8. Dự phòng: duyệt CLI trong pod [C]

Đọc cùng proposal/evidence như trên, lấy `PROPOSAL_ID` từ proposal/driver và nhập danh tính `ONCALL`:

```sh
kubectl --context sentinelops-demo -n sentinelops-b-actions exec deploy/remediation-controller -- \
  python scripts/approve_runbook.py --store /artifacts/remediation \
  --proposal-id "$PROPOSAL_ID" --approved-by "$ONCALL" --decision approve \
  --reason 'Đã kiểm tra bằng chứng và runbook' --confirm-oncall-approval
```

Đổi `approve` thành `reject` nếu từ chối. CLI chỉ ghi receipt, không bỏ qua thiếu image/H4/guard. Bản live đang bật policy (mục 2a), nên receipt approve hợp lệ sẽ dẫn tới PATCH thật.
Căn cứ: `scripts/run_runbook_demo.py:69`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:169`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:171`.

## 9. Xác nhận và dọn sau demo

Đọc UI outcome/receipt, controller log và `output/<n>.json` ở thư mục đã chọn: action_id, action_revision, last_event_hash, telemetry, dispatches_before/after; đối chiếu image readiness bằng Deployment thực tế. Approval thành công chưa phải rollback thành công (`scripts/run_runbook_demo.py:245`, `scripts/run_runbook_demo.py:251`).
Prometheus [LIVE] đã trả `GET /-/ready` thành công, target `otel-collector` up qua `GET /api/v1/targets`; Grafana [LIVE] đã deploy, rollout 1/1, `GET /grafana/api/health` trả `database=ok`, `GET /grafana/login` trả 200 (xác minh live ngày 18/11/2026). Ngày 18/11 Grafana dùng login quản trị local qua port-forward, OAuth Cognito chưa bật; **từ 19/11 Grafana chỉ đăng nhập qua Cognito OAuth qua CloudFront** (mục 2a). Dữ liệu ở `emptyDir` sẽ mất khi thay pod (live 18/11: `kubectl --context sentinelops-demo -n sentinelops-b-actions get deployment grafana`, kiểm env/storage). Jaeger có bằng chứng triển khai lịch sử [A]; CloudWatch dashboard apply lỗi [B], chưa có bằng chứng đã sửa (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`).
Sau lượt cuối: kiểm fault đã healthy, nhất là `fault_cleanup` của failed-verify; nhánh reject cần khôi phục image qua quy trình review. Dừng traffic/port-forward, lưu receipt đã lọc bí mật, thu hồi enrollment/client/runtime credentials và teardown edge theo runbook (`scripts/run_runbook_demo.py:257`, `scripts/aws_demo/README.ui.md:149`, `scripts/aws_demo/EDGE.md:203`).
Không tự xóa PVC/state trước khi lưu bằng chứng; làm checklist [AWS_DEMO_PLAN.md](../delivery/AWS_DEMO_PLAN.md#8-checklist-dọn-dẹp-sau-demo). Teardown hoàn tất: **Không có bằng chứng** tại commit (`docs/delivery/AWS_DEMO_PLAN.md:114`).
Mọi receipt driver mang `demo_mode=true`, `G6_accepted=false`, `q09_eligible=false`; không nâng demo/local thành acceptance AWS (`scripts/run_runbook_demo.py:144`).

Đối chiếu phạm vi triển khai ở [AWS_DEPLOYED_SUMMARY.md](../delivery/AWS_DEPLOYED_SUMMARY.md); bằng chứng cho runbook là các tham chiếu path:line và ghi nhận [LIVE] ngày 18/11/2026 nêu trên.
