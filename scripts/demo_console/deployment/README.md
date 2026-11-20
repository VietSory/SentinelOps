# Đóng gói console cho EKS, mặc định chỉ đọc

Package này chuẩn bị deployment `fault-console` trong namespace
`sentinelops-b-actions`. Nó không chạy kubectl, không apply và không bơm lỗi.
Giữ nguyên backend bind `127.0.0.1:8765` và kiểm peer/Host/Origin. Truy cập
qua `kubectl port-forward` tới pod; không có Service, Ingress hoặc CloudFront.

Image chỉ chứa console, fault request contract và bốn runtime dependency trực tiếp
đã pin version. Không có kubectl/AWS CLI/credential/script B7/B9, không mount
Secret/PVC hoặc token ServiceAccount, không cấp RBAC. Nút deploy image hỏng giữ
trạng thái chưa cấu hình. Đây là deployment giao diện chỉ đọc; chưa phải xác nhận
demo bơm lỗi hoạt động trên cụm.

## Build và render để review

Từ gốc repo, sau khi review code và kiểm tra offline:

```sh
docker build -f scripts/demo_console/deployment/Dockerfile \
  --build-arg SENTINELOPS_SOURCE_REVISION=REVIEWED_COMMIT \
  -t YOUR_REGISTRY/fault-console:REVIEWED_COMMIT .
```

Dockerfile-specific ignore chỉ gửi source cần thiết; không gửi `.herdr`, AWS
credential, model, dữ liệu hay git vào build context. Image base pin cùng digest
với control-plane Dockerfile hiện có. Transitive Python dependencies được pip
resolve khi build; ghi lại image digest thực sau build/publish, không deploy tag.
Việc build có thể cần network để lấy image/dependencies. Lệnh trên không push.

```sh
python scripts/demo_console/deployment/render.py \
  --image YOUR_REGISTRY/fault-console@sha256:ACTUAL_64_HEX_DIGEST \
  > /tmp/fault-console.json
```

Renderer từ chối tag hoặc digest không hợp lệ. Kiểm lại manifest, image digest,
namespace/context trước khi người điều phối apply. Đường chuẩn bị review:

```sh
AWS_PROFILE=console kubectl --context sentinelops-demo \
  apply --dry-run=server -f /tmp/fault-console.json
```

Deployment dùng một replica, Recreate, UID/GID 10001, root filesystem read-only,
drop ALL capabilities, seccomp RuntimeDefault, request/limit CPU và memory.
Exec probes đọc `/` từ localhost, không gọi faults/business endpoint. Readiness
chỉ chứng minh console phục vụ HTTP; tình trạng services nằm ở `/api/status`.
NetworkPolicy deny ingress bổ sung nếu CNI thực thi policy; loopback listener
vẫn là lớp chặn trực tiếp. Policy không chặn egress DNS/service. Namespace phải
đã tồn tại; package không tạo namespace hay thay cấu hình workload nghiệp vụ.

## Truy cập sau khi rollout được xác nhận

```sh
AWS_PROFILE=console kubectl --context sentinelops-demo \
  -n sentinelops-b-actions port-forward --address 127.0.0.1 \
  deployment/fault-console 8765:8765
```

Mở **http://127.0.0.1:8765**, dùng đúng port 8765 để khớp Host/Origin guard.
Kubernetes port-forward nối tới localhost trong pod; không đổi bind thành
`0.0.0.0` để sửa probe hoặc truy cập. Quyền `pods/portforward` là quyền truy cập
công cụ; console không có hệ thống đăng nhập riêng.

Console GET tới `payment-service:8000` và `inventory-service:8000`; xác nhận
Service port thực trước apply. Nếu endpoint faults tắt, UI phải hiển thị lỗi 404,
không tự bật biến môi trường trên business pods. Fault state thuộc từng pod:
Service có nhiều replica sẽ không đại diện trạng thái toàn deployment.

Không bật `DEMO_CONSOLE_ENABLE_FAULTS` trong vòng rollout đầu. Trước phiên bơm lỗi
thật cần kiểm target, trạng thái baseline, một replica mỗi service, đường phục hồi
và kiểm độc lập các guards. Với B7/B9, dùng console local theo README cấp trên sau
khi review script đầy đủ; image này không có quyền thay Deployment. Không thêm
credential/RBAC hoặc mount script tùy tiện để mở nút deploy image.
