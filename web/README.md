# SentinelOps Web UI (Bilingual VI / EN)

Modern white-and-orange SaaS interface for **SentinelOps** built with **React + TypeScript + Tailwind CSS + shadcn/ui + Framer Motion + Recharts + React Flow (`@xyflow/react`)**, supporting instant **Vietnamese / English (`VI | EN`)** switching.

## Chạy local / Run locally

```bash
cd web
npm install
npm run dev
```

- Tiếng Việt (mặc định): `http://127.0.0.1:5173/?lang=vi`
- English: `http://127.0.0.1:5173/?lang=en`
- Có thể bấm trực tiếp nút **`VI | EN`** ở góc phải thanh Topbar để chuyển đổi song ngữ tức thì mà vẫn giữ nguyên trạng thái phiên đang thao tác.

## Bản demo trên EKS

Mở [SentinelOps Demo](https://dr76usfhbe2w8.cloudfront.net/ui/demo/). Giao diện dùng dữ liệu mẫu, mô phỏng phê duyệt và khắc phục trong trình duyệt. Workspace vận hành dùng Cognito tại [`/ui/`](https://dr76usfhbe2w8.cloudfront.net/ui/).

Để đóng gói, chạy `npm ci` và `VITE_BASE_PATH=/ui/demo/ npm run build` trong thư mục `web/`. Dockerfile tại `deployment/Dockerfile` nhận image digest hiện tại của container `operator-ui` qua `OPERATOR_IMAGE`, user của image gốc qua `OPERATOR_USER` và commit qua `SOURCE_REVISION`. Dùng thư mục `web/` làm build context.
