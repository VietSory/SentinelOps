# Render manifest EKS (offline)

Payment image evidence Job `review`: see [README.review.md](README.review.md).

Chạy trong bản sao repo đã `chown`, bằng `python:3.11-slim-bookworm`; cài package
repo và kubeconform v0.7.0 trước. Không cần AWS CLI, Terraform, kubectl hoặc Docker
socket trong container. Script không gọi các công cụ đó, không đọc credential.
Kubeconform tải schema Kubernetes công khai nếu cache chưa có.

```bash
bash scripts/aws_demo/render_all.sh images.json terraform-outputs.json runtime.json
# Hoặc chọn thư mục MỚI cho lần render sau:
bash scripts/aws_demo/render_all.sh images.json terraform-outputs.json runtime.json \
  --output artifacts/aws-demo/k8s-next
```

- `images.json`: flat map bảy key như `k8s-images.example.json`; tất cả là
  registry digest. ECR application/control-plane phải khớp Terraform outputs.
  `push_images_ecr.py` chỉ xuất hai image: lấy `images.<key>.pinned` rồi bổ sung
  năm image phụ trợ. Docker image inspect JSON không dùng trực tiếp được.
  Khi bật `early_warning` trong runtime, thêm key `operator-ui` trỏ tới digest
  lớp React/API trong cùng ECR repo control-plane theo [REBUILD.md](REBUILD.md).
- Terraform outputs: JSON đã xuất sẵn theo định dạng `terraform output -json`,
  hoặc map giá trị trực tiếp. Ví dụ cấu trúc trong `k8s-terraform.example.json`.
  Lấy exporter role từ `collector_bindings.exporter_role_arn`, kiểm tra đối chiếu
  `workload_role_arns.telemetry-exporter`; không dùng reader role.
- `runtime.json`: theo `k8s-runtime.example.json`. `cluster_uid` là UID namespace
  `kube-system`, không phải EKS ARN/tên cluster. Namespace/deployment UID phải là
  UID thật của tài nguyên đang chạy, không tự tạo UUID. Health stream lấy từ
  health worker đã được chấp nhận; SHA-256 giả chỉ dành cho kiểm tra offline.
  Worker URL Kubernetes dùng Terraform endpoint, thêm port 443 khi chưa có.
  API cần OIDC HTTPS công khai; audience claim có thể `aud` hoặc `client_id`.
  `early_warning: true` cần model PVC/release và các output Terraform
  `edge_domain`, `edge_api_node_port`, `operator_oidc_binding`, `operator_login_domain`,
  `demo_alerts_topic_arn`. Lúc đó `30-api.yaml` cấp ConfigMap OIDC và NodePort
  cho `operator-ui` cùng pod/PVC với controller.

**Mọi digest/UID/OIDC trong example đều là dữ liệu thử; không apply kết quả thử.**
Pipeline từ chối ghi đè thư mục đích, render vào staging, chạy
`kubeconform -strict -summary -kubernetes-version 1.34.0` trên cả sáu YAML,
chỉ xuất thư mục cuối sau khi validate thành công. Không dùng ignore-missing-schemas.

## Thứ tự apply để operator thực hiện sau khi được phép

Không chạy các bước dưới đây trong tác vụ render này.

1. `00-namespaces.yaml`.
2. Tạo dependencies từ kênh cấp phát riêng: commerce Secret trong
   `sentinelops-b-actions` (`commerce-credentials`: `password`, `database-url`);
   bootstrap/migrate RDS; `control-runtime` trong **cả hai namespace** (worker
   cần `database-url` không chứa credential và `password` riêng);
   API action reader Secret riêng trong `sentinelops`; CA ConfigMap
   (`ca-bundle.crt`), các PVC `health-artifacts`, `incident-artifacts`,
   `remediation-artifacts`, `review-artifacts` trong `sentinelops-b-actions`.
   Renderer worker hiện cũng xuất remediation-controller (policy tắt mặc định):
   controller còn cần Secret `remediation-runtime` với `action-database-url`,
   `known-good-ledger-ref`, `approved-by` được cấp riêng. Pipeline không bật policy.
   PVC health phải truy cập được bởi cả hai worker (RWO cùng node hoặc RWX phù hợp).
   Worker đã đặt `SSL_CERT_FILE`; API cần runtime TLS DSN/CA hợp lệ riêng.
3. `10-testbed.yaml`, chờ PostgreSQL và ba service, Prometheus/Jaeger sẵn sàng;
   `20-collector.yaml` sau khi role IRSA/log group/KMS đã tồn tại;
   `30-api.yaml` sau khi OIDC/DB/Secret đã sẵn sàng.
4. `50-traffic.yaml`: bắt đầu traffic trước health history. Deployment dùng image
   application đã có httpx/sentinelops, mount ConfigMap chứa đúng hàm `drive()`
   từ `scripts/run_s2_h4.py` và wrapper nhỏ. Không import/chạy harness kind/Docker,
   không cần scripts có sẵn trong image, không có service-account token.
   Mỗi entry chạy riêng, nominal 1 rps = 60 request/phút/service; Checkout còn
   tạo downstream traffic. Wrapper chấp nhận HTTP 5xx để tiếp tục quan sát lỗi,
   giữ kiểm tra trace/correlation; retry transport error sau 1 giây.
5. Ghi nhận UID thật sau bước 3. Render lại vào thư mục mới nếu UID thay đổi.
   Worker file chứa health, incident và remediation-controller: ở lần bootstrap đầu, operator tách
   resources health worker cùng shared ConfigMaps/RBAC từ `40-workers.yaml`,
   provision observer credentials, chạy health trước, lấy accepted stream, rồi
   render lại bằng stream/UID thật và apply đủ `40-workers.yaml`. Controller
   có RBAC patch đúng Payment Deployment nhưng policy vẫn tắt; không cấp ledger
   giả hay tự coi kết quả render là quyền hành động. Khi đã có
   accepted stream, apply nguyên file sau dependencies. Không dùng stream giả.
   Observer Secret cần token/CA của **incident-worker** SA đúng RBAC; token hết
   hạn cần rotate/restart. Không render nội dung Secret trong pipeline này.

## Giới hạn cần kiểm chứng sau deployment

- Không có load generator Deployment EKS liên tục trước thay đổi này. Driver
  `run_s2_h4.drive`, `run_g6_trial.resilient_traffic` và experiments runner là
  harness cục bộ/bounded. `experiments/traffic.py` là helpers, không phải CLI.
- 60/phút là tốc độ mục tiêu khi phản hồi đủ nhanh; driver gốc đợi request trước
  tick tiếp. Outage, latency >2 giây, pod restart/CPU throttling có thể làm số
  request quan sát được <30/bucket. Phải kiểm tra counter Prometheus thực tế
  (profile `prometheus-increase-60s-5s/v2`, full 60 giây, đủ warm-up), không lấy
  request `/health` hoặc số lượt attempted thay cho business request count.
- Collector AWS ở `sentinelops`, collector testbed ở `sentinelops-b-actions` là
  hai pipeline riêng. Testbed hiện export Prometheus/Jaeger nội bộ, **chưa gửi
  telemetry vào AWS collector**. NetworkPolicy AWS collector chỉ cho pod trong
  cùng namespace; muốn export AWS cần cấu hình routing/cross-namespace policy
  riêng đã review. Không thay collector nội bộ vì health worker cần v2 profile.
- Testbed PostgreSQL/Prometheus dùng emptyDir (mất dữ liệu khi thay Pod); dev bật
  fault injection Payment/Inventory. Manifest này dành cho demo, không production.
- Kubeconform chỉ xác nhận schema, không chứng minh image kéo được, IRSA trust,
  TLS/Secret, storage topology, network, OIDC, UID hay health/model acceptance.
  Không có model PVC/release trong pipeline mặc định; render không phê duyệt model,
  known-good image hay rollback. Controller policy luôn tắt trong wrapper này.
