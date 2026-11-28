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

Trên epoch local `ald` ([layer 2](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md)), IF và MAD cùng đạt event F1 1,000 trên ba service, 12/12 ca latency S1/S3, 0 false incident — F1 chạm trần trên cohort development `ald` này.

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

| | Lane A: LTFP thay XGBoost | Lane B: ADSketch + MAD thay IF |
| --- | --- | --- |
| Chỉ số chính | Recall gradual ở lead ≥60 s; AP | Event F1 (rule `alibaba-proxy-event/v1`, mục 5; nhãn `> 2,0×` median) |
| Nơi so xếp hạng | Split đánh giá Alibaba chưa dùng | Split evaluation Alibaba (bucket 8–9, phút 324–352, warmup từ 259; support D1 chưa đo, cần bundle) |
| Nơi kiểm gate | Holdout local | Holdout local (Q-04..Q-06, tách khỏi `scope='alibaba-proxy-ranking'`) |
| Ưu điểm khai báo | Lượt không breach bị cảnh báo; tác dụng graph theo service có hàng xóm | (a) Cận dưới CI 95% của Δ event F1 > 0, hoặc (b) cận trên CI 95% của Δ (false incident / healthy service-hour) < 0, cùng bootstrap, coverage cặp ≥ 0,95 |
| Không dùng làm ưu điểm | Lead time local (bị horizon chặn) | MTTD (bucket 60 s, Q-06 cần clock arrival-aware); mẫu novel (chỉ 6 positive, báo NA lý do `insufficient-support`); bỏ tiêu chí cũ "recall hơn IF" và "pattern ID" |
| Báo kèm bắt buộc | Số Alibaba của LTFP dù thua | Mọi cặp trong `PAIRS` (kèm `adsketch_mad_or` − `median_mad`, `median_mad` − `isolation_forest` cùng CI) và đóng góp ADSketch kèm đối chứng `mad_at_combo_threshold` (kể cả khi bằng 0) |

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
| 3 | Ứng viên Lane B | Owner chốt ngày 27-11: ứng viên chính **ADSketch + MAD**. Cảnh báo khi một trong hai vượt ngưỡng; hai ngưỡng hiệu chuẩn chung để tổng flag trên negative ≤1% ở calibration; lưới chọn ngưỡng có trường hợp chỉ MAD. Bảng khai báo trước bốn hàng IF, MAD, ADSketch, ADSketch + MAD và báo riêng phần ADSketch đóng góp thêm so với MAD, kể cả khi bằng 0. Development: chỉ MAD recall 0,464; ADSketch bắt thêm 2 positive ngoài MAD. B triển khai. |
| 4 | Ưu điểm khai báo của Lane B | B thay bằng một chỉ số đo được có ngưỡng khai báo trước, hoặc bỏ. |
| 5 | Split evaluation có sẵn | Đúng: `split_of()` gán bucket 8–9. Split này **đã được dùng** để báo cáo baseline cũ `de38fc6b` (layer 1, XGB so với slope) và IF/MAD trong kế hoạch. Chưa ai chấm ứng viên v17 (LTFP, ADSketch) trên split này. Support ở phút 324–352, K=16: 55.901 window có nhãn, 96 positive trên 21 service, 621 window có hàng xóm, 0 positive có hàng xóm. Tác dụng graph trên Alibaba khai NA. Bootstrap theo service chỉ có 21 service dương. |
| 6a | Early stopping có dùng phút 324–352 không | Không. `fit_weighted_pair` chọn epoch trên 20% service của tập train (hash tên service), cửa sổ train 162–172. Calibration không được đọc khi fit. |
| 6b | Positive có hàng xóm ở evaluation | 0. Xem dòng #5 ở bảng trên. |
| 7 | Có nên đăng ký capture sau khi chốt Lane B | Capture cũ đã dừng nên phải đăng ký lại. Owner quyết định chạy riêng Lane A ngay hay gộp với Lane B. |

**Lệch 75 và 71 của ADSketch.** Hai số đo hai thứ khác nhau. 75 là `positive_flags` trong `freeze/candidates.json`: số positive có score ≥ ngưỡng ở budget 1%. 71 là số positive được xếp loại pattern known (65) hoặc novel (6), không xét ngưỡng. 112 positive còn lại xếp loại normal; 65 + 6 + 112 = 183.

**Bundle cho Lane B** nằm ở `artifacts/handoff-lane-b-v17` trên máy A. Gồm 120 Parquet và sidecar, `report.json` của fit, `splits.json` (`3ab8606e…`, giống v16 và v17), protocol draft v16 kèm B01–B06, freeze protocol v17 Lane A, `fit.json` của `936ae30e…`, receipt support, và `SHA256SUMS`.

## 5. Lane B: khai báo trước v17

Ngày 28-11-2026 (cập nhật SPEC v2). Mục này ghi đầy đủ các khai báo trước của Lane B v17 (đề xuất của B, chờ owner duyệt trước khi freeze; xem chi tiết tại [ADR-021](../adr/ADR-021-lane-b-v17-preregistration.md)). Holdout chưa mở; chưa có kết quả đánh giá thật trên dữ liệu Alibaba evaluation hay capture local v17.

### D1 · Event Alibaba proxy (`alibaba-proxy-event/v1`)

**Nhãn từng phút (oracle B):** dùng đúng công thức đường fit Lane B (`src/sentinelops/ml/model_benchmark_detector_fit.py`, dòng 29–33, 107–121): `y = int(latency_ms > 2,0 × baseline)` (so sánh strict `>`), với `baseline` là median của tối đa 60 phút liền trước cùng service (cần ≥ 5 phút, `baseline > 0`); bước khác 60 s hoặc latency thiếu thì xóa history. Thiếu điều kiện ⇒ `y = None` (`UNKNOWN`). Đây là nhãn từng phút, không phải nhãn anchor OR-5-phút của calibration và **không phải K=16**: con số 96 positive / 21 service ở mục 4 (#5) là support K=16 của Lane A, không áp cho Lane B; **support D1 chưa đo, cần bundle** `handoff-lane-b-v17` (B-1).

**Phạm vi và warmup:** service thuộc split evaluation (`split_of(msname) == 'evaluation'`, bucket 8–9). Khoảng chấm là phút tương đối **324–352** bao gồm hai đầu. Input nạp kèm **warmup nhân quả từ phút 259** (`= 324 − 60 − 5`) với `eligible=False` để cấp history nhãn, history detector và persistence (không chấm). Phút thiếu row (bước > 60 s) là `UNKNOWN`.

Rule `alibaba-proxy-event/v1` (ghim bằng `stable_id` của dict cấu hình):

1. **Đoạn dương:** các phút `y=1` liên tiếp (cách đúng 60 s), xét trên toàn timeline kể cả warmup.
2. **Gộp:** hai đoạn dương cách nhau `< 5` phút âm có nhãn liên tiếp và không có phút `UNKNOWN` hoặc thiếu row xen giữa thuộc cùng một event. Có `UNKNOWN` xen giữa thì không gộp.
3. **Mốc thời gian:** `onset_at` = `bucket_end` phút dương đầu tiên; `confirmed_at = onset_at`; `end_at` = `bucket_end` phút dương cuối; `deadline_at = onset_at + 300 s`; ghi `length_minutes` (elapsed bao gồm hai đầu) và `positive_minutes` (số phút `y=1`).
4. **Eligible:** `onset_at` thuộc `[324, 352]`, 5 phút ngay trước `onset_at` đều có row và `y=0`, và `onset_at + 300 s <=` `bucket_end` phút 352 (tức onset `<=` phút 347). Event có onset sau 352 không tạo. Event ineligible vẫn giữ trong ledger với `eligible=false` và `reason` theo thứ tự ưu tiên: `onset-before-scoring-range` → `insufficient-pre-onset-history` → `unknown-pre-onset-history` → `insufficient-follow-up`; không vào mẫu số recall nhưng phải đếm và báo.
5. **Match và censor:** giữ luật một-một trong `[onset, onset+300 s]` cùng run/service, lấy alert sớm nhất, alert trước phút 324 không chấm; event không có score trong cửa sổ vẫn tính miss. Alert unmatched phát trong `[onset, end_at + 300 s]` của một event ineligible mang status `censored` — không tính TP, không tính FP, có đếm và báo.
6. **Phút khỏe (`healthy_keys`):** phút trong `[324, 352]`, `y=0`, không nằm trong `[onset − 300 s, end_at + 300 s]` của bất kỳ event nào (eligible hay không). **FP rate** (`false_incidents_per_healthy_service_hour`) chỉ tính alert unmatched phát tại `healthy_keys` chia cho số giờ-service khỏe mà model có score (còn FP của event F1 là mọi alert unmatched không censored).
7. `group_id = run_id = service`, để bootstrap lấy lại nguyên service.

### D2 · Ứng viên `adsketch_mad_or` và hiệu chuẩn chung

- Bảng B có bốn hàng: `isolation_forest`, `median_mad`, `adsketch` (gọi là **"ADSketch, biến thể tìm kiếm giới hạn"** theo kết quả B-3), và `adsketch_mad_or`.
- **Score và availability:** MAD luôn bật; ADSketch bật khi `t_ads` khác `None`. Score combo = `max(s_i / t_i)` trên các thành phần đang bật có score, ngưỡng combo cố định `1,0`. Nhánh chỉ-MAD (tắt ADSketch) dùng `t_ads = None` trong API và receipt JSON; `+inf` chỉ dùng trong công thức/thứ tự tie-break (`None` xếp lớn nhất). Combo available khi ít nhất một thành phần đang bật có score (nhánh chỉ-MAD ⇒ availability đúng bằng MAD); thành phần đang bật thiếu score đóng góp 0. Hàng combo ghi `component_availability ∈ {'both', 'median_mad_only', 'adsketch_only', 'none'}` (`None` ở 3 model đơn). Vi phạm `t_mad > 0` hữu hạn, `t_ads > 0` hữu hạn (khi khác `None`) hoặc score hữu hạn `>= 0` thì báo lỗi. Persistence áp lên cờ combo như ba model kia.
- **Hiệu chuẩn chung** trên `calibration_rows` (bucket 6–7, anchor `eligible=True`, `risk_label ∈ {0, 1}`): mẫu số negative là số negative anchor mà combo available theo cặp ngưỡng đang xét, cần `>= 100` negative, ngân sách nguyên `negative_flags <= negatives // 100`. Tập ứng viên mỗi ngưỡng = `{ngưỡng solo} ∪ {score có của model đó >= ngưỡng solo}`, ADSketch thêm `None`. Tìm kiếm vét cạn, tất định: tối đa `positive_flags` → ít `negative_flags` hơn → `t_ads` lớn hơn (`None` lớn nhất) → `t_mad` lớn hơn.
- **Đóng góp ADSketch (`adsketch_increment`):** báo chênh lệch quan sát so với MAD solo (`combo_only_matched_events`, `mad_only_matched_events`, `delta_tp`, `delta_fp`, `delta_false_incidents_per_healthy_service_hour`) và thêm đối chứng **`mad_at_combo_threshold`** (combo với `t_ads=None`, cùng `t_mad` combo) để tách phần quy được cho ADSketch (`attributable_matched_events`, `attributable_false_incidents`). Đối chứng này chỉ nằm trong mục increment, không phải hàng bảng chính; báo cả khi bằng 0.
- **Công khai 3 cách dùng nhãn proxy:** (1) cả ba detector lọc normal reference bằng nhãn train; (2) ADSketch dùng nhãn dương để xác nhận pattern discovery; (3) ngưỡng combo `adsketch_mad_or` tối đa hóa trực tiếp số positive calibration bị flag, còn với IF và MAD solo thì ràng buộc budget chỉ dựa trên negative nhưng tập ngưỡng ứng viên lấy từ mọi score calibration có nhãn (kể cả positive) theo `calibrate()`, và solo không tối ưu theo số positive bị flag. Oracle chấm D1 dùng cùng họ nhãn `2,0×`, tạo lợi thế cấu trúc cho model học từ nhãn.

### D3 · Gate Lane B (`lane_b_recommendation`, đối xứng Lane A)

- **Cặp có hướng cố định (`PAIRS`):** `adsketch_mad_or−isolation_forest` (chính), `median_mad−isolation_forest`, `adsketch_mad_or−median_mad`, `adsketch−isolation_forest`, `adsketch−median_mad`. Bootstrap ghép cặp nguyên service, 1.000 lần, seed 42.
- **Coverage cặp chính:** `min(tỷ lệ phút khỏe cả hai model cùng có score, tỷ lệ event eligible mà cả hai có >= 1 score trong [onset, onset+300 s])`; ngưỡng yêu cầu `>= 0,95`.
- **Thứ tự 9 nhánh gate** (nhánh đầu tiên đúng thì trả):
  1. Thiếu/sai `rules` (`calibration_budget == 0,01`, `calibration_cohort`, `event_rule_id`, `combo_selection_rule_id`) ⇒ `INCONCLUSIVE` `rules-unverified`;
  2. `< 5` service có event eligible ⇒ `INCONCLUSIVE` `insufficient-event-support`;
  3. Coverage cặp chính `< 0,95` ⇒ `INCONCLUSIVE` `pairwise-coverage-incomplete`;
  4. CI quyết định có `< 950` replicate hợp lệ ⇒ `INCONCLUSIVE` `bootstrap-undefined`;
  5. Cận dưới CI 95% của Δ event F1 `< -0,05` (`NON_INFERIORITY_MARGIN`) ⇒ `INCONCLUSIVE` `f1-non-inferiority-not-established`;
  6. Rate ứng viên (`false_incidents_per_healthy_service_hour`) `>` IF theo điểm ước lượng ⇒ `KEEP` `higher-false-incident-cost`;
  7. Cận dưới CI 95% của Δ event F1 `> 0` ⇒ `REPLACE` `f1-improved-without-higher-cost`;
  8. Cận trên CI 95% của Δ rate `< 0` ⇒ `REPLACE` `non-inferior-with-lower-false-incident-cost`;
  9. Còn lại ⇒ `INCONCLUSIVE` `non-inferior-without-measured-advantage`.
- **Tách phạm vi:** mọi nhánh trả `scope='alibaba-proxy-ranking'`, `release='KEEP'`, `activation_allowed=False`, `runtime_parity='NOT_RUN'`, `uses_proxy_positive_labels=True`, `local_gates={'Q-04','Q-05','Q-06'}` mặc định `'NOT_RUN'`. `REPLACE` ở đây chỉ là kết quả xếp hạng Alibaba, tách khỏi gate tuyệt đối Q-04..Q-06 trên holdout local và quyết định release.
- Bỏ ưu điểm cũ "recall hơn IF" và "pattern ID". Không dùng MTTD (bucket 60 s, Q-06 cần clock arrival-aware) và không dùng mẫu novel (báo `NA`, `insufficient-support`, 6 positive) làm ưu điểm. Báo kèm mọi cặp trong `PAIRS` với CI, số replicate hợp lệ/undefined và mẫu số.

### Trạng thái B-1..B-8 (tính đến 28-11-2026)

| # | Trạng thái hiện tại | Ghi chú |
| --- | --- | --- |
| B-1 | **BLOCKED** | Chờ bundle `handoff-lane-b-v17` từ máy A để đo support D1 và fit lại trên clean commit dùng cho freeze v17 |
| B-2 | Chưa freeze | Giữ nguyên config ADSketch (`window_length=5`, 8 normal, 8 anomalous, `max_fit_windows=512`, seed 42), không tune thêm |
| B-3 | Đã đo trên fixture | Đường fit benchmark lệch reference tác giả trên fixture synthetic (gán khớp 456/536, lệch bán kính 11,2); mọi report gọi là **"ADSketch, biến thể tìm kiếm giới hạn"** |
| B-4 | Đã khai báo trước | Report ghi mẫu novel là `NA`, lý do `insufficient-support` (6 positive ở calibration development) |
| B-5 | Review xong, chờ xác nhận nguồn gốc | Review mã nguồn hỗ trợ kết luận triển khai độc lập theo bài báo, không thấy mã logic sao chép từ `OpsPAI/ADSketch` (`f7bd642c`); cần người triển khai hoặc owner xác nhận lịch sử phát triển trước khi đưa vào image |
| B-6 | **NOT_RUN** | Chưa có loader và parity runtime cho ADSketch / `adsketch_mad_or`; release giữ `KEEP` |
| B-7 | **NOT_RUN** | Hai host release rollback lịch sử vắng mặt; cần khôi phục hoặc chốt release thay thế trước khi đổi release |
| B-8 | **NOT_RUN** | Chưa có cặp Payment image đã ghim, namespace riêng và receipt rehearsal ngày 8–9 / CI đúng SHA |
