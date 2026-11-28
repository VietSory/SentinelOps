# ADR-021 — Khai báo trước Lane B v17 (event Alibaba proxy, ứng viên `adsketch_mad_or` và gate)

**Trạng thái:** Proposed (Đề xuất ngày 28/11/2026, cập nhật SPEC v2). Chờ owner duyệt trước khi freeze Lane B v17.

**Liên quan:** [ADR-019](ADR-019-breach-relative-retrain.md), [ADR-020](ADR-020-ltfp-development.md), [kế hoạch v17](../delivery/MODEL_BENCHMARK_V17_PLAN.md), [runbook](../delivery/MODEL_BENCHMARK_RUNBOOK.md), [Lane B local](../delivery/MODEL_BENCHMARK_LANE_B_LOCAL.md).

## Bối cảnh

Sau lần kiểm tra development ngày 27/11/2026 trên `calibration_rows` của `preflight/detector-bound-pilot-v3/fit/b/fit.json` (fit `936ae30e…`, 1.967 anchor Alibaba đủ score, 183 positive proxy; ngưỡng từ `freeze/candidates.json` `36a64094…`), ba detector đạt kết quả development:

- Isolation Forest: AP 0,429, recall 0,158, tỷ lệ flag trên negative 0,95%.
- ADSketch (biến thể tìm kiếm giới hạn): AP 0,659, recall 0,410, tỷ lệ flag trên negative 0,95%.
- Median/MAD: AP 0,680, recall 0,464, tỷ lệ flag trên negative 0,95%.

ADSketch và Median/MAD cùng bắt 73 positive; MAD bắt thêm 12, ADSketch bắt thêm 2, 96 positive không model nào bắt. Gộp OR trực tiếp hai ngưỡng solo chỉ tăng recall lên 0,475 nhưng đẩy tỷ lệ flag trên negative lên 1,23% (vượt budget 1%). Trên 2.039 anchor calibration, chỉ có 65 known và 6 novel mang nhãn proxy dương (72 anchor thiếu score). F1 chạm trần trên cohort development `ald`: 12/12 ca latency S1/S3, ba service, IF và MAD cùng đạt event F1 1,000 và 0 false incident ([layer 2](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md)).

Ngày 27/11/2026, owner chốt ứng viên chính của Lane B là **ADSketch + MAD** (`adsketch_mad_or`), yêu cầu chốt định nghĩa event trên Alibaba, hiệu chuẩn chung hai ngưỡng trong cùng budget 1% và thay ưu điểm khai báo bằng chỉ số đo được. Capture `bench-final-01` đã dừng do máy khởi động lại khi slot healthy đang warmup; holdout `bench-final` chưa mở nhãn và chưa có kết quả chấm thật.

## Quyết định đề xuất

Đề xuất khóa ba phần đặc tả sau (SPEC v2) cho Lane B v17 trước khi freeze protocol và candidates.

### 1. Định nghĩa event Alibaba proxy (`alibaba-proxy-event/v1`)

- **Nhãn từng phút (oracle B):** dùng đúng công thức đường fit Lane B (`src/sentinelops/ml/model_benchmark_detector_fit.py`, dòng 29–33, 107–121): `y = int(latency_ms > 2,0 × baseline)` (so sánh strict `>`), với `baseline = median` của tối đa 60 phút liền trước cùng service, cần ≥ 5 phút và `baseline > 0`; bước khác 60 s hoặc latency thiếu thì xóa history. Thiếu điều kiện ⇒ `y = None` (`UNKNOWN`). Đây là nhãn từng phút, không phải nhãn anchor OR-5-phút của calibration và **không phải K=16**. Con số 96 positive / 21 service ở mục 4 kế hoạch v17 là support K=16 của Lane A, không áp cho Lane B; **support D1 chưa đo, cần bundle** `handoff-lane-b-v17`.
- **Phạm vi và warmup:** các service thuộc split evaluation (`split_of(msname) == 'evaluation'`, bucket 8–9), khoảng chấm là phút tương đối **324–352** bao gồm hai đầu. Input nạp kèm **warmup nhân quả từ phút 259** (`= 324 − 60 − 5`) với `eligible=False` cho history nhãn, history detector và persistence (không chấm). Phút thiếu row (bước > 60 s) là `UNKNOWN`.
- **Tạo và gộp event:** xét trên toàn timeline kể cả warmup, các phút `y=1` liên tiếp (cách đúng 60 s) trên cùng service tạo thành một đoạn dương. Hai đoạn dương cách nhau `< 5` phút âm có nhãn liên tiếp và không có `UNKNOWN`/thiếu row xen giữa được gộp thành một event; nếu có `UNKNOWN` xen giữa thì không gộp.
- **Mốc thời gian:** `onset_at` = `bucket_end` phút dương đầu tiên, `confirmed_at = onset_at`, `end_at` = `bucket_end` phút dương cuối, `deadline_at = onset_at + 300 s`; lưu `length_minutes` (số phút elapsed gồm hai đầu) và `positive_minutes` (số phút `y=1`).
- **Điều kiện eligible và thứ tự ưu tiên reason:** `onset_at` thuộc `[324, 352]`, 5 phút ngay trước `onset_at` đều có row và `y=0`, và `onset_at + 300 s <=` `bucket_end` phút 352 (tức onset `<=` phút 347). Không tạo event có onset sau 352. Event ineligible vẫn giữ trong ledger với `eligible=false` và lấy reason đầu tiên đúng theo thứ tự: `onset-before-scoring-range` → `insufficient-pre-onset-history` → `unknown-pre-onset-history` → `insufficient-follow-up`; không vào mẫu số recall nhưng phải đếm và báo.
- **Match, censor và phút khỏe:** giữ luật match một-một trong `[onset, onset+300 s]` cùng run/service (alert sớm nhất khớp, alert trước phút 324 không chấm, thiếu score vẫn tính miss). **Censor:** alert unmatched phát trong `[onset, end_at + 300 s]` của một event ineligible mang status `censored` — không TP, không FP, có đếm và báo. **Phút khỏe (`healthy_keys`)** là phút trong `[324, 352]`, `y=0`, không nằm trong `[onset − 300 s, end_at + 300 s]` của bất kỳ event nào (eligible hay không). **FP rate** (`false_incidents_per_healthy_service_hour`) chỉ tính alert unmatched phát tại `healthy_keys` chia cho số giờ-service khỏe mà model có score (còn FP của event F1 là mọi alert unmatched không censored).
- **Nhóm bootstrap:** đặt `group_id = run_id = service` để bootstrap lấy lại nguyên service.

### 2. Ứng viên `adsketch_mad_or`, hiệu chuẩn chung và tên hàng ADSketch

- **Bảng B bốn hàng:** `isolation_forest`, `median_mad`, `adsketch` (gọi là **"ADSketch, biến thể tìm kiếm giới hạn"**), và `adsketch_mad_or`.
- **Kết quả kiểm tra B-3 (fixture synthetic):** đường fit benchmark lệch reference tác giả (`OpsPAI/ADSketch` tại `f7bd642c`) trên fixture 596 cửa sổ reference và 536 discovery — gán pattern gần nhất cùng loại khớp **456/536** (85,1%), lệch bán kính tuyệt đối **11,2** (`11,20480550`, đơn vị scaled). Do đó mọi report và bảng kết quả phải gọi hàng này là **"ADSketch, biến thể tìm kiếm giới hạn"**.
- **Score và availability:** MAD luôn bật; ADSketch bật khi `t_ads` khác `None`. Score combo = `max(s_i / t_i)` trên các thành phần đang bật có score, ngưỡng combo cố định `1,0`. Nhánh chỉ-MAD (tắt ADSketch) dùng `t_ads = None` trong API và receipt JSON; `+inf` chỉ biểu thị trong công thức toán học (đóng góp ADSketch bằng 0) và thứ tự tie-break (`None` xếp lớn nhất). Combo available ⇔ ít nhất một thành phần đang bật có score (nhánh chỉ-MAD ⇒ availability đúng bằng MAD); thành phần đang bật thiếu score đóng góp 0. Hàng combo ghi `component_availability ∈ {'both', 'median_mad_only', 'adsketch_only', 'none'}` (`None` ở 3 model đơn). Raise khi `t_mad` hoặc `t_ads` (khi khác `None`) không hữu hạn `> 0`, hoặc score không phải `None`/hữu hạn `>= 0`. Persistence áp lên cờ combo như ba model kia.
- **Hiệu chuẩn chung (tất định, vét cạn):** thực hiện trên `calibration_rows` (bucket 6–7, anchor `eligible=True`, `risk_label ∈ {0, 1}`). Mẫu số negative là negative anchor mà combo available theo cặp ngưỡng đang xét, cần `>= 100` negative, ngân sách nguyên `negative_flags <= negatives // 100`. Tập ứng viên mỗi ngưỡng = `{ngưỡng solo} ∪ {score có của model đó >= ngưỡng solo}`, ADSketch thêm `None`. Chọn cặp tối đa `positive_flags` → ít `negative_flags` hơn → `t_ads` lớn hơn (`None` lớn nhất) → `t_mad` lớn hơn. Điểm vận hành lưu ở `calibration['adsketch_mad_or']`; score và `verify-report` replay dùng chung `append_combo_predictions`.
- **Đóng góp ADSketch (`adsketch_increment`):** báo chênh lệch quan sát so với MAD solo (`combo_only_matched_events`, `mad_only_matched_events`, `delta_tp`, `delta_fp`, `delta_false_incidents_per_healthy_service_hour`) và thêm đối chứng **`mad_at_combo_threshold`** (combo với `t_ads=None`, cùng `t_mad` combo) để tách phần quy được cho ADSketch (`attributable_matched_events`, `attributable_false_incidents`). Đối chứng này chỉ nằm trong mục increment, không phải hàng bảng chính; báo cả khi bằng 0.
- **Công khai bắt buộc 3 cách dùng nhãn proxy:**
  1. Cả ba detector lọc normal reference bằng nhãn train.
  2. ADSketch dùng nhãn dương để xác nhận pattern discovery.
  3. Ngưỡng combo `adsketch_mad_or` tối đa hóa trực tiếp số positive calibration bị flag; với IF và MAD solo, ràng buộc budget chỉ dựa trên negative nhưng tập ngưỡng ứng viên vẫn lấy từ mọi score calibration có nhãn (kể cả positive) theo `calibrate()`, và solo không tối ưu theo số positive bị flag.
  Oracle chấm D1 dùng cùng họ nhãn `2,0×`, nên tạo lợi thế cấu trúc cho các model học từ nhãn. Phải ghi công khai này trong mọi report và ADR kết quả của Lane B.

### 3. Gate Lane B (`lane_b_recommendation`, đối xứng Lane A)

- **Cặp có hướng cố định (`PAIRS`):** `adsketch_mad_or−isolation_forest` (chính), `median_mad−isolation_forest`, `adsketch_mad_or−median_mad`, `adsketch−isolation_forest`, `adsketch−median_mad`. Bootstrap ghép cặp nguyên service, 1.000 lần, seed 42.
- **Coverage cặp chính:** `min(tỷ lệ phút khỏe cả hai model cùng có score, tỷ lệ event eligible mà cả hai có >= 1 score trong [onset, onset+300 s])`; ngưỡng yêu cầu `>= 0,95`.
- **Thứ tự 9 nhánh gate** (nhánh đầu tiên đúng thì trả):
  1. Thiếu/sai `rules` (`calibration_budget == 0,01`, `calibration_cohort`, `event_rule_id`, `combo_selection_rule_id`) ⇒ `INCONCLUSIVE` `rules-unverified`.
  2. `< 5` service có event eligible ⇒ `INCONCLUSIVE` `insufficient-event-support`.
  3. Coverage cặp chính `< 0,95` ⇒ `INCONCLUSIVE` `pairwise-coverage-incomplete`.
  4. CI quyết định có `< 950` replicate hợp lệ ⇒ `INCONCLUSIVE` `bootstrap-undefined`.
  5. Cận dưới CI 95% của Δ event F1 `< −0,05` (`NON_INFERIORITY_MARGIN`) ⇒ `INCONCLUSIVE` `f1-non-inferiority-not-established`.
  6. Rate ứng viên (`false_incidents_per_healthy_service_hour`) `>` IF (điểm ước lượng) ⇒ `KEEP` `higher-false-incident-cost`.
  7. Cận dưới CI 95% của Δ event F1 `> 0` ⇒ `REPLACE` `f1-improved-without-higher-cost`.
  8. Cận trên CI 95% của Δ rate `< 0` ⇒ `REPLACE` `non-inferior-with-lower-false-incident-cost`.
  9. Còn lại ⇒ `INCONCLUSIVE` `non-inferior-without-measured-advantage`.
- **Tách phạm vi và giới hạn:** mọi nhánh trả `scope='alibaba-proxy-ranking'`, `release='KEEP'`, `activation_allowed=False`, `runtime_parity='NOT_RUN'`, `uses_proxy_positive_labels=True`, `local_gates={'Q-04','Q-05','Q-06'}` mặc định `'NOT_RUN'`. `REPLACE` ở đây chỉ là kết quả xếp hạng Alibaba, tách riêng khỏi gate tuyệt đối Q-04..Q-06 trên holdout local và quyết định release. Bỏ ưu điểm cũ "recall hơn IF" và "pattern ID". Không dùng MTTD làm ưu điểm (bucket 60 s; Q-06 cần clock arrival-aware). Mẫu novel khai báo `NA` với lý do `insufficient-support` (6 positive). Báo kèm mọi cặp trong `PAIRS` kèm CI, số replicate hợp lệ/undefined và mẫu số.

### 4. Shape đối soát (duy nhất tại ADR này)

- `evaluation['coverage']['pairs'][key]` (có cho mọi key trong `PAIRS`, gồm `adsketch_mad_or_minus_isolation_forest`): gồm 4 số đếm `healthy_minutes` (phút trong `healthy_keys`), `healthy_both_scored` (phút khỏe cả hai model có score), `events` (event eligible), `events_both_scored` (event mà cả hai model có `>= 1` score trong `[onset, onset+300 s]`), cùng `coverage = min(healthy_both_scored / healthy_minutes, events_both_scored / events)`. Khi một mẫu số bằng 0 thì `coverage = None`, và gate trả nhánh `pairwise-coverage-incomplete`.
- `evaluation['bootstrap']['event_groups']`: số service có `>= 1` event eligible (`int`).
- `evaluation['bootstrap']['intervals'][key][metric]` cho 4 metric (`precision`, `recall`, `f1`, `false_incidents_per_healthy_service_hour`): gồm `lower`, `upper`, `valid_replicates`, `undefined_replicates`, `denominators`, `missing_reason`.
- `evaluation['models'][m]`: ghi `false_incidents_per_healthy_service_hour` (`float | None`), `healthy_false_incidents` (`int`) và `healthy_scored_service_hours` (`float`).
- Event ledger (`events`): ngoài các trường audit sẵn có, bổ sung `end_at`, `length_minutes`, `positive_minutes` và `censor_until` (mốc cuối cửa sổ censor `[onset, end_at + 300 s]` dùng cho event ineligible).

## Điều kiện trước freeze và trước khi đổi release

1. **Trước freeze v17 (B-1..B-4):** owner duyệt ADR này; nhận bundle `handoff-lane-b-v17` từ máy A để gỡ trạng thái **BLOCKED** cho B-1 (đo support D1, fit lại trên clean commit, kiểm F5 round-trip ≤ 1e-6 và budget 1%); giữ nguyên config ADSketch (B-2); gọi đúng tên **"ADSketch, biến thể tìm kiếm giới hạn"** theo kết quả B-3; giữ mẫu novel là `NA` (B-4).
2. **Trước khi đổi release (B-5..B-8):** B-5 đã có review mã nguồn hỗ trợ kết luận **triển khai độc lập** theo bài báo (không phát hiện dòng mã logic sao chép từ `OpsPAI/ADSketch` tại `f7bd642c`, mã sản phẩm không import module tác giả), cần người triển khai hoặc owner xác nhận lịch sử phát triển trước khi đưa vào image; xây dựng loader và xác minh parity runtime cho ứng viên đã freeze (B-6); khôi phục hoặc chốt phương án rollback (B-7); chạy rehearsal ngày 8–9 và CI trên đúng SHA (B-8). Chưa xong B-5..B-8 thì release giữ baseline `de38fc6b` (`KEEP`).
