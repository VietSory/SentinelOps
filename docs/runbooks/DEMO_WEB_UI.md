# Demo qua trang web operator

> **Máy biên soạn KHÔNG có AWS credential hợp lệ và KHÔNG có kube context `sentinelops-demo`; không có xác minh live nào.**
> **Tại commit đối chiếu (`0afb06d`), UI KHÔNG có bằng chứng đã deploy và không có URL public được bàn giao.**
> Runbook gồm dựng UI rồi mới demo; không được hiểu UI/Cognito/CloudFront đang sẵn sàng.
> Căn cứ về trạng thái UI/edge: `scripts/aws_demo/README.ui.md:156`, `scripts/aws_demo/EDGE.md:5`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:15`.

## 1. Phạm vi bằng chứng

Tài liệu được tổng hợp từ đối chiếu source và báo cáo trong `docs/delivery`.

| Mức | Ý nghĩa và giới hạn |
| --- | --- |
| [A] | Có bằng chứng lịch sử đã apply/chạy AWS; không xác nhận trạng thái hôm nay. Hạ tầng và demo phát hiện Payment được báo cáo tại `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:91`. |
| [B] | Có IaC/manifest, không có bằng chứng apply: controller, sidecar/API/UI, Cognito, CloudFront/ALB, Grafana. `scripts/aws_demo/README.ui.md:3`, `scripts/aws_demo/EDGE.md:5`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:166`. |
| [C] | Tooling/mã/local: renderer, driver, CLI duyệt; không chứng minh vòng AWS thành công. `scripts/run_runbook_demo.py:76`, `scripts/approve_runbook.py:23`. |

**Cạm bẫy tên:** Terraform mặc định `environment=prod`, nhưng đợt triển khai được báo cáo là `dev`, cluster `sentinelops-dev`, RDS `sentinelops-control-dev`.
Không lấy tên suy từ mặc định IaC làm tên live; runtime demo dùng `aws-dev`, fault API dùng `DEPLOYMENT_ENVIRONMENT=dev`.
Căn cứ: `infra/aws/variables.tf:32`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:9`, `scripts/aws_demo/README.ui.md:99`, `services/common/faults.py:17`.

Mọi lệnh dưới đây dành cho operator ở môi trường đã chuẩn bị, **chưa chạy trong phiên biên soạn**. Không thay placeholder bằng giá trị đoán.

## 2. Checklist ĐIỀU KIỆN TIÊN QUYẾT

- [ ] [B] Deploy `remediation-controller`, có action-runtime/control_action_writer và worker image phù hợp; báo cáo còn ghi đây là việc chưa hoàn tất. `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:163`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:164`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:166`.
- [ ] [B/C] Nạp model/release được review, thỏa H4; không dùng bundle local làm chứng cứ AWS đã sẵn sàng. `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162`, `src/sentinelops/remediation/authority.py:65`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:17`.
- [ ] [C] Health stream, environment, policy và artifact-root binding đúng; health/review/change/remediation stores đọc đúng dữ liệu controller, target chứa đúng cluster/namespace/deployment UID. `scripts/run_runbook_demo.py:426`, `scripts/run_runbook_demo.py:433`, `scripts/run_runbook_demo.py:436`.
- [ ] [C] Payment đang healthy ở known-good; review open/complete đạt `AWAITING_OPERATOR_REVIEW`, owner duyệt known-good **TRƯỚC khi tiêm lỗi**. Không tự gán PASS. `docs/runbooks/CONTROLLED_REMEDIATION.md:76`, `docs/runbooks/CONTROLLED_REMEDIATION.md:118`, `scripts/aws_demo/README.review.md:36`.
- [ ] [C] Credential tách biệt: deploy PATCH, observer đọc, fault-token riêng cho verify lỗi; action reader SELECT-only, DB `sentinelops_control`, TLS `sslmode=verify-full`. `scripts/run_runbook_demo.py:420`, `scripts/run_runbook_demo.py:442`, `scripts/run_runbook_demo.py:445`.
- [ ] [C] Kill switch dùng chung với controller; đường normal phải không kích hoạt, người vận hành biết cách bật để dừng. `scripts/run_runbook_demo.py:296`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:17`.
- [ ] [C] Nếu dùng fault API, cấu hình `ENABLE_FAULT_INJECTION=true`, `DEPLOYMENT_ENVIRONMENT=dev`, `ALLOW_DEV_FAULT_INJECTION=true`; local/test chỉ cần bật cờ đầu, ngoài allowlist trả 404. `services/common/faults.py:15`.
- [ ] [B] Sidecar OIDC cùng PVC controller, có public client/scopes/callback hợp lệ và operator đã enroll TOTP; không dùng token tĩnh trên EKS. `scripts/aws_demo/README.ui.md:10`, `scripts/aws_demo/README.ui.md:17`, `scripts/aws_demo/README.ui.md:99`.

Duyệt known-good sau khi đã đọc evidence, tại nơi nhìn thấy đúng review store [C]:

```sh
python -m scripts.approve_known_good --review-store "$REVIEW_STORE" \
  --evidence-ref "$EVIDENCE_REF" --approved-by "$OWNER" \
  --environment aws-dev --confirm-operator-approval
```

Căn cứ: `docs/runbooks/CONTROLLED_REMEDIATION.md:118`; ledger kết quả được truyền vào `LEDGER_REF`, không lấy ref minh họa.

## 3. Dựng UI trước khi mở demo

### Cấu hình chung cho cả hai đường [B/C]

Các biến bên dưới lấy từ binding/manifest đã review. Giá trị thật của domain, pool ID, client ID: **Không có bằng chứng**.
Operator lấy các output trên đúng state `dev` (output không chứng minh endpoint đang sống):

```sh
terraform -chdir=infra/aws output -raw edge_domain
terraform -chdir=infra/aws output -json operator_oidc_binding
terraform -chdir=infra/aws output -raw operator_login_domain
terraform -chdir=infra/aws output -raw artifact_bucket
```

`ISSUER`, `JWKS_URL`, `UI_CLIENT_ID` lấy lần lượt từ `.issuer`, `.jwks_url`, `.audience`; `LOGIN_DOMAIN` từ `operator_login_domain`.
Pool ID là segment cuối issuer; tên bucket thật: **Không có bằng chứng**, lấy `artifact_bucket` ở trên.
Căn cứ: `infra/aws/operator_identity.tf:130`, `infra/aws/operator_identity.tf:142`, `infra/aws/outputs.tf:102`, `scripts/aws_demo/EDGE.md:93`.

Render API ConfigMap/Service và worker sidecar bằng image digest cùng các binding đã kiểm:

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
  --policy-enabled --demo-ui --output "$WORKER_UI_MANIFEST"
```

Căn cứ flags: `scripts/render_worker_manifests.py:437`, `scripts/aws_demo/README.ui.md:37`.
**Chặn trước apply:** API renderer xuất namespace `sentinelops`, controller ở `sentinelops-b-actions`; operator phải sửa metadata namespace của ConfigMap/Service trong manifest vừa render sang namespace controller và review lại selector/port.
Không coi hai renderer tự nối end-to-end; `render_all` cũng không tự bật sidecar/policy.
Căn cứ: `scripts/render_api_manifests.py:71`, `scripts/render_worker_manifests.py:31`, `scripts/aws_demo/render_all.py:107`.

Chỉ sau review binding/namespace, operator triển khai ConfigMap trước worker rồi kiểm sidecar:

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

Mở `http://127.0.0.1:8000/ui/`; debug qua `http://127.0.0.1:8000/health`.
**Cạm bẫy:** client Cognito mặc định chỉ đăng ký callback CloudFront; port-forward đơn thuần **không đăng nhập được**.
Ba lựa chọn: dùng đường B; hoặc đăng ký thêm callback chính xác `http://127.0.0.1:8000/ui/callback` qua cấu hình identity được review; hoặc duyệt CLI trong pod ở mục 8.
Không dùng token từ Secret để bypass OIDC. Căn cứ: `scripts/aws_demo/README.ui.md:143`, `scripts/aws_demo/README.ui.md:149`.

### Đường B — CloudFront edge

Phải triển khai edge theo [EDGE.md](../../scripts/aws_demo/EDGE.md), giữ đúng baseline/state `dev`, cấu hình:

```hcl
edge_enabled              = true
operator_identity_enabled = true
edge_vpc_origin_bootstrap = true # bootstrap; distribution disabled
```

Khi sidecar/NodePort/auth đã kiểm, đổi `edge_vpc_origin_bootstrap=false`, review/publish theo EDGE rồi lấy `edge_domain` bằng output trên.
Mở `https://<edge_domain>/ui/` (mẫu ghép, **không phải URL live**); callback là `https://<edge_domain>/ui/callback`.
`edge_enabled=false` mặc định làm output null; bootstrap=true làm distribution disabled ngay cả khi có domain.
Đường dự kiến: CloudFront → VPC origin → ALB internal → NodePort 30080 → sidecar:8000.
Căn cứ: `infra/aws/variables.tf:283`, `infra/aws/edge.tf:242`, `infra/aws/outputs.tf:107`, `scripts/aws_demo/EDGE.md:58`, `scripts/aws_demo/EDGE.md:149`.

## 4. Đăng nhập [B/C]

1. Bấm **Đăng nhập Cognito**; operator được admin tạo user, đổi mật khẩu tạm và enroll MFA TOTP theo quy trình identity.
2. Browser chạy Authorization Code + PKCE S256: lấy `/ui/auth-config`, tạo verifier/state/nonce, authorize, callback kiểm state rồi đổi code/verifier lấy token.
3. Cần `openid`, `sentinelops/incidents.read` và `sentinelops/remediation.approve`; API kiểm access JWT, issuer, `client_id`, expiry, `token_use=access`, scope.
4. Token chỉ trong RAM, refresh tab mất phiên và cần login lại; chỉ transaction PKCE ở sessionStorage tối đa 10 phút. Disconnect không revoke toàn bộ Cognito SSO.

Căn cứ: `scripts/aws_demo/EDGE.md:167`, `scripts/aws_demo/README.ui.md:68`, `scripts/aws_demo/README.ui.md:71`, `scripts/aws_demo/README.ui.md:77`, `scripts/aws_demo/README.ui.md:91`.

## 5. Kịch bản chính — traffic → lỗi → proposal → Duyệt → xác nhận

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
`REGRESSION_IMAGE` phải chứa `/payment-regression@sha256:<64hex>`; báo cáo ECR lại ghi regression chung repo application: **Không có bằng chứng digest đã push dùng thẳng được cho driver**. Dừng chuẩn bị nếu không khớp.
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

4. Nhập **Lý do quyết định**, bấm **Duyệt** một lần. API chỉ ghi receipt; tick controller sau mới claim approval một lần, kiểm guard và PATCH rollback known-good.
5. Chỉ xác nhận thành công sau `recovered` + `telemetry_verified`, Payment ready ở known-good, dispatch tăng đúng 1 và 3 bucket Payment eligible/clear sau action; deadline verify 600 giây.
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
| Target/UID/image/ledger drift; RCA >180 giây | `approval-target-changed`, `approval-after-evidence-freshness`; TTL chưa hết vẫn có thể bị chặn. Đọc action journal reason gốc. `docs/design/AUTO_RUNBOOK_INTERFACE.md:174`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:176`. |
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

Đổi `approve` thành `reject` nếu từ chối. CLI chỉ ghi receipt, không bỏ qua thiếu image/H4/guard; nếu controller chưa deploy thì đường dự phòng cũng chưa dùng được.
Căn cứ: `scripts/run_runbook_demo.py:69`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:169`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:171`.

## 9. Xác nhận và dọn sau demo

Đọc UI outcome/receipt, controller log và `output/<n>.json` ở thư mục đã chọn: action_id, action_revision, last_event_hash, telemetry, dispatches_before/after; đối chiếu image readiness bằng Deployment thực tế. Approval thành công chưa phải rollback thành công (`scripts/run_runbook_demo.py:245`, `scripts/run_runbook_demo.py:251`).
Prometheus/Jaeger có bằng chứng triển khai lịch sử [A]; Grafana [B] chưa có bằng chứng deploy, CloudWatch dashboard apply lỗi [B], không lấy chúng làm màn hình có sẵn (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/aws_demo/README.grafana.md:3`, `scripts/aws_demo/render_grafana.py:227`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`).
Sau lượt cuối: kiểm fault đã healthy, nhất là `fault_cleanup` của failed-verify; nhánh reject cần khôi phục image qua quy trình review. Dừng traffic/port-forward, lưu receipt đã lọc bí mật, thu hồi enrollment/client/runtime credentials và teardown edge theo runbook (`scripts/run_runbook_demo.py:257`, `scripts/aws_demo/README.ui.md:149`, `scripts/aws_demo/EDGE.md:203`).
Không tự xóa PVC/state trước khi lưu bằng chứng; làm checklist [AWS_DEMO_PLAN.md](../delivery/AWS_DEMO_PLAN.md#8-checklist-dọn-dẹp-sau-demo). Teardown hoàn tất: **Không có bằng chứng** tại commit (`docs/delivery/AWS_DEMO_PLAN.md:114`).
Mọi receipt driver mang `demo_mode=true`, `G6_accepted=false`, `q09_eligible=false`; không nâng demo/local thành acceptance AWS (`scripts/run_runbook_demo.py:144`).

Đối chiếu phạm vi triển khai ở [AWS_DEPLOYED_SUMMARY.md](../delivery/AWS_DEPLOYED_SUMMARY.md); liên kết này không thay thế các bằng chứng path:line và không chứng minh UI đang chạy.
