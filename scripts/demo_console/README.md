# Console bơm lỗi cho SentinelOps dev

Trang HTML/CSS/JS tĩnh và backend FastAPI có sẵn trong repo, không thêm dependency.
**Bấm nút sẽ làm service dev hỏng thật. Mặc định chỉ đọc, mọi POST bị khóa.**
Backend chỉ bind `127.0.0.1`, một worker, không có xác thực. Không đặt sau proxy,
không mở tunnel ra Internet hoặc public ingress; chạy bằng entrypoint dưới đây,
không đổi host qua uvicorn CLI.

## Mốc triển khai ngày 19/11/2026 và cấu hình hiện hành

Console đã được deploy riêng thành `fault-console` trên EKS `sentinelops-dev`,
namespace `sentinelops-b-actions`; xem [receipt rollout](../../docs/delivery/FAULT_CONSOLE_DEPLOY_2026-11-19.md)
và [hướng dẫn deployment](deployment/README.md).
Truy cập qua port-forward **http://127.0.0.1:8765**; backend vẫn bind loopback.
**Bản bàn giao ngày 19/11 chỉ đọc, mọi thao tác OFF**: `DEMO_CONSOLE_ENABLE_FAULTS=false`,
`DEMO_CONSOLE_DEPLOY_SCRIPT` rỗng, runner chưa cấu hình và idle. Không có runner
B7/B9 trong image, không mount token ServiceAccount/credential, không có
Service/Ingress hoặc quyền RBAC cho console.

12 Deployment cũ giữ nguyên spec/image; thêm console là **13 Deployment**.
Console là thành phần tùy chọn, không thay bước kiểm traffic 2 replica,
readiness, telemetry hoặc baseline trong [kịch bản demo](../../docs/runbooks/DEMO_FLOW.md).
GET `/api/status` lúc **14:18:49 UTC** xác nhận gate tắt, không busy,
Payment/Inventory trả `healthy`, delay 0; đây chỉ là trạng thái fault của các
endpoint được đọc, không phải bằng chứng sức khỏe nghiệp vụ/toàn bộ pod.

Trạng thái hiện tại phải đọc từ `/api/status`, không suy ra từ mốc bàn giao trên.
Renderer có `--enable-faults` để mở bảy thao tác HTTP trong phiên dev đã được
cho phép, vẫn giữ loopback và không thêm quyền Kubernetes. B7/B9 và OOM có runner
riêng; nút chưa đủ cấu hình phải bị khóa và hiển thị lý do. B9 mới có kiểm thử
offline, chưa có bằng chứng E2E và chưa có watchdog
độc lập; khi controller không rollback, B9 báo URGENT và từ chối fallback PATCH,
không bảo đảm tự phục hồi. Chưa dùng nút deploy B9 cho đến khi owner chốt quyền
rollback độc quyền và đường khôi phục.

## Chuẩn bị kết nối

Phần này dành cho chạy backend local. Nếu đang dùng port-forward tới
`fault-console` trên cổng 8765, dùng phiên đó; không khởi động thêm backend
local trên cùng cổng. Pod console dùng URL Service nội bộ theo tài liệu deployment.

Console không tạo port-forward và không gọi kubectl cho các thẻ fault mode.
Dùng các port-forward do operator đã mở, hoặc đặt URL HTTP(S) của service dev.
Mục tiêu của bài demo: EKS `sentinelops-dev`, `AWS_PROFILE=console`, context
`sentinelops-demo`, namespace `sentinelops-b-actions`. URL không chứng minh đang
trỏ đúng cụm: operator phải kiểm tra đích trước khi bật thao tác.

Mở mỗi port-forward ở một terminal riêng khi chuẩn bị demo. Dùng Service để
tunnel chọn Pod hiện tại. Nếu rollout làm mất kết nối với Pod, chạy lại tunnel
rồi xác minh endpoint trước khi kích hoạt fault.

```sh
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 service/payment-service 18081:8000
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 service/inventory-service 18082:8000
```

Trong terminal console, từ thư mục gốc repo:

```sh
export DEMO_CONSOLE_PAYMENT_URL=http://127.0.0.1:18081
export DEMO_CONSOLE_INVENTORY_URL=http://127.0.0.1:18082
.venv/bin/python -B -m scripts.demo_console
```

Mở `http://127.0.0.1:8765` (dùng đúng IP, không dùng hostname localhost).
Trang vẫn mở được khi service mất kết nối; từng thẻ hiện lỗi thật, không suy đoán healthy.
GET/POST faults trên service chỉ có khi `ENABLE_FAULT_INJECTION=true`,
`DEPLOYMENT_ENVIRONMENT=dev`, `ALLOW_DEV_FAULT_INJECTION=true`; console không thay các biến này.

## Bật thao tác có chủ đích

Dừng phiên chỉ đọc, rồi chỉ khi đã sẵn sàng làm hỏng dev:

```sh
DEMO_CONSOLE_ENABLE_FAULTS=true .venv/bin/python -B -m scripts.demo_console
```

| Biến môi trường | Mặc định / tác dụng |
| --- | --- |
| `DEMO_CONSOLE_ENABLE_FAULTS` | `false`; chỉ `true` (không phân biệt hoa/thường) mở POST, kể cả khôi phục và deploy |
| `DEMO_CONSOLE_PAYMENT_URL` | `http://127.0.0.1:18081`; origin HTTP(S), không credentials/path/query |
| `DEMO_CONSOLE_INVENTORY_URL` | `http://127.0.0.1:18082`; quy tắc tương tự |
| `DEMO_CONSOLE_PORT` | `8765`; chỉ thay port, không thay địa chỉ bind |
| `DEMO_CONSOLE_DEPLOY_SCRIPT` | Rỗng; đường dẫn tuyệt đối đến script Bash B7 hoặc B9 đã được operator review |
| `DEMO_CONSOLE_OOM_SCRIPT` | Rỗng; đường dẫn tuyệt đối đến Bash wrapper kích hoạt OOM của workload demo riêng |
| `DEMO_CONSOLE_ENABLE_OOM` | `false`; gate riêng của nút OOM, chỉ bật sau khi workload/controller và adapter đã được kiểm tra |

Chín thẻ: payment trễ 950/2000 ms, payment 503, inventory trễ 950 ms,
inventory 503, cả hai 503, khôi phục cả hai, deploy image hỏng và OOM.
Nguồn contract: `services/common/faults.py`, `services/payment/app.py`,
`services/inventory/app.py`. Chỉ có `healthy`, `latency`, `error`; không có mode image.
Mode `error` làm API nghiệp vụ trả 503. `healthy` đặt delay về 0 khi khôi phục.

## Terminal lệnh và kết quả

Khung **Commands and output** bên dưới các thẻ hiển thị request/lệnh thực sự
được backend gửi, kết quả HTTP, trạng thái chưa rõ khi mất phản hồi, log runner
và mã thoát. HTTP faults dùng HTTPX; dòng request trong terminal không có nghĩa
backend tạo thêm một process `curl`. Không có ô nhập lệnh shell tùy ý.
Nhật ký lệnh giữ tối đa 200 bản ghi trong bộ nhớ; log từng runner tối đa 8 KiB
được hiển thị, đã lọc credential và ký tự điều khiển. Poll `/api/status` không
tạo thêm nhật ký thực thi. Nhật ký này phục vụ quan sát phiên, không thay durable
incident/action receipts hoặc chứng minh dịch vụ đã hồi phục.

## Kịch bản OOM đang chuẩn bị

OOM dùng **Deployment `sentinelops-oom-demo` riêng** trong
`sentinelops-b-actions`, container `oom-demo`, một replica, image pin digest.
Workload cấp phát hữu hạn 128 MiB; baseline limit 256Mi và trigger giảm limit
về 64Mi. Đây không phải HTTP fault mode của Payment/Inventory. Console không
tạo workload hoặc tự tăng RAM để hồi phục: controller OOM riêng phải quan sát
`OOMKilled`, tạo proposal, nhận **Approve** trên Operator UI rồi mới đổi
64Mi → 256Mi theo policy đã triển khai.

Adapter `trigger_oom.py` chạy trên **máy operator có kubectl**, qua wrapper
`trigger_oom.sh`. Nó yêu cầu identity đã kiểm chứng của cluster, namespace,
Deployment, image và controller. File JSON cấu hình có hình dạng sau; thay
mọi giá trị minh họa bằng đúng metadata triển khai đã review, không dùng tên
controller như bằng chứng nó đã tồn tại:

```json
{
  "target": {
    "cluster_uid": "ACTUAL_KUBE_SYSTEM_NAMESPACE_UID",
    "namespace_uid": "ACTUAL_DEMO_NAMESPACE_UID",
    "deployment_uid": "ACTUAL_OOM_DEPLOYMENT_UID",
    "image": "YOUR_REGISTRY/control-plane@sha256:ACTUAL_64_HEX_DIGEST",
    "namespace": "sentinelops-b-actions",
    "deployment": "sentinelops-oom-demo",
    "container": "oom-demo"
  },
  "controller": {
    "deployment": "YOUR_OOM_CONTROLLER_DEPLOYMENT",
    "uid": "ACTUAL_CONTROLLER_DEPLOYMENT_UID",
    "configmap": "YOUR_CONTROLLER_TARGET_CONFIGMAP",
    "key": "target.json"
  }
}
```

`target` trùng chính xác JSON `MemoryTarget` mà OOM controller đọc. Controller
phải có đúng một container chạy `python -m scripts.run_pod_memory_demo` hoặc
script `/.../run_pod_memory_demo.py`, args `--policy-enabled --target /target/target.json`
cùng các args store/token/CA/server/kill-switch hiện có. ConfigMap được chỉ
định chứa key `target.json` là JSON của **riêng object `target`**, mount
read-only tại `/target`, không dùng subPath. Adapter xác minh đúng đường mount,
UID, image pin digest, replica và readiness; dòng JSON state mới nhất của
controller phải là `observing/no-current-oom-at-low-limit`, tối đa 10 giây.
Proposal pending, controller chưa chạy, cooldown hoặc cấu hình không khớp đều
chặn trigger. Việc cấu hình này không triển khai controller thay cho công việc OOM.

Trước khi nối nút, kiểm tra bằng đường chỉ đọc:

```sh
export DEMO_CONSOLE_OOM_CONFIG=/absolute/path/to/oom-console-config.json
export DEMO_CONSOLE_KUBECONFIG=/absolute/path/to/reviewed-kubeconfig
export DEMO_CONSOLE_KUBE_CONTEXT=sentinelops-demo
python3 -B -m scripts.demo_console.trigger_oom --check
```

Chỉ sau khi workload/controller đã sẵn sàng và kiểm tra trên đạt:

```sh
export DEMO_CONSOLE_OOM_SCRIPT=/absolute/path/to/repo/scripts/demo_console/trigger_oom.sh
export DEMO_CONSOLE_ENABLE_OOM=true
export DEMO_CONSOLE_OOM_EXECUTE=1
DEMO_CONSOLE_ENABLE_FAULTS=true .venv/bin/python -B -m scripts.demo_console
```

Nút OOM chỉ kích hoạt wrapper sau khi bấm xác nhận. Adapter kiểm cluster/namespace
UID, image/resources/replicas/owner, không có HPA và node không MemoryPressure;
PATCH dùng test UID/resourceVersion/generation/toàn bộ spec rồi chỉ thay memory
256Mi → 64Mi. Không gửi lại PATCH khi kết quả chưa rõ. Nó chờ tối đa khoảng
120 giây cộng timeout các lần đọc đang dở để tìm termination mới có
`reason=OOMKilled`, exit137, Pod UID và đúng ReplicaSet owner. Exit137 riêng lẻ
không đủ. Timeout trả UNKNOWN; exit0 chỉ chứng minh đã thấy OOM, không chứng minh
recovery. Không có fallback tăng RAM hoặc tự bấm Approve. File lock mặc định
`/tmp/sentinelops-oom-trigger.lock` ngăn hai wrapper OOM chạy đồng thời trên cùng
máy; đổi bằng `DEMO_CONSOLE_OOM_LOCK_FILE` khi cần một path tuyệt đối được quản lý.
Toàn bộ PATCH được truyền qua stdin; terminal chỉ hiện argv thật và tóm tắt CAS/RAM
để không phát tán environment/annotation có thể nằm trong spec Kubernetes.

Image EKS mỏng hiện không có kubectl hoặc credential cho adapter này. Khi chỉ
rollout console HTTP, giữ `DEMO_CONSOLE_OOM_SCRIPT` rỗng. Muốn chạy OOM từ pod
phải có bản đóng gói/dependency và quyền target được review riêng; không mount
kubeconfig admin vào console để mở nút.

## Script deploy image ngoài repo

Backend **không hardcode thư mục chứa script**, không nhận command/path từ HTTP.
Nút deploy dùng `bash <đường-dẫn-từ-env>` không qua shell interpolation, cwd là gốc repo.
Giữ nguyên các file hỗ trợ/cấu trúc mà script gốc yêu cầu; trỏ tới script đã review,
không chỉ sao chép riêng file `.sh`. Operator vẫn phải bật gate riêng của script;
console không tự đặt `B7_EXECUTE`/`B9_EXECUTE`.

Ví dụ B7 (thay đường dẫn minh họa bằng file thật đã được review):

```sh
export DEMO_CONSOLE_DEPLOY_SCRIPT=/absolute/path/to/b7_run.sh
export B7_EXECUTE=1
export AWS_PROFILE=console
DEMO_CONSOLE_ENABLE_FAULTS=true .venv/bin/python -B -m scripts.demo_console
```

Hoặc B9: đặt đường dẫn tới `b9_hold.sh`, `B9_EXECUTE=1`, tùy chọn
`B9_WAIT_SECONDS=720` (script hỗ trợ 1..900 giây). Không truyền tham số tùy ý từ browser.
Không có cấu hình hợp lệ thì nút deploy bị khóa và API trả lỗi rõ ràng.
Script có thể tạo Job/ConfigMap và đổi image thật khi operator bấm nút.

POST deploy trả 202 khi đã tạo tiến trình, không chờ script nhiều phút.
Trang đọc trạng thái tiến trình/mã thoát và 8 KiB log cuối mỗi 5 giây; không suy ra
deployment khỏe từ mã thoát 0 hay từ `mode=healthy`. Script chạy bằng môi trường
được kế thừa, chịu trách nhiệm rollout/rollback và bằng chứng riêng của nó.
Trong lúc script chạy, các POST khác trả 409 để không chen vào luồng khôi phục.
Giữ backend chạy đến khi script kết thúc. Backend không kill script khi shutdown;
script tách session để còn hoàn tất cleanup, nhưng khởi động lại console sẽ mất
handle/log và khả năng khóa phiên script cũ. Phải kiểm tra script cũ trước khi mở phiên mới.

## Ý nghĩa trạng thái và giới hạn

- Mỗi thẻ hiện kết quả **GET thực tế**, tự làm mới 5 giây, timeout mỗi request 3 giây;
  không dùng trạng thái mong muốn hoặc phản hồi POST để giả lập trạng thái hiện tại.
- GET kiểm tra đúng tên service; sai JSON/tên, timeout, HTTP lỗi đều hiện chưa đọc được.
  Request không tự retry, không theo redirect, không dùng proxy từ môi trường.
- POST các thẻ fault là best effort từng service, không phải giao dịch nguyên tử.
  Sau POST, GET lại để xác nhận. Lỗi từng phần được trả riêng (HTTP 502); không che
  mất service đã đổi. Timeout POST có thể xảy ra sau khi service đã áp dụng.
- Fault state nằm trong RAM **từng process/pod**. Nếu nhiều replica, URL cân bằng tải
  không đại diện tất cả pod; port-forward tới deployment cũng chỉ chọn một pod.
  Rollout image có thể đóng port-forward; operator cần nối lại đúng instance.
- “KHÔI PHỤC” reset fault mode của hai endpoint, không rollback image và không chứng
  minh sức khỏe nghiệp vụ. Thẻ deploy cũng hiện GET faults của payment, còn image
  luôn ghi chưa xác minh qua API này.
- Host, IP client, Origin và header thao tác được kiểm tra; không bật CORS.
  Đây là công cụ localhost, không phải hệ thống phân quyền cho nhiều người.

## Kiểm thử an toàn

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_demo_console.py
node --check scripts/demo_console/static/app.js
```

Test dùng FastAPI TestClient, HTTPX MockTransport và subprocess mock: không mở
port-forward, không gọi service thật, không chạy B7/B9, không chạm cụm.

Kiểm tra browser tùy chọn nếu môi trường đã có Playwright (`ui-test`) và Chromium:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m scripts.demo_console.check_ui
```

Có thể chọn Chrome sẵn có bằng `--browser-executable /absolute/path/to/chrome`
hoặc `SENTINELOPS_CHROME`. Trên NixOS cần môi trường thư viện C++ phù hợp cho
greenlet/Playwright (`LD_LIBRARY_PATH`). Script này chặn **mọi** request trong
browser, kiểm tra desktop/mobile và nút bấm bằng fixture; không chạy backend.
