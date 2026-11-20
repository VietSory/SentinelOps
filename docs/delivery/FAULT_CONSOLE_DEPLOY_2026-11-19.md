# Console bơm lỗi: rollout chỉ đọc ngày 19/11/2026

Đã triển khai `deployment/fault-console` trên EKS `sentinelops-dev`, context
`sentinelops-demo`, namespace `sentinelops-b-actions`, account `833925774773`,
region `ap-southeast-1`. Rollout và GET thật qua port-forward đã thành công.
Đây là lần chạy console thật ở chế độ chỉ đọc; **chưa chạy bơm lỗi hoặc B9 thật**.

Truy cập **http://127.0.0.1:8765** khi giữ terminal sau hoạt động:

```sh
AWS_PROFILE=console kubectl --context sentinelops-demo \
  -n sentinelops-b-actions port-forward --address 127.0.0.1 \
  deployment/fault-console 8765:8765
```

Không đổi port hoặc dùng hostname `localhost`: backend kiểm chính xác peer,
Host và Origin. Console không có đường public hay đăng nhập riêng. Quyền
Kubernetes `pods/portforward` kiểm soát việc mở đường truy cập.

## Artifact và giới hạn quyền

- Image triển khai:
  `833925774773.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/application@sha256:4e1a66c446b1ceab01cda52a2abb1ed9c29dad5164e5f0330362d5ae6fdf58c0`.
- Base image pin digest; bốn dependency trực tiếp pin version. Dependency gián
  tiếp được resolve lúc build; artifact triển khai được khóa bằng digest thực.
- Source nền `160e335a7d5ed32a97eb85cdfba4fa9ab3bb4c8c`; package deployment mới
  còn trong working tree. Không coi Git SHA này là đã chứa package mới.
- Chỉ tạo Deployment, ServiceAccount không token và NetworkPolicy deny ingress.
  Không có Service, Ingress, RBAC, Secret/PVC mount hay AWS credential cho console.
- Listener giữ `127.0.0.1`; probe dùng exec GET `/` trong pod. NetworkPolicy là
  lớp bổ sung phụ thuộc CNI. Probe Ready không chứng minh business/SLO healthy.
- UID/GID 10001, root filesystem read-only, drop ALL capabilities,
  `allowPrivilegeEscalation=false`, seccomp RuntimeDefault, CPU/memory có giới hạn.
- `DEMO_CONSOLE_ENABLE_FAULTS=false`, `DEMO_CONSOLE_DEPLOY_SCRIPT` rỗng.
  Tất cả nút thao tác bị khóa; image không chứa runner B7/B9 hoặc kubectl/AWS CLI.

## Kiểm chứng

1. Review độc lập backend: **62 test offline PASS**, JavaScript syntax PASS.
2. Image chạy bằng server HTTP thật trong container **network=none**, không
   credential, root filesystem read-only: trang và hai assets HTTP 200; tám
   POST khi gate tắt đều HTTP 403; Host sai HTTP 400; upstream không truy cập
   được hiện UNKNOWN thay vì healthy. Không thể tiếp cận cụm trong phép kiểm này.
3. Server dry-run cả ba resource PASS trước apply. Rollout EKS thành công.
4. Snapshot trước/sau giữ nguyên UID, generation, hash spec và image của cả
   **12 Deployment cũ**. Thêm console nên tổng sau rollout là **13 Deployment**.
5. GET `/api/status` thật lúc **14:15:55 UTC / 21:15:55 giờ Việt Nam**:
   `enabled=false`, 8 actions, runner `configured=false`, `state=idle`;
   Payment và Inventory đúng tên, `mode=healthy`, `delay_ms=0`.
   Đây là trạng thái fault mode quan sát được, không phải bằng chứng SLO/model.

Evidence local (không commit vào Git): `.herdr/out/deploy/console-20261119/`
gồm `image.json`, `manifest.json`, `server-dry-run.log`, `apply.log`,
`before.json`, `after.json`, `live-status.json`, `source-sha256.json` và
`container-smoke.json`. Hướng dẫn tái tạo package:
[deployment README](../../scripts/demo_console/deployment/README.md).

## Điều kiện trước khi mở thao tác

Chưa bật nút deploy image hỏng. B9 đã được sửa và kiểm offline, nhưng chưa có
cơ chế giành quyền phục hồi độc quyền với controller hoặc watchdog độc lập.
Console giữ process handle/lock trong RAM; restart có thể mất lock trong khi
script tách session hoặc Job cũ còn chạy. Không gắn credential quản trị hoặc
copy script `.sh` vào pod để mở nhanh nút này.

Trước lần chạy thật cần rehearsal trong môi trường tách biệt, kiểm tranh chấp
approval/rollback, deadline, mất mạng và process/node chết; chứng minh khôi phục
image gốc và Ready hoặc báo khẩn cấp có người xử lý. Mọi thao tác fault mode
cũng cần xác nhận đúng target, baseline và đường phục hồi trước khi mở gate.

Các thay đổi ngoài console vẫn phải đối soát riêng: freshness 900 giây đã có
trong source nhưng không được rollout bằng tác vụ này; admission vẫn
Audit/Warn, không phải Deny; cảnh báo email chưa được coi là triển khai hoặc
gửi thành công từ thiết kế/manifest cũ.

## Phân công kiểm tra qua herdr

Các Codex chạy tương tác bằng `hc run --bg ... codex --no-daemon`, task cha `t6k`:

| Terminal | Công việc | Báo cáo local |
| --- | --- | --- |
| `t7h console-smoke` | Đối chiếu evidence GET và cấu hình pod; browser live chưa thực hiện | `.herdr/out/hc-console-smoke.md` |
| `t7j console-b9-review` | Rà soát B9 đã sửa, go/no-go và rehearsal cô lập | `.herdr/out/hc-b9-review.md` |
| `t7k console-handoff` | Đối soát sáu việc và sửa runbook | `.herdr/out/hc-console-handoff.md` |

`t7j` đã hoàn tất: **35 Python tests PASS**, review vẫn NO-GO cho B9 thật,
với kế hoạch rehearsal cô lập trong báo cáo. Không thực thi/source shell runner.
`t7k` đã hoàn tất bảng done/pending/evidence/next đủ sáu việc và sửa
[runbook](../runbooks/DEMO_FLOW.md): **20 fixture jq PASS**, **29 block shell
qua kiểm cú pháp**; không thực thi các lệnh demo. Hướng dẫn cũ hứa B9 tự khôi
phục đã được sửa theo hành vi từ chối fallback PATCH hiện tại.

Yêu cầu cấp quyền GET ngoài sandbox của `t7h` đã được hủy; task được thu hẹp
sang evidence đã thu, không thay người dùng chấp nhận dialog. Không có kết quả
browser live hoặc screenshot mới từ bước này.
`t7h` đã hoàn tất đối chiếu: tám action, gate/runner, identity, digest pod thực,
quyền và snapshot 12 workload cũ nhất quán. Kết quả này dựa trên evidence đã lưu,
không phải một lượt HTTP/browser live độc lập.
