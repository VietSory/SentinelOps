# Operator UI preview local

**DỮ LIỆU GIẢ, chỉ duyệt giao diện; không dùng để kết luận về hệ thống thật.**
Không kết nối DB/RDS, Kubernetes hay model. Không dùng cho production.

Từ root repo, dùng môi trường Python đã cài dependencies của dự án:

```sh
.venv/bin/python -m scripts.ui_preview.server --port 18080
```

Mở <http://127.0.0.1:18080/> để đọc cảnh báo và lấy **token giả**, rồi mở `/ui/`,
dán token vào ô API access token và bấm **Connect**. Không nhập token thật.
Server chỉ bind `127.0.0.1`; không có tùy chọn đổi host. Ctrl+C để dừng.

Fixture dùng trực tiếp các hàm trong `scripts/check_operator_ui.py`, gồm 8 sự cố,
critical/high/medium/low, recovered/escalated và các state khác, phát hiện từ 5 phút
đến 30 giờ trước lúc khởi động. Chọn **Last 24 hours** sẽ còn 7 sự cố.
`INC-FIXTURE-A` có proposal pending: cửa sổ review/TTL đếm giảm thật trong 14 phút.
Mốc giờ giữ nguyên qua các lần poll; khởi động lại server để duyệt lượt mới.

4 module: filters, summary, deeplinks, proposalwatch. Deep links Grafana/Jaeger
trỏ đến trang ghi rõ fixture local; không mở telemetry thật. Chọn sự cố để thấy
links, explanation, timeline, actions và quarantine. Payload XSS cố ý được giữ
từ fixture kiểm thử để UI hiển thị như văn bản. Tất cả thao tác ghi trả HTTP 405.

`sentinelops.ui.routes.router` phục vụ UI/assets thật. Chỉ response HTML preview
được thêm banner cảnh báo và 2 thẻ meta cấu hình đích deeplink local. Không sửa
`routes.py` hay file nào trong `src/sentinelops/ui/assets/`; giữ nguyên CSP.
`/ready` chỉ phản ánh fixture phục vụ được, không phải sức khỏe hệ thống thật.

```sh
.venv/bin/python -m pytest -q tests/test_ui_preview.py tests/test_operator_ui.py
```

Để kiểm tra 4 module bằng trình duyệt qua HTTP thật (không chặn/thay API), giữ
server chạy và dùng môi trường đã cài extra `ui-test` cùng Chromium:

```sh
.venv/bin/python -m scripts.ui_preview.check_ui --output /tmp/operator-preview-check
```

Thư mục output phải chưa tồn tại. Có thể chọn Chrome sẵn có bằng
`--browser-executable /path/to/chrome` hoặc biến `SENTINELOPS_CHROME`.
Kết quả gồm `verification.json` và ảnh desktop/mobile.
