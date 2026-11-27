# Benchmark v17: tiêu chí non-inferiority và việc cần làm trước holdout mới

Ngày 27-11-2026. Tài liệu này chốt hướng sau lần kiểm tra development ngày 27-11. Holdout `bench-final` chưa được chấm và chưa mở nhãn. Capture đã dừng sau group `bench-final-01` theo yêu cầu của owner. Các số dưới đây đều là development, không phải kết quả holdout.

## 1. Kết quả kiểm tra development

### Lane B: ADSketch, IF, Median/MAD

Nguồn: `calibration_rows` trong `preflight/detector-bound-pilot-v3/fit/b/fit.json` (fit `936ae30e…`). Có 1.967 anchor Alibaba đủ score, 183 positive proxy, mỗi anchor một service. Ngưỡng lấy từ `freeze/candidates.json` (`36a64094…`).

| Model | AP | Recall tại ngưỡng | Tỷ lệ flag trên negative |
| --- | --- | --- | --- |
| ADSketch | 0,659 | 0,410 | 0,95% |
| Isolation Forest | 0,429 | 0,158 | 0,95% |
| Median/MAD | 0,680 | 0,464 | 0,95% |

ADSketch và MAD cùng bắt 73 positive. MAD bắt thêm 12, ADSketch bắt thêm 2, 96 positive không model nào bắt. Gộp MAD hoặc ADSketch chỉ tăng recall lên 0,475 và tăng flag negative lên 1,23%. IF không bắt thêm positive nào ngoài hai model kia.

Trên epoch local `ald` ([layer 2](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md)), IF và MAD cùng đạt event F1 1,000 trên ba service, 12/12 sự kiện, 0 false incident. Local không còn chỗ để ứng viên vượt baseline.

### Lane A: LTFP, XGBoost, slope

Nguồn: `preflight/development-k16-score-v5/predictions.parquet`, phút tương đối 324–352. Có 55.730 window đủ bốn score, 2.003 service, 89 positive (tỷ lệ 0,16%). Ngưỡng lấy từ `freeze/candidates.json`.

| Model | AP | Recall tại ngưỡng | Tỷ lệ flag trên negative |
| --- | --- | --- | --- |
| XGBoost | 0,024 | 0,292 | 0,86% |
| LTFP graph | 0,002 | 0,056 | 0,93% |
| LTFP self-only | 0,002 | 0,056 | 0,95% |
| Slope | 0,016 | 0,382 | 11,95% |

AP của LTFP xấp xỉ tỷ lệ positive, tức model gần như không học được. 926 window có hàng xóm trong graph đều không có positive, nên phần graph không có tín hiệu để học. Khoảng phút này trùng khoảng dùng để chọn ngưỡng; AP không phụ thuộc ngưỡng nhưng vẫn là số development.

Nguyên nhân có khả năng cao nhất: hai model không được train ngang nhau. XGBoost dùng `scale_pos_weight` và ràng buộc đơn điệu (`src/sentinelops/ml/models.py`). LTFP chạy cố định 100 bước, batch 32, không cân lớp và không early stopping (`SETTINGS` trong `src/sentinelops/ml/ltfp_training.py`).

Trên hai lượt development local `ale-02` (ramp 400 ms, không breach), Checkout chậm cùng phút với service bị lỗi. Độ lệch 0 phút, có lúc ±1 phút, ở bucket 1 phút. Gọi đồng bộ nên graph không cho tín hiệu sớm hơn trên testbed local. Trên hai lượt này, XGBoost cảnh báo ở cả hai lượt (service gốc và Checkout, 11–13 phút mỗi service). LTFP chỉ cảnh báo Inventory ở lượt inventory, 17 phút. Ít cảnh báo hơn có thể chỉ do LTFP kém nhạy.

Trên `ald`, XGBoost cảnh báo đúng 20/20 sự kiện gradual với lead khoảng 270 s, sát horizon 300 s. Recall và lead ở local đã bão hòa.

### Lane A: fit lại LTFP có cân lớp (development)

Owner yêu cầu giữ LTFP. Train cũ không hội tụ: loss tăng từ 0,972 lên 1,604 sau 100 bước. Thử nghiệm dùng đúng 3.200 mẫu train (81 positive) và 55.730 window calibration (89 positive) như ledger `development-k16-score-v5`. Biến thể tái tạo cách train cũ cho đúng AP 0,0023, nên đường đo khớp ledger.

Cách train mới, kiến trúc `Forecast` giữ nguyên:

- `pos_weight` = số negative / số positive trong loss BCE.
- Adam, learning rate 0,001, batch 32, 30 epoch.
- Chọn epoch theo AP trên 20% service của tập train, tách theo hash tên service. Calibration không dùng để chọn epoch.
- Không chuẩn hóa feature. Chuẩn hóa làm AP giảm còn khoảng 0,027, vì feature thứ hai có độ lệch chuẩn 294.
- Lấy trung bình score của 3 seed (42, 7, 13).

Kết quả trên window calibration, ngưỡng mỗi model chọn cho ~1% flag trên negative. CI là bootstrap ghép cặp theo service, 1.000 lần, seed 42:

| Model | AP | Recall | Δ AP so với XGBoost, CI 95% | Δ recall so với XGBoost, CI 95% |
| --- | --- | --- | --- | --- |
| XGBoost | 0,0242 | 0,292 | — | — |
| LTFP graph, 3 seed | 0,0504 | 0,404 | [+0,0008; +0,0676] | [−0,045; +0,241] |
| LTFP self-only, 3 seed | 0,0613 | 0,427 | [−0,0005; +0,0926] | [−0,051; +0,314] |

Từng seed riêng lẻ: graph AP 0,042–0,055, self-only AP 0,049–0,077; mọi seed đều cao hơn XGBoost về điểm ước lượng. LTFP graph 3 seed đạt tiêu chí mục 2 trên development: không kém về recall với margin −0,05 và hơn về AP. Phần graph vẫn chưa đo được tác dụng riêng vì window có hàng xóm không có positive. Đây là development; khoảng calibration này cũng là nơi chọn ngưỡng.

## 2. Tiêu chí thay model

Ứng viên được đề xuất thay baseline khi cả hai điều kiện sau đúng:

1. **Không kém hơn.** Cận dưới CI 95% của (ứng viên − baseline) ≥ −0,05 trên chỉ số chính. CI dùng bootstrap ghép cặp 1.000 lần, seed 42, lấy lại nguyên group như runbook.
2. **Có ít nhất một ưu điểm đo được**, khai báo trước trong bảng dưới.

Các gate tuyệt đối Q-04 đến Q-07 vẫn giữ nguyên và đo trên holdout local. Quyết định release vẫn `KEEP` cho tới khi có loader và parity runtime.

| | Lane A: LTFP thay XGBoost | Lane B: ADSketch thay IF |
| --- | --- | --- |
| Chỉ số chính | Recall gradual ở lead ≥60 s; AP | Event F1 |
| Nơi so xếp hạng | Split đánh giá Alibaba chưa dùng | Split đánh giá Alibaba chưa dùng |
| Nơi kiểm gate | Holdout local | Holdout local |
| Ưu điểm khai báo | Lượt không breach bị cảnh báo; tác dụng graph theo service có hàng xóm | Recall trên Alibaba so với IF; pattern ID giải thích cảnh báo |
| Không dùng làm ưu điểm | Lead time local (bị horizon chặn) | Mẫu novel (chỉ 6 positive, báo NA) |
| Báo kèm bắt buộc | Số Alibaba của LTFP dù thua | ADSketch so với MAD (development: ngang, không hơn) |

## 3. Việc cần làm

### Lane A

1. Đưa cách train ở mục 1 vào `src/sentinelops/ml/ltfp_training.py`: `pos_weight`, 30 epoch, chọn epoch trên split service trong train, 3 seed. Receipt hiện kiểm cứng `steps == 100`; cần cập nhật receipt, test và export. Cả `ltfp_graph` và `ltfp_self_only` dùng cùng cài đặt; chỉ adjacency khác nhau.
2. Hỗ trợ trung bình 3 seed ở scorer benchmark và loader runtime, hoặc chốt một seed duy nhất và chấp nhận phương sai cao hơn.
3. Hiệu chuẩn lại ngưỡng LTFP trên calibration với budget 1%.
4. Chấm lại hai lượt local `ale-02` để xem số cảnh báo trên lượt không breach.
5. Kiểm protocol có split đánh giá Alibaba chưa dùng. Không có thì chia split mới trước khi freeze.

### Lane B

| # | Việc | Đạt khi |
| --- | --- | --- |
| B-1 | Fit lại ADSketch, IF, MAD ở clean commit dùng cho freeze v17. Checkpoint `936ae30e…` là fit thử trên source workspace của draft v5 | `fit/b/fit.json` mới ghi source commit sạch; F5 round-trip lệch ≤1e-6; điểm vận hành giữ budget 1% |
| B-2 | Không tune thêm ADSketch | Config giữ như fit hiện tại (`window_length=5`, 8 pattern normal, 8 anomalous, `max_fit_windows=512`, seed 42) |
| B-3 | Parity cho đường fit benchmark, không chỉ chế độ offline của B01 | Test so pattern và gán pattern của đường fit benchmark với reference trên input ghim. Không làm thì report gọi là "ADSketch, biến thể tìm kiếm giới hạn" |
| B-4 | B06 novel | Report ghi novel là NA, lý do thiếu support |
| B-5 | Giấy phép GPLv3 | Ghi cách đáp ứng giấy phép, hoặc xác nhận bản triển khai độc lập từ bài báo trước khi đưa vào image |
| B-6 | Loader và parity runtime cho ADSketch | Image load đúng digest đã freeze; score runtime khớp replay |
| B-7 | Rollback | Có lại host release rollback hoặc chốt release thay thế |
| B-8 | Rehearsal ngày 8–9 | Có cặp Payment image `e520e702…` / `766dd5f5…`, namespace riêng, approval on-call; receipt API/UI/Grafana và CI đúng SHA |

B-1 đến B-4 cần xong trước freeze v17. B-5 đến B-8 cần xong trước khi đổi release; chưa xong thì release giữ IF.

### Chung

1. Ghi tiêu chí mục 2 vào runbook và ADR từng lane.
2. Freeze protocol và candidates v17 cho cả hai lane.
3. Đăng ký capture mới, thu 5 group (khoảng 34 giờ). Group `bench-final-01` đăng ký với candidates `36a64094…` nên không dùng cho chấm v17; giữ nguyên dữ liệu.
4. Chấm: gate trên holdout local, xếp hạng trên split Alibaba chưa dùng. Viết ADR từng lane.

## 4. Phản hồi review ngày 27-11 và sự cố capture

**Capture v17 dừng.** Máy khởi động lại lúc khoảng 18:00 ngày 27-11. Slot healthy của `bench-final-01` (registration `5a2d7629…`) đang warmup thì dừng. Code không chạy lại slot dở dang, nên registration này bỏ. Dữ liệu giữ nguyên, chưa có slot nào hoàn tất.

| # | Góp ý | Xử lý |
| --- | --- | --- |
| 1 | Event F1 trên Alibaba chưa có định nghĩa | Thuộc Lane B. B chốt định nghĩa event và luật match trước khi freeze Lane B. |
| 2 | Gate trong code vẫn đòi cận dưới CI > 0 | Đã sửa `lane_a_recommendation`. `REPLACE` khi cận dưới CI Δ recall ≥ −0,05, chi phí cảnh báo sai không tăng, và có một ưu điểm đo được: cận dưới > 0, hoặc số lượt không breach bị cảnh báo, hoặc false warning trên giờ-service khỏe thấp hơn hẳn. Không kém nhưng không có ưu điểm thì ghi `INCONCLUSIVE`. Runbook đã cập nhật. |
| 3, 4 | Ứng viên và ưu điểm của Lane B | Owner và B chốt. Các số liệu liên quan nằm ở mục 1. |
| 5 | Split evaluation có sẵn | Đúng: `split_of()` gán bucket 8–9. Split này **đã được dùng** để báo cáo baseline cũ `de38fc6b` (layer 1, XGB so với slope) và IF/MAD trong kế hoạch. Chưa ai chấm ứng viên v17 (LTFP, ADSketch) trên split này. Support ở phút 324–352, K=16: 55.901 window có nhãn, 96 positive trên 21 service, 621 window có hàng xóm, 0 positive có hàng xóm. Tác dụng graph trên Alibaba khai NA. Bootstrap theo service chỉ có 21 service dương. |
| 6a | Early stopping có dùng phút 324–352 không | Không. `fit_weighted_pair` chọn epoch trên 20% service của tập train (hash tên service), cửa sổ train 162–172. Calibration không được đọc khi fit. |
| 6b | Positive có hàng xóm ở evaluation | 0. Xem mục 5. |
| 7 | Có nên đăng ký capture sau khi chốt Lane B | Capture cũ đã dừng nên phải đăng ký lại. Owner quyết định chạy riêng Lane A ngay hay gộp với Lane B. |

**Lệch 75 và 71 của ADSketch.** Hai số đo hai thứ khác nhau. 75 là `positive_flags` trong `freeze/candidates.json`: số positive có score ≥ ngưỡng ở budget 1%. 71 là số positive được xếp loại pattern known (65) hoặc novel (6), không xét ngưỡng. 112 positive còn lại xếp loại normal; 65 + 6 + 112 = 183.

**Bundle cho Lane B** nằm ở `artifacts/handoff-lane-b-v17` trên máy A. Gồm 120 Parquet và sidecar, `report.json` của fit, `splits.json` (`3ab8606e…`, giống v16 và v17), protocol draft v16 kèm B01–B06, freeze protocol v17 Lane A, `fit.json` của `936ae30e…`, receipt support, và `SHA256SUMS`.
