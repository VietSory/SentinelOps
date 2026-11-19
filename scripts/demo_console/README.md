# Console bơm lỗi cho SentinelOps dev

Trang HTML/CSS/JS tĩnh và backend FastAPI có sẵn trong repo, không thêm dependency.
**Bấm nút sẽ làm service dev hỏng thật. Mặc định chỉ đọc, mọi POST bị khóa.**
Backend chỉ bind `127.0.0.1`, một worker, không có xác thực. Không đặt sau proxy,
không mở tunnel/public ingress; chạy bằng entrypoint dưới đây, không đổi host qua uvicorn CLI.

## Chuẩn bị kết nối

Console không tạo port-forward và không gọi kubectl cho các thẻ fault mode.
Dùng các port-forward do operator đã mở, hoặc đặt URL HTTP(S) của service dev.
Mục tiêu của bài demo: EKS `sentinelops-dev`, `AWS_PROFILE=console`, context
`sentinelops-demo`, namespace `sentinelops-b-actions`. URL không chứng minh đang
trỏ đúng cụm: operator phải kiểm tra đích trước khi bật thao tác.

Ví dụ mở ở hai terminal riêng, **chỉ khi operator chuẩn bị chạy demo**:

```sh
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 deployment/payment-service 18081:8000
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 deployment/inventory-service 18082:8000
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

Tám thẻ: payment trễ 950/2000 ms, payment 503, inventory trễ 950 ms,
inventory 503, cả hai 503, khôi phục cả hai, deploy image hỏng.
Nguồn contract: `services/common/faults.py`, `services/payment/app.py`,
`services/inventory/app.py`. Chỉ có `healthy`, `latency`, `error`; không có mode image.
Mode `error` làm API nghiệp vụ trả 503. `healthy` đặt delay về 0 khi khôi phục.

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
