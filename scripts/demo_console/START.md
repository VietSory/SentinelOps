# Khởi động Fault Console trên Windows

Sau khi operator cấu hình launcher demo cho checkout, chạy lệnh sau từ thư mục gốc repo.

```powershell
.\scripts\demo_console\start.cmd
```

Launcher của checkout mở Fault Console tại [127.0.0.1:8765](http://127.0.0.1:8765/) và dùng lại các kết nối demo đã cấu hình. Thêm `--no-browser` để chạy mà không mở trình duyệt.

## Cấu hình theo checkout

Lệnh tìm `.local-ops/start-fault-console.cmd` ở thư mục gốc. Nếu operator lưu launcher ở vị trí khác, đặt `SENTINELOPS_FAULT_CONSOLE_LAUNCHER` thành đường dẫn tuyệt đối tới file `.cmd` đó.

Với checkout mới, thiết lập quyền truy cập môi trường, hai port-forward và các runner theo [hướng dẫn Fault Console](README.md). Khi thiếu launcher, lệnh báo lỗi và dừng. Lịch sử incident và rollback được đọc từ môi trường đang kết nối.
