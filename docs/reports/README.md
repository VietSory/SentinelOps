# Kết quả cần giữ để dùng lại

Hiện chỉ giữ [RCA development baseline](RCA_DEVELOPMENT.md) và JSON đi kèm:
chúng cung cấp mẫu số, giới hạn evidence và mốc so sánh cho thay đổi RCA tiếp theo.

Không cần report lịch sử để xác nhận service chạy: dùng code/tests, lệnh kiểm tra
trong [runbooks](../README.md#runbooks--mở-khi-cần-thực-hiện) và CI đúng phiên bản.
Smoke PASS, sửa bug nhỏ, tiến độ phase hoặc snapshot readiness tạm thời không cần
một file riêng. [PROJECT_STATUS](../PROJECT_STATUS.md) chỉ tóm tắt capability/gap.

Chỉ thêm/cập nhật report khi có kết quả data/model/evaluation hoặc quyết định cần
đối chiếu lâu dài. Không tự tạo một report cho mỗi người, tính năng hoặc vòng test.
Input GAMMA lịch sử dùng cho regression nằm trong `tests/fixtures/`, không ở docs.
