# Tiến độ Lane A — 26/11/2026

Lane A đang ở chặng D2-A. Snapshot Alibaba và audit graph đã được khôi phục từ bundle bàn giao cùng nguồn Alibaba. Kết quả cohort dưới đây là phân tích development, chưa phải kết quả holdout hoặc quyết định release.

## Dữ liệu và preflight

- **P0 PASS.** Bundle `de38fc6b` và fit report từ gói `alibaba-de38fc6b-for-B` khớp `SHA256SUMS` và hash trong runbook. Đã tải đủ 120 archive MSRTMCR, tái tạo 120 Parquet và kiểm từng hash với fit report. Dữ liệu local nằm tại `artifacts/lane-a/`.
- **P1 PASS.** Audit gốc trên graph shard 20–23 tái hiện 9.928 cạnh, 5.401 node và 6.314/182.943, 6.310/182.200 service-phút có hàng xóm. Graph shard 48–51 cho 8.348 cạnh, 4.949 node và 5.690/182.943, 5.654/182.200. Receipt nằm trong `artifacts/lane-a/preflight/support-20-23.json` và `support-48-51.json`.
- **Phần mềm.** Bộ test nền đạt 3.788 test, 4 skip và 1 xfail. Bộ LTFP CPU đạt 354 test và 1 skip do đường dẫn bundle cố định. Test bundle `de38fc6b` chạy riêng với bundle đã khôi phục và đạt. Kiểm tra wheel đã cài cũng đạt.
- **Fit/score smoke PASS.** Dùng `ltfp_training.fit_pair` trên hai window Alibaba train, một positive và một negative. Hai biến thể graph và self-only đều hoàn thành 100 bước, lưu weight, nạp lại và trả score hữu hạn. Lần chạy lại xác minh receipt và cho cùng output. Đây là smoke hai mẫu, không dùng để so chất lượng model. Replay giả định dữ liệu khả dụng tại timestamp của bucket và đặt feature ngưỡng bằng `log(1/16)` cho nhãn K=16. Receipt, sample và score nằm trong `artifacts/lane-a/preflight/paired-smoke-fit/`.
- **Trainer.** Fit mới dùng thứ tự record theo hash với seed 42 cho cả hai biến thể. Receipt báo số record đầu vào và số record thực sự được dùng trong ngân sách 3.200 lượt. Checkpoint cũ vẫn được xác minh và nạp lại. Mỗi cặp model mới chỉ được xuất ra sau khi cả hai biến thể và receipt hoàn tất.
- **Fit development.** Đã fit cả hai LTFP trên cùng 3.200 window train, gồm toàn bộ 81 positive và 3.119 negative chọn theo hash seed 42. XGBoost được fit lại trên chính các window này và nạp lại cho score khớp tuyệt đối. Tập train được lấy mẫu theo lớp và không phản ánh tỷ lệ gốc. Receipt nằm trong `artifacts/lane-a/preflight/development-k16-fit-v5/` và `development-k16-xgboost-v5/`.

## Cohort development tại K=16

Các số dưới đây dùng `service_window_arrays`, split theo service và topology cùng split. Phép lọc năm bước ban đầu xấp xỉ khả dụng runtime và có thể nhận bước thiếu bucket. Các graph được dựng trước hai khoảng chấm. Nhãn là proxy độ trễ tương đối của Alibaba. Điều kiện tối thiểu 120 phút trên mỗi service được tính trên toàn snapshot. Đây là số pilot lịch sử, chưa chứng minh parity runtime hay purge theo protocol cuối. Dùng bảng split thời gian phía dưới cho số đã áp dụng bộ lọc runtime.

| Graph | Khoảng phút | Split | Positive window | Positive có hàng xóm | Negative có hàng xóm | Event positive có hàng xóm |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 20–23 | 162–191 | train | 286 | 11 | 5.634 | 4 |
| 20–23 | 162–191 | calibration | 59 | 5 | 980 | 1 |
| 20–23 | 324–353 | train | 269 | 1 | 5.407 | 1 |
| 20–23 | 324–353 | calibration | 90 | 0 | 964 | 0 |
| 48–51 | 162–191 | train | 286 | 11 | 5.110 | 4 |
| 48–51 | 162–191 | calibration | 59 | 5 | 770 | 1 |
| 48–51 | 324–353 | train | 269 | 1 | 4.859 | 1 |
| 48–51 | 324–353 | calibration | 90 | 0 | 755 | 0 |

Receipt và script nằm trong `artifacts/lane-a/preflight/cohort-20-23.json`, `cohort-48-51.json` và `cohort_support.py`. Các số giữ nguyên cả service cô lập trong mẫu số. Negative có hàng xóm vượt 100 trong từng trường hợp, nhưng calibration ở khoảng thứ hai không có positive có hàng xóm với cả hai graph.

Với graph 48–51 ở khoảng 324–353, cả 90 positive window calibration thuộc service không có cạnh tới service cùng split. Kết quả phân loại nguyên nhân nằm trong `cohort-48-51-diagnosis.json`.

Đã thử thêm graph shard 52–53 sau khi xem kết quả trên. Graph 48–53 vẫn có 0 positive calibration có hàng xóm ở khoảng 324–353. Đây là phân tích thăm dò sau khi xem dữ liệu, được lưu riêng tại `cohort-48-53.json`.

### Cohort với split thời gian

Chạy lại graph 20–23 với train phút 0–179 và calibration phút 180–359. Cửa sổ được chọn lần lượt là 162–172 và 324–352. Hai cửa sổ giữ toàn bộ lịch sử tối đa 68 phút và horizon cùng confirmation tail 7 phút trong partition tương ứng. Một service chỉ đủ điều kiện tại anchor khi có ít nhất 120 phút quan sát trong partition đến anchor đó. Mỗi cạnh nối hai service cùng split và được quan sát trước chuỗi feature.

| Split | Service | Positive window / service | Negative window / service | Positive có hàng xóm / service | Negative có hàng xóm / service |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train | 6.002 | 81 / 23 | 62.556 / 5.815 | 10 / 3 | 2.085 / 197 |
| Calibration | 2.032 | 89 / 21 | 55.641 / 1.999 | 0 / 0 | 920 / 36 |

Train có 24 event positive thuộc 23 service. Calibration có 26 event thuộc 21 service. Các negative window có thể trùng lặp theo thời gian hoặc chia sẻ graph, nên số window không chứng minh tính độc lập thống kê. Kết quả ở trên đạt điều kiện số lượng hai lớp và có cạnh của A08 cho thí nghiệm development. Chưa có positive calibration với hàng xóm để đánh giá riêng nhóm đó.

Phân vùng này được chọn sau các phân tích pilot và chỉ dùng cho development. Topology calibration đã được đối chiếu với các shard CallGraph trước cutoff trên đồng hồ replay. Chưa chứng minh thời điểm topology khả dụng trong runtime thật. Chưa dùng phân vùng này làm giao thức holdout. Lệnh và receipt được ghim tại [evidence cohort](../reports/evidence/ltfp-cohort-causal-development.json). Script [audit cohort](../../scripts/audit_ltfp_cohort.py) kiểm hash fit report, 120 Parquet, split service và ranh giới thời gian.

Đã dựng protocol draft từ 3.200 record train, 2.039 anchor calibration và 56.858 window development còn lại. Draft giữ topology một bước đã dùng cho từng service và snapshot các file nguồn theo hash. `code.commit` là commit nền `27d443e`; snapshot gồm cả thay đổi chưa commit, nên không phải clean commit nghiệm thu. Availability của Alibaba vẫn được mô phỏng. Chưa gắn receipt A01–A09 hoặc xác nhận support vào draft; audit trả `blocked` và không freeze candidate. Protocol local nằm tại `artifacts/lane-a/preflight/protocol-draft-v1/protocol.json`, với SHA logic `05716e3c8a470b6dd59eb45157d10972593632968d2c21817cb26f2e4fc2a78b`.

Bản draft v3 bổ sung receipt fixture cho A01–A06 và A09. Bản v4 gắn receipt A08 và support cho development Alibaba. Bản v5 gắn receipt A07 từ lượt capture thật. Audit trước đây báo Lane A `ready`, nhưng kiểm tra mới phát hiện checker A07 khác source snapshot v3. Draft v5 hiện `blocked` cho cả hai lane. A07 đã replay lại từ commit sạch `5ac49bb2e2ded9ce55c586a7e24510720bba596d` trên cùng 150 raw HTTP, sai số lớn nhất 0; receipt mới là `artifact://manifests/e87297ad5391eb1202722c85b09b0d0f056bd7e6422546384c670a1077a75010.json`. Cần protocol revision mới ghim đúng checker và chạy lại các fixture trước khi freeze.

Draft v12 ghim source sạch `1c4f73818a29b6ce654ae781f7a94244f9acc6ad` và receipt A07 `artifact://manifests/ddf780f82e3612543270375b961465c293934239b543ba8c51fc9dc542e8ba06.json`. Audit báo Lane A `ready` và Lane B `blocked`; đây là readiness của protocol development, chưa phải freeze hay kết quả holdout. Bản sửa runner sau đó cần một revision protocol mới trước khi chấm chính thức.

Draft v16 ghim source sạch `09b3bef06634eb64ac4f09fb6761befa7982aaf0`, receipt A07 mới và kết quả B01–B06 development. Cả hai lane đã qua audit; protocol và candidates đã freeze tại local. Holdout chưa mở, pilot Inventory còn chạy. Hash và giới hạn được ghi trong [evidence freeze](../reports/evidence/model-benchmark-freeze-development.json).

Launcher dùng lại artifact đã có qua `--lane-a-evidence`. Chạy từng stage `fit`, `calibrate` hoặc `score` để đối chiếu hash và ledger. `artifact_status: verified` xác nhận artifact development. Exit code vẫn là 2 nếu audit Lane A còn gate mở.

`freeze-candidates --lane-a-evidence <evidence.json> --lane-b-fit-sha256 <sha256>` yêu cầu protocol chung đã freeze, checkpoint B đã xác minh và checkpoint A tái lập được từ các artifact hiện có. Manifest ghim ngưỡng và model của hai lane. Capture holdout vẫn cần protocol và luồng nghiệm thu riêng.

`capture-preflight --draft-matrix` xuất thiết kế chung gồm 35 lượt với seed, slot ID, profile, recipe và ngân sách 34 giờ 05 phút. SHA logic của thiết kế là `e97484b36b160dff8333361e90bdb9b989e5d4a250631d176560233a1fc007da`. Lệnh trả `blocked` khi chưa có protocol và adapter holdout được duyệt, freeze ứng viên và receipt thử ramp 400 ms trên development.

Plan `configs/alibaba_low_ramp_development.json` khai báo hai lượt pilot 400 ms cho Payment và Inventory trong nhóm `ale` riêng. Cả hai dùng profile v2, warmup 1.980 giây và oracle request hiện có. Plan này chỉ cho development. Checkout pilot sạch dùng commit `cd1b732a853b8f28741b998a17e277c29144d382` trên nhánh `feat/lane-a-low-ramp-pilot`. Lượt pilot được xếp sau A07 và chỉ bắt đầu nếu capture cùng verifier A07 thành công.

Nhóm `ale-01` bị gián đoạn do host khởi động lại. Receipt `artifact://manifests/4535e5e0e97743219db85a39d8047fe095557225e585c8f7625faa1b012ec293.json` đóng toàn bộ nhóm và giữ nguyên dữ liệu gốc. Nhóm thay thế `ale-02` đang thu development; chưa có kết quả nghiệm thu pilot.

Fit thử detector Lane B đã dùng phân vùng train và anchor calibration của draft v5. Checkpoint local `preflight/detector-bound-pilot-v3/fit/b/fit.json` có SHA-256 `936ae30e86030d3c87b2793ca97c24840ba1491dbe4ee4e70ea2283f1921e719`. Cả ba detector có 2.039 anchor, gồm 1.784 negative có score, 183 positive có score và 72 `UNKNOWN` thiếu lịch sử liên tục. Replay checkpoint cho sai số score lớn nhất 0. Fit thử dùng source workspace, chưa có protocol chung ở clean commit và chưa chứng minh B02/B06; không dùng checkpoint này để mở holdout.

Verifier kiểm freeze protocol và ứng viên, sau đó đối chiếu receipt theo ma trận trước khi đọc oracle. Receipt thiếu lượt, có run lặp, bị gián đoạn hoặc thuộc nhóm `ald` bị từ chối. Bước xác minh nguồn kiểm hash raw, replay phục vụ và dựng lại oracle từ request. Caller vẫn phải ghim ma trận và commit capture trước khi thu. Adapter holdout và seal chưa được nối vào launcher.

```bash
python -m scripts.model_benchmark --root artifacts/lane-a \
  --protocol artifacts/lane-a/preflight/protocol-draft-v5/protocol.json \
  score --lane a --lane-a-evidence docs/reports/evidence/ltfp-alibaba-development.json
```

### Điểm development trên Alibaba

Đã chạy lại bước chuẩn bị fit trên 120 shard và đối chiếu với receipt đã lưu. Cohort được chọn, admission, input hash và hash metadata của hai model đều khớp. Một lần fit độc lập trên cùng input cũng tạo loss, trọng số và metadata trùng tuyệt đối cho cả hai model sau 100 bước. Sau khi tải lại model từ lần fit độc lập, toàn bộ 2.039 dòng calibration khớp byte với ledger cũ. Trainer không hỗ trợ phục hồi từ checkpoint giữa quá trình fit; F5 chưa được chứng minh đầy đủ.

Đã hiệu chuẩn XGBoost và hai LTFP bằng một anchor chọn theo hash cho mỗi service, trước khi lọc nhãn. Có 1.923 negative có score, 1 positive và 115 UNKNOWN trong 2.039 service. Một service chỉ góp một anchor vào hiệu chuẩn, còn các graph có thể chia sẻ hàng xóm. Slope giữ ngưỡng cố định `> 0,05`.

Hai LTFP dùng 81 positive và 3.119 negative khi fit. Ledger calibration xác nhận cả bốn model có score trên 1.923 anchor âm và 1 anchor dương. Trong các anchor có hàng xóm khả dụng, 31 âm, 4 UNKNOWN và 0 dương. Mẫu số âm đủ phân giải budget 1% của A08. Nhóm dương có cạnh vẫn thiếu để đánh giá tác dụng riêng của graph.

Chấm lại toàn bộ khoảng calibration, gồm 58.897 window. Tập có nhãn và đủ score cho cả bốn model có 55.730 window, gồm 89 positive, với prevalence 0,001597.

| Forecast | AP trên tập chung | Negative window phát cờ / có score |
| --- | ---: | ---: |
| Slope | 0,01607 | 6.655 / 55.668 |
| XGBoost | 0,02423 | 476 / 55.668 |
| LTFP self-only | 0,00233 | 529 / 55.641 |
| LTFP graph | 0,00232 | 516 / 55.641 |

Điểm graph chấm bằng bộ đệm feature đã được so với 2.039 lượt inference gốc, sai số lớn nhất bằng 0. Số cờ là tỷ lệ trên negative window, chưa phải false warnings mỗi giờ. Đây là cùng tập đã dùng để hiệu chuẩn và chọn cách phân tích, nên AP chỉ mô tả development. Không có positive calibration với hàng xóm trong graph đã thử. Chưa có căn cứ để kết luận tác dụng của graph hoặc thay XGBoost. [Evidence và hash](../reports/evidence/ltfp-alibaba-development.json)

## Gate còn mở

- **A08 đạt ở phạm vi development.** Đã dựng lại topology từ CallGraph phút 60–71 và xác nhận graph không dùng node train. Trong 1.996 service chưa thấy trong fit có score graph, 35 có hàng xóm khả dụng và 1.961 không có. Thứ tự thời gian đã được xác nhận trên đồng hồ Alibaba replay với availability mô phỏng. Nhóm calibration dương có cạnh vẫn thiếu để đánh giá graph.
- **A07 đạt trong phạm vi replay development.** Lượt `ald-02-healthy` hoàn tất và được xác minh theo ADR-019. Worker replay dùng 150 raw HTTP profile v2 trong 50 phút cho ba service. Node IDs, mask và feature giữa hai đường khớp, sai số lớn nhất 0. Dữ liệu đến muộn bị loại trước cutoff và khả dụng đúng tại receipt. Receipt được gắn vào draft v5. Replay dùng fakeDB có trạng thái; thời điểm topology được gán lúc thiết lập replay, còn parity scorer và độ trễ live chưa được chứng minh. Lượt `ald-01-healthy` bị gián đoạn được giữ riêng.
- **Benchmark chính thức chưa chạy.** Bảng development ở trên chưa có event oracle độc lập, lead time, false warnings mỗi giờ hay khoảng tin cậy theo group. Chưa có candidate đã freeze hoặc quyết định thay model. Chưa mở holdout.

Oracle forecast đã được sửa để tính từng vi phạm sau khi có ba bucket khỏe liên tiếp theo health policy. Bucket thiếu hoặc `UNKNOWN` không được dùng để xác nhận phục hồi. Test kiểm hai onset trong cùng lượt và giữ cả hai trong mẫu số khi model không có score. CI đã đạt cho commit `bacb10e` chứa thay đổi này.

Runner chấm XGBoost bằng bản refit ghim manifest, trọng số và `fit_input_sha256` của cùng tập train LTFP. Nếu thiếu refit, score XGBoost không khả dụng ngoài fixture; baseline cũ không được tự thay thế. Trên feature Payment replay từ A07, score refit là `0.237899050116539` và khớp phép tính độc lập với sai số 0. Đây chưa phải parity scorer live.

[Kế hoạch](MODEL_BENCHMARK_PLAN.md) · [Runbook](MODEL_BENCHMARK_RUNBOOK.md) · [Kết quả Lane B](MODEL_BENCHMARK_LANE_B_LOCAL.md)
