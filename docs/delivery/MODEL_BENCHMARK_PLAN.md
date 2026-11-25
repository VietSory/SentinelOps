# Kế hoạch benchmark LTFP và ADSketch trong 9 ngày

Mục tiêu là xác định bằng số liệu liệu **LTFP dùng metrics và đồ thị dịch vụ** có cảnh báo vi phạm SLO độ trễ tốt hơn XGBoost, và **ADSketch gốc** có phát hiện bất thường tốt hơn Isolation Forest. Bàn giao hai bảng benchmark có thể chạy lại, ADR ghi quyết định của từng nhánh và release đã kiểm chứng local để chuẩn bị thử AWS. Hạn 9 ngày tính đến `main` và CI xanh. Nghiệm thu AWS L2/L3 chạy theo [đặc tả demo](DEMO_UPGRADE_SPEC.md#15-nghiệm-thu-live-sau-khi-wp1wp7-lên-main).

Lệnh chạy, dữ liệu đối chiếu, ca kiểm thử, điểm dừng an toàn và tiêu chí GO/BLOCKED của từng lane nằm trong [đặc tả thực thi](MODEL_BENCHMARK_RUNBOOK.md).

## Phân công

| Chủ trì | Kết quả chịu trách nhiệm | Việc làm song song ngay từ ngày 1 |
| --- | --- | --- |
| **Lane A — owner, LTFP** | Nối dữ liệu Alibaba, khóa nhãn và split cảnh báo, fit LTFP, đối chiếu XGBoost và slope, đánh giá ảnh hưởng của đồ thị. Giữ trách nhiệm về feature, nguồn telemetry, worker và bộ đánh giá dùng chung. | Xác minh MSRTMCR và MSCallGraph trên một lát train. Chạy benchmark baseline development và chuẩn bị giao thức capture. |
| **Lane B — ADSketch và tích hợp demo** | Tái hiện ADSketch gốc, kiểm khả năng tạo pattern từ Alibaba train, chạy benchmark IF/MAD, đóng gói image và kiểm đường chạy UI/Grafana/API với release được chọn. | Chạy mã tác giả trên dữ liệu mẫu của bài, kiểm dependency và cách dùng nhãn. Dùng fixture tại giao diện model để chuẩn bị tích hợp ngay. |
| **A + B — một lượt review bàn giao** | Ngày 2 khóa giao thức. Cuối đợt đọc chung hai bảng, review diff giao diện, chọn release từng nhánh và xác nhận khả năng rollback. Owner ghi quyết định. | Mỗi lane dùng checkout, thư mục output, model store và tài nguyên chạy riêng. Trao manifest và artifact theo checksum. |

Trong đợt này B sở hữu phần triển khai ADSketch và test tương ứng. A là người duy nhất sửa registry, feature dùng chung và wiring worker. B giao module ADSketch qua giao diện đã thống nhất, đồng thời sở hữu đóng gói/API/UI theo hợp đồng hiện tại. Khi có thay đổi contract, cả producer và consumer cùng review. [Hợp đồng A/B](WORKSTREAM_CONTRACT.md)

## Ngày 1–2 — Khóa cách đo và xác minh dữ liệu

1. **Pin baseline và nguồn.** Ghi SHA code, bundle `de38fc6b`, snapshot Alibaba v2022, manifest split, profile `prometheus-increase-60s-5s/v2`, cadence 60 giây, seed, dependency và policy. Chỉ dùng Alibaba đã duyệt để fit và calibration. Local HTTP dùng để replay hoặc đánh giá. RCAEval giữ vai trò đánh giá RCA. Lưu manifest trước khi chạy và giữ raw bất biến. [Model pipeline](../runbooks/MODEL_PIPELINE.md) · [ADR-016](../adr/ADR-016-public-production-training-source.md)
2. **A thử nối đồ thị trên train.** Xuất số service, edge, site/thời đoạn ghép được, tỷ lệ ghép được và số hàng thiếu/không khớp. Kiểm định hướng edge và timestamp. Chốt cách xử lý node lẻ, graph thiếu và các nhóm có dependency bắc qua split. Graph và mọi tham số học từ dữ liệu phải dùng đúng tập fit. Các edge suy ra từ telemetry chỉ được dùng thông tin khả dụng tại lúc chấm. Chứng minh runtime có thể dựng đầu vào theo cùng quy tắc. [Alibaba v2022](https://github.com/alibaba/clusterdata/tree/master/cluster-trace-microservices-v2022) · [LTFP](https://www.mdpi.com/1999-4893/19/9/720)
3. **B kiểm nhãn cho ADSketch.** Tái hiện bước học mẫu và cách nhận diện mẫu chưa thấy từ mã tác giả. Ghim revision và kiểm giấy phép trước khi tích hợp mã. Kiểm khả năng tạo segment bất thường từ mốc vi phạm độ trễ đã khóa của **Alibaba train**. Ghi số segment, cách phân đoạn và phần nào của ADSketch triển khai được. Ghi rõ nhãn Alibaba là nhãn suy ra từ telemetry. Mẫu đã biết và mẫu chưa từng học cần có kết quả riêng nếu dữ liệu hỗ trợ. [ADSketch](https://doi.org/10.1145/3510003.3510085) · [Mã tác giả](https://github.com/OpsPAI/ADSketch)
4. **Khóa benchmark.** Ghi rõ nguồn, train/calibration/test, cutoff, danh sách kịch bản, mẫu số, quy tắc match sự kiện một-một, persistence, ngưỡng chọn trước và quy tắc thiếu dữ liệu. Giữ split service/nhóm và thời gian. Purge các window có lookback, baseline history hoặc horizon bắc qua ranh giới thời gian. Tập đã dùng để xem hoặc chọn model giữ vai trò development, kể cả epoch `ald`. Holdout ghép cặp theo cùng run giữa các model, giữ kín kết quả tới khi khóa ứng viên. [Định nghĩa chỉ số](../architecture/CONSTRAINTS_AND_GATES.md#7-metric-definitions)
5. **Chốt khả năng chạy cuối ngày 2.** A phải có bảng join coverage, một lượt thử fit/score và dữ liệu graph khả dụng cho runtime. B phải có test tái hiện ADSketch và đủ segment train/calibration theo giao thức đã chốt. Hai lane gắn trạng thái `GO` hoặc `BLOCKED` với bằng chứng. Nếu một lane bị chặn, lane kia tiếp tục triển khai và capture theo phạm vi độc lập.

## Lane A — LTFP cảnh báo SLO

**Câu hỏi.** Tại phút `t`, liệu service đang khỏe có bắt đầu vi phạm SLO độ trễ trong `(t, t+300 giây]`? Mốc thật trên local lấy từ SLO p95 theo health policy. Mốc deploy dùng để phân tích thời gian và kịch bản. Nếu một window thiếu theo dõi đủ horizon hoặc chất lượng telemetry, ghi `UNKNOWN`. Nhãn vượt ngưỡng tương đối trên Alibaba và nhãn SLO local được báo ở hai bảng riêng.

**Bốn dòng so sánh.**

1. **Slope + persistence** từ evaluator hiện có.
2. **XGBoost** từ release hiện tại nếu giao thức khớp. Fit lại trên train và hiệu chuẩn riêng nếu đổi nhãn hoặc split. Ghi chính xác bản được so.
3. **LTFP dùng metrics + đồ thị**, đầu ra một điểm rủi ro cho mỗi service mỗi phút. Ghi rõ đây là biến thể theo SentinelOps và mô tả lớp nào kế thừa từ bài báo.
4. **Cùng LTFP bỏ cạnh giữa các service**, giữ metric, temporal backbone, train split và quy tắc chọn ngưỡng. Phép thử này đo giá trị tăng thêm của cạnh đồ thị trong thiết lập đã thử.

Cả bốn dòng chấm cùng các window và run hợp lệ theo một giao thức. Báo coverage toàn tập từng model bên cạnh kết quả trên tập ghép cặp để lộ điểm mù do thiếu graph. So AP với prevalence và slope. Tại điểm vận hành đã khóa, lấy **tỷ lệ sự kiện gradual có cảnh báo trước ít nhất 60 giây** làm chỉ số chính. Báo thêm lead p50/p10, số miss/late, số lượt có cảnh báo sai trên lượt không breach, false warning trên giờ-service khỏe, p95 thời gian suy luận và độ trễ từ lúc bucket sẵn sàng đến lúc phát cảnh báo. Giữ thêm cột **lead ≥180 giây** cho mục tiêu demo. [Q-07](../architecture/CONSTRAINTS_AND_GATES.md) · [Gate demo](DEMO_UPGRADE_SPEC.md#7-tham-số-được-phép-chỉnh-khi-tập-dượt)

## Lane B — ADSketch phát hiện bất thường

**Câu hỏi.** ADSketch gốc có phát hiện nhiều sự kiện độ trễ hơn hoặc phát hiện sớm hơn IF và Median/MAD ở cùng ngân sách cảnh báo sai không?

So **Median/MAD, Isolation Forest, ADSketch gốc** trên cùng service/run, mốc onset và quy tắc match. Ghi rõ IF/MAD dùng feature tổng hợp còn ADSketch đọc chuỗi, cùng lịch sử telemetry khả dụng. ADSketch gốc dùng bộ mẫu học offline đã freeze. Nếu thử cập nhật online, báo thành một hàng riêng và kiểm nhãn/feedback thực sự đã đến trước lúc cập nhật. Giữ các sự kiện 5xx của SLO rule ở hàng riêng khi nguồn Alibaba không có nhãn lỗi HTTP. Phần so detector chỉ ghi nhận các alert do detector phát.

Chỉ số chính là **event F1 ở cùng ngân sách cảnh báo sai**, kèm TP/FP/FN, precision, recall, false incident trên giờ-service khỏe, MTTD p50/p90, số miss, coverage và p95 thời gian inference. Đọc riêng kết quả theo Checkout, Payment, Inventory và loại lỗi. Ghi đúng kịch bản đã đo khi đánh giá Q-04/Q-05. [Q-04–Q-06](../architecture/CONSTRAINTS_AND_GATES.md) · [ADSketch](https://doi.org/10.1145/3510003.3510085)

## Ma trận chạy và bảng số liệu

**Số liệu đã có để thiết kế phép thử.** Trên Alibaba evaluation, IF đạt event F1 **0,336**, MAD **0,300**. Trên epoch development `ald`, cả hai phát hiện **12/12** ca độ trễ. XGBoost và slope cùng cảnh báo **20/20** sự kiện gradual trước 60 giây. XGBoost đạt AP **0,947–0,953**, slope **0,808–0,835**. Cả 10 lượt gradual `ald` đều breach. Ma trận mới cần lượt gradual không breach để kiểm xem hai model có phân biệt được mức tăng độ trễ dưới ngưỡng hay không. Giữ các số lịch sử cùng nguồn và phạm vi của chúng khi trình bày. [Alibaba evaluation](../reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md) · [Local development `ald`](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md)

- Dùng một đợt capture local bất biến cho cả hai lane khi schema telemetry đủ. Chạy cùng một bộ telemetry qua tất cả model. Ma trận tối thiểu có healthy, abrupt latency, gradual latency có breach và gradual latency không breach, phủ Payment/Inventory với Checkout là service liên quan. Giữ các case fail, no-breach, UNKNOWN và miss trong thống kê. Đề xuất tối thiểu 5 nhóm run để lập lịch. Chốt số nhóm và thời lượng theo pilot và các gate hiện có. Kết quả ít sự kiện hoặc khoảng tin cậy rộng phải được ghi đúng mức bằng chứng.
- Chọn ngưỡng trên calibration với ngân sách cảnh báo sai đã khóa cho từng nhánh. Giữ persistence và event matching thống nhất. Báo cả ngân sách mục tiêu lẫn tỷ lệ cảnh báo sai quan sát được trên holdout. Tính 95% CI cho chênh lệch bằng bootstrap ghép cặp theo **nhóm run local** hoặc **group service Alibaba**, với seed đã khóa. Khi mẫu không đủ để phân biệt, báo chênh lệch quan sát được cùng độ bất định.
- Báo thời gian fit, thời gian xây feature/graph và p95 inference riêng, kèm CPU/GPU, phiên bản thư viện và coverage. Đính kèm SHA code, data/split/config, model digest, lệnh chạy, raw predictions/event matches và báo cáo theo service. Dữ liệu thô và artifact lớn nằm trong store đã duyệt, báo cáo dẫn manifest hoặc checksum.

| Detector | TP / FP / FN | Precision / Recall / F1 | FP / giờ-service khỏe | MTTD p50 / p90 | Coverage | Δ F1 so với IF + 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
| Median/MAD | — | — | — | — | — | — |
| Isolation Forest | — | — | — | — | — | Mốc |
| ADSketch | — | — | — | — | — | — |

| Forecast | AP / prevalence | Sự kiện lead ≥60 s / tổng | ≥180 s / tổng | Lead p50 / p10 | False warnings / giờ khỏe | Lượt không breach có cảnh báo / tổng | Δ precursor recall so với XGB + 95% CI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Slope + persistence | — | — | — | — | — | — | — |
| XGBoost | — | — | — | — | — | — | Mốc |
| LTFP không cạnh liên service | — | — | — | — | — | — | — |
| LTFP metrics + đồ thị | — | — | — | — | — | — | — |

Các bảng trên là mẫu. Thêm hàng chênh lệch LTFP có/không cạnh và báo số sự kiện miss/late ở phụ lục theo từng model. Mỗi bảng có metadata nêu dataset, split/SHA, coverage, số nhóm run và đồng hồ đo. Kết quả replay dùng đồng hồ replay, còn kết quả phát cảnh báo thực dùng thời điểm phát cảnh báo tại runtime.

## Lịch 9 ngày và điểm giao

| Ngày | A — LTFP | B — ADSketch và demo | Bằng chứng cần thấy |
| --- | --- | --- | --- |
| **1** | Pin baseline, lấy mẫu dữ liệu Alibaba và thử join graph. | Tái hiện mã ADSketch, kiểm dependency và nhãn. Chuẩn bị fixture cho API/UI. | Baseline identity, báo cáo pilot join/nhãn và artifact mẫu. |
| **2** | Chạy fit/score nhỏ, chốt nhãn, split và graph runtime. | Chốt giao diện ADSketch, segment train và khả năng tái hiện kết quả. | Freeze giao thức, handoff giao diện, GO/BLOCKED từng lane. |
| **3–4** | Fit LTFP có/không cạnh, chạy XGBoost/slope trên development, chọn ngưỡng trên calibration. | Fit ADSketch, chạy IF/MAD dev/calibration, chuẩn bị image và smoke test với fixture. | Hai ứng viên đã freeze, chỉ số development, smoke replay, budget runtime. |
| **5–6** | Capture hoặc replay holdout đã khóa, tính các bảng LTFP và bootstrap. | Chấm ADSketch/IF/MAD trên cùng holdout, hoàn thành test thiếu dữ liệu và bundle. | Hai bảng điền số, đủ manifest, raw denominators và kết quả theo service. |
| **7–8** | Review bằng chứng, ghi ADR, đóng băng release phù hợp, kiểm parity worker. | Kiểm image và demo local đầu cuối, kiểm UI/Grafana, proposal, rollback và cách trở về release cũ. | Smoke có telemetry thật, release/rollback digest, diff đã review, `main` và CI đúng SHA. |
| **9** | Dự phòng lỗi dữ liệu hoặc integration. Chốt kết quả từng nhánh. | Dự phòng CI/rehearsal, hoàn thiện handoff để thử AWS. | Báo trạng thái từng gate cùng lỗi và next action. |

Đợt capture `ald` trước đây mất gần 23 giờ. Giữ slot chạy riêng cho capture, bắt đầu ngay khi đủ prerequisite. Trong lúc capture, B tiếp tục đóng gói, kiểm UI và failure paths. Giữ tài nguyên dự phòng cho bước publish và CI. [Lịch capture đã đo](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md)

## Quyết định release và bàn giao AWS

- **ADSketch.** Đề xuất thay IF khi đạt các gate detector trong phạm vi đo, có cải thiện có thể bảo vệ ở cùng ngân sách cảnh báo sai và chạy đúng hợp đồng runtime.
- **LTFP.** Đề xuất thay XGBoost khi đạt Q-07 trong phạm vi đo, có cải thiện tỷ lệ cảnh báo sớm hoặc lead time ở cùng ngân sách cảnh báo sai, và vượt preflight demo local. So có/không cạnh để quyết định phần cải thiện nào có thể quy cho đồ thị. Giữ mốc demo lead ≥180 giây và thời gian deploy đến breach ≤600 giây.
- **Bàn giao kỹ thuật.** Ghi quyết định từng nhánh, scope, bảng hiệu quả, giới hạn và SHA vào ADR/review packet. Pin release và image digest. Kiểm parity giữa replay và runtime, readiness, trạng thái thiếu model/feature, cảnh báo trong UI/Grafana, proposal cần on-call duyệt, đúng một rollback và telemetry xác nhận phục hồi. Giữ bundle trước đó để rollback bằng release pin. LTFP chỉ phát cảnh báo, còn quyền hành động vẫn theo approval và các guard hiện hành.
- **Thiếu bằng chứng.** Nếu ứng viên trượt hoặc holdout không đủ, ghi `FAIL` hoặc `INCONCLUSIVE`, giữ model đã kiểm chứng ở nhánh đó và bàn giao kết quả nghiên cứu. Mỗi lần chỉnh theo kết quả holdout cần bộ đánh giá mới để kết luận cuối. Kết quả ngày 9 là sẵn sàng cho AWS dev khi gate local đạt. AWS L2/L3 vẫn cần bằng chứng live.
