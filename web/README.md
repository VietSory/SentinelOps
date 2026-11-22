# SentinelOps Web UI

Giao diện React và TypeScript của SentinelOps. Có chế độ xem dữ liệu mẫu để
trình diễn và workspace kết nối với backend. Nút **VI | EN** đổi ngôn ngữ trong
cùng phiên.

## Chạy local / Run locally

```bash
cd web
npm ci
npm run dev
```

- **Dữ liệu mẫu:** [tiếng Việt](http://127.0.0.1:5173/ui/demo/?lang=vi) hoặc [English](http://127.0.0.1:5173/ui/demo/?lang=en). Phê duyệt và phục hồi ở đây diễn ra trong mô phỏng trên trình duyệt.
- **Workspace kết nối backend:** [mở trang gốc](http://127.0.0.1:5173/). Dùng kết nối từ [testbed local](../docs/runbooks/LOCAL_TESTBED.md) hoặc môi trường đã triển khai.

## Đóng gói để triển khai

Trong cấu hình EKS, `/ui/demo/` phục vụ mô phỏng, còn `/ui/` dùng Cognito
cho workspace vận hành. Môi trường mới lấy domain từ
[quy trình dựng AWS](../scripts/aws_demo/REBUILD.md).

Để đóng gói, chạy `npm ci` và `VITE_BASE_PATH=/ui/demo/ npm run build` trong thư mục `web/`. Dockerfile tại `deployment/Dockerfile` nhận image digest hiện tại của container `operator-ui` qua `OPERATOR_IMAGE`, user của image gốc qua `OPERATOR_USER` và commit qua `SOURCE_REVISION`. Dùng thư mục `web/` làm build context.
