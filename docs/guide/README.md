# Hiểu SentinelOps

Nếu muốn chạy thử, bắt đầu ở [README](../../README.md). Mục này giúp đọc thiết
kế và lần theo code sau khi đã biết mình muốn tìm gì.

1. [Tổng quan và kiến trúc](01_OVERVIEW.md) giải thích testbed, control plane
   và ranh giới trách nhiệm giữa các thành phần.
2. [Model và dữ liệu](02_AI_TRAINING_DATA.md) mô tả nguồn Alibaba, cách huấn
   luyện và vai trò của telemetry local trong đánh giá.
3. [Luồng xử lý sự cố](03_INCIDENT_FLOW.md) đi từ tín hiệu tới IER, RCA,
   phê duyệt và xác minh hồi phục.
4. [Snapshot kiểm thử ngày 18/11/2026](04_LOCAL_BUILD_STATUS.md) lưu kết quả
   kiểm tra tại thời điểm đó. Xem [trạng thái dự án](../PROJECT_STATUS.md)
   và CI theo commit để đối chiếu hiện tại.

[Tra cứu runbooks, ADR và báo cáo](../README.md)
