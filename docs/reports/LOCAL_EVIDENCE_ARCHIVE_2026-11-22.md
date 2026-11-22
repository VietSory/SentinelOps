# Bằng chứng kiểm thử local lưu ngày 22/11/2026

Các số liệu dưới đây trích từ receipt gốc của SentinelOps. Đường dẫn trong
bảng tính từ thư mục `artifacts/` trong bản sao lưu riêng. SHA-256 xác định
đúng phiên bản của từng receipt để đối chiếu lại khi cần.

| Receipt gốc | Kết quả và phạm vi | SHA-256 |
| --- | --- | --- |
| `local-http-corpus-20261104/dataset-freeze.json` | 15 nhóm đăng ký, 30 lượt chạy xác minh, 3.060 dòng feature. Receipt lịch sử ghi `READY_FOR_FITTING`; holdout chưa mở, activation chưa được phép. | `a5b628bec29f169e3957324f29a0c86e23ff5c4bc39c540f9039be7633388857` |
| `local-http-detector-events-20261108/evaluation-freeze.json` | `READY_FOR_EVALUATION`: 5 nhóm, 15 lượt chạy, 705 dòng feature. Mỗi service có 5 nhóm abrupt và 5 nhóm healthy. Số dòng fitting bằng 0. | `be5c00898a396931c998990a733d3e67dc89be84ada5e78757cb628ec9a5f6bd` |
| `evaluations/rcaeval-native-014-real-v1/verified-summary.json` | Cả 3 ca được đánh giá, cả 3 đều abstain, 0 ca đủ điều kiện tính accuracy. Lần lặp cho kết quả giống nhau. | `cd0d340ba633b4f57b5a1fec1ff3de3851ace9275961b86dbb8432679cf8e501` |
| `operations/nfr-016-native-v4/live/acceptance.json` | Tích hợp local theo đồng hồ thực: 24 batch, 72 tín hiệu và 2 bucket lỡ deadline. Checkout và Payment mỗi service ghi nhận một lần mở incident và một lần xác nhận recovery. Kiểm tra worker metrics và continuous incident đều PASS. | `626e95eec6f8d37a235ed31d9a249aa1b66ea10c4d74447a58f0376e461322e2` |
| `incidents/store-acceptance.json` | Fixture-only PASS với 9 kiểm tra. | `10a489f1d58189d2ae3074996f7f3a930973aba3896b47a0ee95af0e020ebf99` |
| `incidents/api-acceptance.json` | Fixture-only PASS với 18 kiểm tra. HTTP và PostgreSQL chạy thật với role non-owner. Tạo 3 incident; revision được kiểm tra là 2. | `9c9e75698e52bc066fafdb616c37bd30d847c60781efaf66fccb79a1122923f9` |

Trạng thái corpus HTTP phản ánh chính sách ở thời điểm thu thập. Chính sách
huấn luyện hiện hành được ghi trong
[ADR-016](../adr/ADR-016-public-production-training-source.md). Báo cáo
[G3](G3_CANDIDATE_38BCC766.md) có kết quả theo service. Báo cáo
[RCA development](RCA_DEVELOPMENT.md) là baseline để so sánh các lượt đánh
giá RCA. Các receipt trên chỉ xác minh phạm vi local hoặc fixture được nêu
trong bảng; cờ `full_G4_accepted` và `actions_allowed` đều là `false`
trong hai receipt RCAEval và NFR-016.
