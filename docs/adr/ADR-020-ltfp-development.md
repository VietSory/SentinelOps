# ADR-020 — Quyết định tạm thời cho LTFP

**Trạng thái:** Đề xuất ngày 26/11/2026. Chờ review bằng chứng và nghiệm thu Lane A.

**Liên quan:** [ADR-019](ADR-019-breach-relative-retrain.md), [kế hoạch benchmark](../delivery/MODEL_BENCHMARK_PLAN.md), [runbook](../delivery/MODEL_BENCHMARK_RUNBOOK.md), [kết quả Lane A](../delivery/MODEL_BENCHMARK_LANE_A_LOCAL.md).

## Bối cảnh

LTFP phải được so với XGBoost và slope trên cùng các window hợp lệ. Phép so graph và self-only dùng cùng đầu vào, nhãn, seed và ngân sách fit. Kết luận thay model cần bằng chứng local với oracle độ trễ, cùng ngân sách cảnh báo sai và gate Q-07.

Đã khôi phục snapshot Alibaba và bundle XGBoost `de38fc6b` theo hash của fit report. Trên 3.200 window train development, hai biến thể LTFP và XGBoost đã được fit cùng các record. Các negative train được lấy mẫu và XGBoost dùng quy tắc cân bằng lớp hiện có. Thời điểm nhận metric Alibaba được giả lập tại cuối bucket.

Tập calibration development đầy đủ có 55.730 window chung và 89 positive. AP của slope, XGBoost, LTFP self-only và LTFP graph lần lượt là 0,01607, 0,02423, 0,00233 và 0,00232. Các window này đã được dùng để hiệu chuẩn và phân tích pilot. Không có positive calibration với hàng xóm trong graph đã thử. [Evidence](../reports/evidence/ltfp-alibaba-development.json)

## Quyết định đề xuất

**KEEP** bundle đã kiểm chứng `de38fc6b` làm baseline. **INCONCLUSIVE** đối với việc thay XGBoost bằng LTFP và đối với giá trị tăng thêm của graph.

Quyết định dựa trên phạm vi bằng chứng hiện có. Chưa có parity A07 trên telemetry v2 đã lưu, oracle latency-only độc lập cho holdout local, bảng lead và false warnings theo giờ, hay khoảng tin cậy ghép cặp theo group. Kết quả development không đủ để áp dụng gate thay model hoặc quy khác biệt cho graph.

Bundle baseline có model version `de38fc6bb89896856556e13e7c6142ba21c8d0fea88ebd082f95e96d4e5b0e57` và manifest SHA-256 `d52df5846ac5027507d96b67c751f02f2c5519e3ea970fbdc3599207d18eb84e`. Gói người dùng cung cấp có bundle và fit report. Digest image, release và bằng chứng rollback của lần triển khai cần được đối chiếu riêng trước bàn giao AWS.

## Điều kiện review lại

1. Xác minh A07 với raw capture v2 và chứng minh topology calibration ngoại sinh trước cutoff.
2. Khóa protocol, ứng viên và operating point trước khi mở holdout mới. Giữ kết quả `ald` và kết quả Alibaba hiện tại trong phạm vi development.
3. Chấm bốn dòng trên cùng holdout, giữ UNKNOWN và miss trong mẫu số, đối soát event oracle cùng clock và tính bootstrap ghép cặp theo group.
4. Xác minh replay và worker, release digest, rollback, local demo và CI của đúng commit. Ghi quyết định cuối theo các gate trong kế hoạch.
