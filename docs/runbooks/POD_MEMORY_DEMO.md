# Demo: duyệt phục hồi pod bị OOMKilled

Phạm vi duy nhất là Deployment `sentinelops-oom-demo`, container `oom-demo`,
namespace `sentinelops-b-actions`: một replica, image ghim digest, request RAM
`32Mi`, limit đổi đúng `64Mi → 256Mi`. Workload cấp phát cố định `128Mi` rồi phục
vụ `/health`; vì vậy `64Mi` tạo OOM thật và `256Mi` đủ chạy. Không tạo tải vào
Payment hoặc database và không thay đổi model.

Controller đọc trực tiếp sự kiện Kubernetes: container kết thúc vì `OOMKilled`
với exit code `137`, ownership Pod → ReplicaSet → Deployment, UID của cluster /
namespace / Deployment, image, cấu hình RAM, node Ready và không MemoryPressure.
Nó tạo incident cùng proposal có bằng chứng. Người trực đăng nhập UI hiện có,
xem mức RAM trước/sau và bấm Approve. Chỉ sau receipt duyệt còn hạn, controller
kiểm lại các điều kiện, dry-run rồi PATCH RAM. Nó chỉ báo recovered khi rollout
hoàn thành, pod Ready và `/health` trả đúng payload; lỗi hoặc kết quả không chắc
chắn được giữ để điều tra, không tự gửi lại PATCH mù quáng.

## Chuẩn bị

1. Build image controller có `scripts/run_pod_memory_demo.py` và package hiện
   tại. Ghim digest cho cả controller và workload. Lưu deployment controller
   trước thay đổi vào vị trí riêng tư để rollback; không đưa cấu hình runtime
   hoặc Secret vào Git/log.
2. Tạo baseline khỏe bằng renderer offline rồi review/apply manifest vào đúng
   context đã xác minh:

   ```bash
   python -m scripts.render_oom_demo --image <image@sha256:digest> --healthy > oom-workload.yaml
   kubectl --context <context> apply -f oom-workload.yaml
   kubectl --context <context> -n sentinelops-b-actions rollout status deployment/sentinelops-oom-demo --timeout=90s
   ```

3. Capture UID thực tế của namespace `kube-system` (cluster identity), namespace
   demo và Deployment mới; tạo `target.json` với các khóa `cluster_uid`,
   `namespace_uid`, `deployment_uid`, `image`. Giữ file này cùng phiên bản đã
   triển khai; xóa/tạo lại workload sẽ đổi UID và buộc bind lại policy.
4. ServiceAccount hiện có `payment-rollback` cần quyền đọc Pods/ReplicaSets/HPA,
   GET hai namespace định danh và GET node chạy workload. Quyền bổ sung chỉ cho
   GET/PATCH đúng Deployment demo và GET các node được ghim. Người có quyền quản
   trị RBAC apply manifest đã review; role Edit namespace không đủ tự cấp RBAC.
5. Capture controller mới nhất thành JSON rồi render patch. `--node-name` lặp
   lại nếu cần nhiều node đã quan sát; bỏ flag này chỉ khi GET node đã được cấp.

   ```bash
   python -m scripts.aws_demo.oom_controller --controller controller-before.json --target target.json --image <controller@sha256:digest> --policy-enabled --node-name <observed-node> --output-dir oom-render
   kubectl --context <admin-context> apply -f oom-render/oom-policy.yaml
   kubectl --context <context> -n sentinelops-b-actions patch deployment remediation-controller --type=json --patch-file oom-render/oom-controller-patch.json --dry-run=server
   kubectl --context <context> -n sentinelops-b-actions patch deployment remediation-controller --type=json --patch-file oom-render/oom-controller-patch.json
   ```

Renderer giữ nguyên container controller/UI, auth, mounts và annotations hiện
có. Sidecar dùng chung PVC `/artifacts/remediation`, token/CA đã copy và cùng
kill switch `/artifacts/remediation/KILL_SWITCH`. Nó lấy đúng Secret references
database của UI, dựng DSN `control_writer` trong RAM và không truyền password
qua command line. JSON patch có UID/resourceVersion tests; nếu conflict, capture
lại deployment và render lại, không bỏ các tests. Patch chứa snapshot cấu hình
runtime nên lưu riêng tư. Không apply lại sidecar lần hai.

## Chạy demo

1. Baseline có một pod Ready ở `256Mi`; UI và OOM sidecar đã sẵn sàng. Kiểm tra
   kill switch chưa bật và không có proposal cũ đang chờ xử lý cho lần rehearsal.
2. Capture Deployment demo mới nhất, render reset với `--fault` và review/apply
   patch để giảm limit về `64Mi`:

   ```bash
   python -m scripts.aws_demo.reset_oom_demo --deployment workload-current.json --target target.json --fault --output oom-fault.json
   kubectl --context <context> -n sentinelops-b-actions patch deployment sentinelops-oom-demo --type=json --patch-file oom-fault.json
   ```

3. Pod mới bị `OOMKilled`; UI hiển thị incident và proposal RAM. Chờ người trực
   kiểm bằng chứng rồi Approve, trong vòng 3 phút kể từ lần OOM gần nhất. Receipt
   có hạn 15 phút nhưng evidence quá 180 giây vẫn bị chặn. Trước duyệt limit vẫn
   phải là `64Mi`.
4. Theo dõi proposal qua executing/verifying đến recovered. Xác minh Deployment
   giữ nguyên UID/image/request RAM và limit thành `256Mi`, một pod Ready,
   `/health` thành công liên tục ít nhất 30 giây và incident đã có kết quả phục
   hồi. Không gọi demo đạt chỉ vì Kubernetes đã nhận PATCH.
5. Để chạy lại, bắt đầu từ baseline khỏe rồi tạo fault mới; proposal/receipt cũ
   không được tái sử dụng. Có cooldown 60 giây sau episode trước. Reject/expired
   phải giữ RAM ở `64Mi`.

## Dừng và phục hồi cấu hình

Bật kill switch chung để chặn dispatch mới. Nếu cần đưa workload về khỏe mà
không demo approve, capture lại nó và render `reset_oom_demo` **không** `--fault`
để tạo patch về `256Mi`; review/apply và kiểm rollout/probe. Đây là thao tác của
operator, không ghi thành AI recovery. Khi gỡ demo, restore cấu hình controller
đã lưu với kiểm tra phiên bản hiện tại và chỉ xóa các tài nguyên demo được tạo
trong phiên. Không xóa PVC hoặc lịch sử incident/approval. Nếu node scheduling
đổi sang node ngoài allowlist, controller phải bị chặn đến khi RBAC được quản trị
cập nhật có chủ đích.
