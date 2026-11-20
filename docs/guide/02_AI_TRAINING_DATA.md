# AI/ML trong SentinelOps

Ngày rà soát: **18/11/2026**. Checkout: `08844452d0b37c603e0c481c9119e16264edba03`.

Báo cáo dựa trên mã nguồn, cấu hình, test và báo cáo đã lưu trong repo. Không chạy train, benchmark, dịch vụ hay gọi AWS trong lần rà soát này. Kết quả thực nghiệm bên dưới là **kết quả được repo ghi nhận**, không phải phép đo mới trên máy hiện tại. Raw data và trọng số model không có trong checkout đang khảo sát.

## 1. Kết luận chính

**Repo có training thực sự**, không chỉ có mock hoặc gọi model bên ngoài. Pipeline chính thức train **Isolation Forest** để phát hiện bất thường latency và **XGBoost** để dự báo vượt ngưỡng latency trong 300 giây tiếp theo. Nguồn train hiện hành là telemetry production công khai **Alibaba cluster-trace-microservices-v2022 / MSRTMCR**. Một bundle dùng chung cho Checkout, Payment và Inventory; không phải ba model riêng cho ba service.

Bundle hiện hành được tài liệu xác định là **`de38fc6b`**, feature set **`http-relative-5m/v2`**, theo ADR-019. Model đã được duyệt G3 **có điều kiện** và có báo cáo tích hợp local; các mốc này không đồng nghĩa nghiệm thu toàn bộ chất lượng, AWS hoặc final holdout. Xem [README](../../README.md), [ADR-019](../../docs/adr/ADR-019-breach-relative-retrain.md), [G3 packet](../../docs/reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md) và [trạng thái dự án](../../docs/PROJECT_STATUS.md).

**Có code hỗ trợ LLM qua Amazon Bedrock** như provider tùy chọn để tổng hợp lời giải thích root cause dựa trên evidence đã lọc và RCA hypothesis được xếp hạng. Service kiểm tra service, score và evidence refs trước khi hiển thị; mặc định không cấu hình provider và dùng lời giải thích deterministic. Chưa có bằng chứng Bedrock đã được triển khai/gọi thật; không có train/fine-tune LLM. Nguồn: [Bedrock adapter](../../src/sentinelops/explanations/bedrock.py), [explanation service](../../src/sentinelops/explanations/service.py), [ASGI entry point](../../src/sentinelops/main.py), [explanation runbook](../../docs/runbooks/EXPLANATIONS.md).

## 2. Thành phần nào là ML, thành phần nào là luật

| Thành phần | Vai trò và cách hoạt động | Nguồn đối chiếu |
| --- | --- | --- |
| Isolation Forest | Unsupervised anomaly detection; học phân bố cửa sổ latency healthy của Alibaba. Score bất thường tăng khi `-score_samples` tăng, được ánh xạ sang percentile bằng ECDF calibration. | `src/sentinelops/ml/models.py`: `Detector`, `fit_detector` |
| XGBoost / `ExceedanceRisk` | Binary classifier dự báo onset vượt mức breach trong tương lai 300 s. Dùng ba đầu vào latency tương đối với breach và ràng buộc đơn điệu tăng. | `src/sentinelops/ml/models.py`: `ExceedanceRisk`, `fit_exceedance_risk` |
| Median/MAD | Đối chứng thống kê: median và MAD từ train, threshold từ calibration, cùng tập feature/budget với IF. Không điều khiển alert serving của bundle hiện hành. | `src/sentinelops/ml/models.py`; `docs/adr/ADR-019-breach-relative-retrain.md` |
| Slope control | Đối chứng dự báo dùng độ dốc latency. Không phải một model deep learning và không thay XGBoost trong serving. | `scripts/alibaba_controls.py`; `configs/alibaba_local_evaluation.json` |
| SLO health | Luật latency p95 > 750 ms hoặc error rate > 5%, với điều kiện số request, coverage và persistence. Lỗi HTTP 5xx được xử lý bằng luật này vì Alibaba không có trường lỗi. | `configs/slo_health_policy.json`; `src/sentinelops/incidents/health.py` |
| Correlation / RCA | Thuật toán xác định dựa trên topology, thời gian và bằng chứng; ranking có trọng số cố định, có quyền abstain. Không có quá trình fit một neural RCA model. | `src/sentinelops/incidents/correlation.py`; `src/sentinelops/incidents/rca.py`; `configs/rca_policy.json` |
| Explanation | Deterministic RCA ranking; Bedrock tùy chọn tổng hợp narrative từ ranked hypothesis và evidence. Không thay ranking hoặc cấp remediation. | `src/sentinelops/explanations/service.py`; `src/sentinelops/explanations/bedrock.py` |
| Demo / samples | JSON minh họa và điểm giả lập phục vụ hợp đồng/demo, không phải output estimator hay dữ liệu train chính thức. | `samples/`; `src/sentinelops/demo.py` |

Dependencies ML được pin trong [pyproject.toml](../../pyproject.toml): `numpy==2.2.6`, `pandas==2.3.3`, `pyarrow==21.0.0`, `scikit-learn==1.7.2`, `xgboost-cpu==3.1.1`, cài qua extra `[ml]`. Code dùng `joblib` để lưu/load detector; bundle ghi phiên bản thư viện và kiểm tra khi nạp. Không thấy pipeline PyTorch/TensorFlow, embedding, vector database hay RAG trong mã nguồn backend được rà soát.

## 3. Dữ liệu đến từ đâu?

### 3.1. Nguồn train chính thức: Alibaba production traces

[ADR-016](../../docs/adr/ADR-016-public-production-training-source.md) quyết định dùng dữ liệu thật từ Alibaba và thay nguồn fitting local. Dataset gốc là `alibaba/cluster-trace-microservices-v2022`, bảng `MSRTMCR`, chứa thời gian phản hồi và call rate từng container/từng phút. Tài liệu mô tả nguồn tổng thể có hơn 1.300 microservices và hơn 90.000 containers; đây là mô tả nguồn từ tài liệu, không phải số lượng tự đếm lại trong checkout.

Adapter [src/sentinelops/public_data/alibaba.py](../../src/sentinelops/public_data/alibaba.py) chỉ đọc bốn cột:

- `timestamp`: thời gian của minute bucket, được quy đổi từ milliseconds sang minute index ở bước chuẩn bị train.
- `msname`: định danh microservice dùng để gom dữ liệu và chia split.
- `http_mcr`: call rate chuẩn hóa, vừa là trọng số khi gom latency vừa được cộng theo microservice.
- `http_rt`: HTTP response time, đơn vị milliseconds.

Archive dự kiến có tên `MCRRTUpdate_*.tar.gz`, kèm file `.sha256` được ghi lúc acquisition. `aggregate_archive` xác minh SHA-256, stream đúng một CSV trong tar, đọc từng chunk, bỏ hàng không có HTTP traffic hoặc latency không hợp lệ. Latency phút của service là `sum(http_rt * http_mcr) / sum(http_mcr)`, call rate là tổng `http_mcr`. Đầu ra gồm Parquet và JSON provenance với hash archive, hash Parquet, tên CSV member, số service/row/phút. `load_minutes` tiếp tục kiểm hash và gộp các aggregate trước khi tạo feature.

Điểm cần phân biệt về acquisition: ADR nói archive lấy từ vị trí OSS chính thức do publisher cung cấp; runbook hướng dẫn archive bất biến, retry và checksum. Tuy nhiên **`scripts/download_public_data.py` trong checkout chỉ hỗ trợ `rcaeval` và `smd`, không có lựa chọn `alibaba`**. Không thấy downloader Alibaba chuyên biệt hoặc URL OSS cụ thể trong các script hiện có; không nên mô tả lệnh downloader này như đã tải Alibaba end-to-end. Script Alibaba bắt đầu từ thư mục raw đã acquisition: [scripts/aggregate_alibaba.py](../../scripts/aggregate_alibaba.py).

G3 packet của **model hiện hành** ghi **240 archive có checksum**, với 6.184 train services, 2.092 calibration services và 2.128 evaluation services. Model card cũ của `badf9da0` và runbook còn nhắc files 0–119 / 0–6 giờ; không lấy phạm vi cũ làm provenance chính xác của fit mới. Do raw/report fit đầy đủ ở ngoài Git, không thể xác minh độc lập toàn bộ archive trong lần đọc repo này. Nguồn: [G3 de38fc6b](../../docs/reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md), [model card cũ](../../docs/reports/MODEL_CARD_ALIBABA_BADF9DA0.md), [model pipeline](../../docs/runbooks/MODEL_PIPELINE.md).

Nguồn Alibaba **không có HTTP status/error field và không có operator-confirmed incident labels**. Nhãn được suy ra bằng luật latency đã đóng băng; không phải nhãn incident thủ công. Điều kiện sử dụng được ADR mô tả là research/study, có yêu cầu thông báo maintainer khi công bố; không tự diễn giải thành một giấy phép thương mại rộng hơn.

### 3.2. Telemetry vận hành: nguồn inference và bằng chứng

Ba ứng dụng ở `services/checkout/app.py`, `services/payment/app.py`, `services/inventory/app.py` tạo request thật, metrics, completion logs và traces; cấu hình OTel nằm tại [services/common/observability.py](../../services/common/observability.py), log export tại `services/common/log_export.py`. Collector và Prometheus được cấu hình trong `observability/otel-collector-config.yaml`, `observability/prometheus.yml`.

[src/sentinelops/telemetry/prometheus.py](../../src/sentinelops/telemetry/prometheus.py) có source profile **`prometheus-increase-60s-5s/v2`**, truy vấn `increase(...[60s])` cho request count, error count, duration histogram và thêm `_sum/_count` để tính **mean latency**. `mean_latency_ms` dùng duration sum / count. Mean này khớp đại lượng `http_rt` dùng khi train Alibaba; p95 được giữ riêng cho SLO. Tên metric duration cụ thể trong adapter là `sentinelops_http_request_duration_ms_milliseconds_sum` và `_count`.

Luồng chính:

```text
HTTP services → OTel / Prometheus → raw captures → observations / quality checks
  → accepted health batches và lịch sử service
  → relative feature projection → IF + XGBoost → model signals → incident/IER
  → deterministic RCA → explanation renderer / Bedrock tùy chọn
```

`src/sentinelops/telemetry/core.py`, `pipeline.py`, `store.py` giữ normalization, feature envelopes, quality và artifact refs. Có adapter CloudWatch, X-Ray và infrastructure tại `src/sentinelops/telemetry/cloudwatch.py`, `xray.py`, `infrastructure.py`; sự tồn tại adapter không chứng minh telemetry AWS đã chạy hoặc được dùng làm train set. Tài liệu hiện hành vẫn ghi AWS chưa triển khai.

Đáng chú ý: `telemetry/core.py` vẫn định nghĩa **`http-red-5m/v1`** gồm request rate, p95 mean/slope và error-rate mean/slope. Đây là shape của telemetry/envelope cũ, **khác** vector six-feature `http-relative-5m/v2` của model Alibaba hiện hành. `src/sentinelops/ml/alibaba_live.py` dựng vector model từ lịch sử raw v2 được chấp nhận, dùng chung transform với training; không đem nguyên mẫu `FeatureWindow` cũ vào estimator mới.

### 3.3. Local HTTP corpus: dữ liệu đo thật dưới fault injection, chỉ để đánh giá

Nguồn này không phải vài JSON sample. Repo có testbed chạy ba service, phát HTTP traffic, áp lỗi có kiểm soát, thu Prometheus/log/trace và đo SLO từ server completions đối chiếu client ledger.

| Nhóm công cụ | File và chức năng |
| --- | --- |
| Sinh traffic / chạy scenario | `src/sentinelops/experiments/traffic.py`, `runner.py`; CLI `scripts/run_experiment.py` có seed, RPS, warmup, active, recovery, target service và fault mode. |
| Áp lỗi ứng dụng | `services/common/faults.py`: chế độ `latency` thêm delay; `error` trả HTTP 503; chỉ bật trong môi trường cho phép. |
| Capture corpus / campaign | `scripts/capture_local_corpus.py`, `scripts/capture_local_campaign.py`; implementation ở `src/sentinelops/experiments/local_corpus.py`, `local_campaign.py`. |
| Nhãn độc lập / kiểm nguồn | `src/sentinelops/experiments/labels.py`, `local_labels.py`, `verification.py`; dùng completion logs và dữ liệu client/server thay vì lấy dự đoán làm ground truth. |
| Freeze dataset | `scripts/build_dataset.py`, `src/sentinelops/experiments/datasets.py`; kiểm run hoàn tất, source sạch, immutable truth/source refs, split/session leakage, overlap, chronology và coverage. |
| S4 / đa lỗi | `src/sentinelops/experiments/s4_postgres.py`, `multifault.py`; dữ liệu phục vụ đánh giá áp lực DB/đa lỗi, không phải nguồn train chính thức. |

Plan hiện hành [configs/alibaba_local_evaluation.json](../../configs/alibaba_local_evaluation.json) pin bundle trước capture, epoch **`ald`**, source v2, feature v2, seed base `20261400`. Có năm loại slot: healthy, payment-abrupt, inventory-abrupt, payment-gradual, inventory-gradual; warmup 1.980 s, ít nhất 30 phút healthy được xác minh, delay tối đa 1.200 ms, recovery 360 s. Plan quy định `fitting_allowed=false` và không cho capture sealed final.

**Không dùng local corpus để fit, học preprocessing hoặc chỉnh threshold của model chính thức.** Ngoại lệ có phạm vi của ADR-019 là đọc lại epoch `alb` đã chấm để kiểm tra development bằng `scripts/score_development_epoch.py`, ghi số lần kiểm tra và không tính kết quả đó vào acceptance. Đường train local đã bị gỡ ngày 09/11/2026. Các tên role `train`/`calibration` còn trong capture schema hoặc manifest lịch sử không chứng minh vẫn có fitting local hiện hành.

### 3.4. Dữ liệu tổng hợp và `samples/`

Có hai nguồn tổng hợp cần tách riêng:

1. **Synthetic fault-shape gate** ở [src/sentinelops/ml/fault_shapes.py](../../src/sentinelops/ml/fault_shapes.py): sinh chuỗi latency/rate xác định theo seed, qua transform và model thật. Config tạo 180 sequences = 3 loại healthy/abrupt/gradual × 3 baseline latency (3/8/24 ms) × 2 noise sigma (0,04/0,10) × 10 seeds. Lỗi abrupt +1.200 ms hoặc ramp 20 phút; gate kiểm model có đáp ứng fault shape trước khi capture dài. Đây là **kiểm tra sau fit**, không phải 180 chuỗi dùng thay Alibaba để train, cũng không chứng minh chất lượng trên traffic thật.
2. **Software fixtures** trong `tests/test_ml_pipeline.py`, `tests/test_alibaba_training.py`, `tests/test_fault_shapes.py` và các test ML liên quan. Ví dụ fixture tạo matrix ngẫu nhiên bằng NumPy rồi fit estimator thật để kiểm scaler, serialization, checksum, inference và fail-closed. Artifact fixture mang origin `fixture-only`, không đủ điều kiện serving/activation.

Thư mục [samples/](../../samples) chứa payload ví dụ cho schema/API: `telemetry.sample.json`, `feature-window.sample.json`, `feature-envelope.sample.json`, `dataset-manifest.sample.json`, `incident-evidence-record.sample.json`, `explanation-response.sample.json`… Không phải một training corpus. Cụ thể `dataset-manifest.sample.json` tự ghi `dataset_id=synthetic-pilot-example`, `row_count=1`, `status=pilot`, `training_ready=false`, có các blocking reasons thiếu split/healthy/precursor. `feature-window.sample.json` có p95/error/timeout/log features minh họa shape cũ, không đại diện cho ordered vector Alibaba v2.

[src/sentinelops/demo.py](../../src/sentinelops/demo.py) cũng gán sẵn anomaly/risk/RCA scores cho demo incident. Các con số đó không phải kết quả chạy estimator và không được dùng làm evidence chất lượng model.

### 3.5. Dataset công khai khác và các policy lịch sử

| Nguồn | Vai trò thực tế hiện hành |
| --- | --- |
| **RCAEval** — `phamquiluan/RCAEval` | Chỉ RCA validation/development/heldout evaluation, `ml_fitting_allowed=false`. Revision pin `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`; metadata khai báo MIT. Có metrics/traces/logs và fault cases; downloader kiểm partition trước đọc/download case, xác minh publisher metadata/hash. |
| **GAMMA v1** — Kaggle `gagansomashekar/microservices-bottleneck-detection-dataset`, version ID `7638732` | Nguồn primary-training của policy cũ `training-data/v1`, đã bị thay thế. `ml/readiness.py` chặn admission vì chưa có source-backed verifier cho healthy/precursor; không được kết luận GAMMA đã train model serving hiện tại. |
| **Local HTTP / ADR-014** | Từng là nguồn train của candidate `38bcc766`; nay retired, chỉ giữ integration/history và evaluation pipeline. Không phải model báo cáo chính thức. |
| **SMD** — `NetManAIOps/OmniAnomaly` | Historical-only; không được mở rộng thành nguồn train mới. Downloader yêu cầu `--historical-reproduction`; audit kiểm 38 feature dimensions. Không phải input của bundle Alibaba hiện hành. |

Nguồn: [catalog](../../configs/public_datasets.json), [training contract v1](../../configs/training_data_contract.json), [training contract v2](../../configs/training_data_contract_v2.json), [downloads](../../src/sentinelops/public_data/downloads.py), [policy](../../src/sentinelops/public_data/policy.py), [readiness gate](../../src/sentinelops/ml/readiness.py), [ADR-016](../../docs/adr/ADR-016-public-production-training-source.md). Các JSON lịch sử vẫn có `primary-training` vì giữ nguyên quyết định cũ; cần đọc cùng ADR thay thế, không tổng hợp mọi dataset thành một tập train chung.

Không có thư mục dataset/raw/model binaries trong checkout. `.gitignore` loại `data/raw/`, `data/processed/`, `models/`, `artifacts/`. Đường dẫn model store và `artifact://...` trong report là reference tới kho bên ngoài Git, không bảo đảm file tồn tại ở máy này.

## 4. Train như thế nào?

### 4.1. Entry points và điều kiện thực thi

Pipeline được triển khai trong [scripts/train_alibaba.py](../../scripts/train_alibaba.py), [src/sentinelops/ml/alibaba_training.py](../../src/sentinelops/ml/alibaba_training.py), [src/sentinelops/ml/models.py](../../src/sentinelops/ml/models.py), theo config đóng băng [configs/alibaba_relative_features.json](../../configs/alibaba_relative_features.json).

```sh
# Yêu cầu môi trường Python tương thích và dependencies [ml].
# raw phải đã được acquisition kèm các checksum sidecar.
python -m scripts.aggregate_alibaba --raw <root>/raw --output <root>/minutes
python -m scripts.train_alibaba --minutes <root>/minutes --output <root>/fit-<date>
python -m scripts.package_alibaba_bundle --fit <root>/fit-<date> --output <root>/bundle-<date>
```

Đây là lệnh mô tả pipeline, **không được chạy trong lần rà soát này**. Train yêu cầu Git checkout sạch đã commit, output directory mới, aggregate có provenance đúng. `--development` chỉ tạo windows cho train/calibration, không đưa evaluation services vào window construction và fit này không được package. Bản train script còn import module `resource` để ghi memory usage; không nên giả định lệnh chạy nguyên trạng trên Windows chỉ từ các ví dụ shell.

Train lưu `detector.joblib`, `risk.json`, `report.json`: config/hash, fitting commit, archive provenance, counts, thresholds, gate/evaluation và các limitations. `activation_allowed=false`, `G3_accepted=false`; fit không tự duyệt hoặc activate model. Repo không cần SageMaker cho training hiện tại: ADR-019 ghi fit trên operator host, G7 vẫn giữ yêu cầu parity/deployment AWS riêng.

### 4.2. Split và phòng data leakage

- Chia **theo microservice identity trước windowing**: `int(sha256(msname),16) % 10`; bucket 0–5 train, 6–7 calibration, 8–9 evaluation, tương ứng tỷ lệ kỳ vọng 60/20/20.
- Service phải có ít nhất 120 phút dữ liệu. Train và evaluation không dùng chung service identity, nhưng vẫn có thể cùng cluster và khoảng thời gian; đây không phải chứng minh độc lập IID hay temporal holdout.
- Feature chỉ dùng dữ liệu tại/trước window end. Tương lai chỉ xuất hiện khi tạo label, không đưa vào inference features.
- Scaler và estimator chỉ fit trên train; calibration dùng chọn threshold và ECDF. Không early stopping hoặc chỉnh tham số dựa trên evaluation/final labels.

Implementation: `split_of`, `_service_series`, `service_window_arrays`, `build_arrays` trong `src/sentinelops/ml/alibaba_training.py`; fit/calibration selection trong `scripts/train_alibaba.py`.

### 4.3. Feature hiện hành

Window dài **5 phút**, step **60 giây**. Baseline là trailing median của service, lấy tối thiểu 5/tối đa 60 phút **trước window**, không tính các phút đang chấm. Cửa sổ phải có đủ dữ liệu hợp lệ để tính feature; missing/nonfinite không được tự điền zero. Gọi `b_rt` là baseline latency và `b_rate` là baseline call rate:

| Ordered feature | Công thức / ý nghĩa |
| --- | --- |
| `rt_log_level_rel` | `log(mean(rt_i / b_rt))`: mức latency trung bình tương đối. |
| `rt_slope_rel_per_min` | Hệ số dốc OLS theo phút của `rt_i / b_rt`. |
| `rate_log_level_rel` | `log(mean(rate_i / b_rate))`: mức traffic tương đối. |
| `rate_slope_rel_per_min` | Hệ số dốc OLS theo phút của `rate_i / b_rate`. |
| `rt_log_peak_rel` | `log(max(rt_i / b_rt))`: đỉnh latency trong window. |
| `rt_log_baseline_to_breach` | Train: `-log(K)` với breach = K × baseline. Serving: `log(b_rt / 750 ms)`. |

**IF chỉ dùng feature latency level và peak**, dù bundle giữ schema sáu feature. MAD control dùng cùng subset. **XGBoost không dùng traffic features**: wrapper chuyển schema trên thành ba inputs `level + breach`, `peak + breach`, `slope * exp(breach)`, tức level/peak/slope so với chính breach level. Không dùng service ID, scenario ID hoặc root-cause ground truth làm feature. Nguồn: `ExceedanceRisk.inputs` và `fit_detector` trong `models.py`; `service_window_arrays` trong `alibaba_training.py`.

### 4.4. Nhãn train

Config đặt breach multipliers **K ∈ {2,4,8,16,32,64}**, onset cần **hai phút liên tiếp** vượt mức, horizon **300 s**, confirmation tail **hai phút**.

- Với **detector healthy reference**, code giữ semantics tại K đầu tiên (=2): so latency từng phút với trailing baseline trước phút đó. Cửa sổ được coi healthy khi không có breach ở trong window và horizon+tail đã quan sát đầy đủ.
- Với **XGBoost risk**, `service_window_arrays` so future latency với **K × baseline của window hiện tại**. Positive khi window còn sạch và có hai phút liên tiếp vượt ngưỡng, phút onset đầu thuộc `(t,t+300s]`. Negative khi window và toàn bộ future horizon+tail đều quan sát đầy đủ, không có phút vượt ngưỡng. Các trường hợp chưa biết/đang breach/thiếu follow-up được gán unknown (`-1` trong arrays), không ép thành negative.

Phân biệt hai baseline trên là cần thiết: detector giữ định nghĩa healthy/onset cũ, còn risk dùng breach-relative target ADR-019. Đây là nhãn thuật toán từ production latency, không phải nhãn “root cause” hoặc xác suất sự cố do con người xác nhận.

### 4.5. Fit Isolation Forest và MAD

`fit_detector` yêu cầu ít nhất 300 train rows và 100 calibration rows. Train script truyền vào **healthy windows** tương ứng từng split.

- `RobustScaler().fit(train)`; không fit scaler trên calibration/evaluation.
- `IsolationForest(n_estimators=100, max_samples="auto", contamination="auto", random_state=42, n_jobs=1)`.
- Raw IF score là `-forest.score_samples(scaled_x)`; score trả cho consumer được normalize bằng ECDF healthy calibration.
- Raw MAD control là max absolute robust deviation theo feature, với scale `max(1.4826 × MAD, 1e-9)` tính từ train.
- Threshold cho mỗi nhánh là quantile 99% (`method="higher"`) trên calibration healthy, theo budget 1%. Raw score phải **lớn hơn** threshold mới alert; con số percentile không phải calibrated probability.

Nguồn: `Detector.raw_scores`, `Detector.predict`, `fit_detector` trong `src/sentinelops/ml/models.py`.

### 4.6. Fit XGBoost và chọn ngưỡng

Mỗi K tạo một bài toán binary; script ghép các hàng đã mã hóa breach level để fit **một model chung**. Giữ toàn bộ positive train rows, lấy ngẫu nhiên 1/10 negatives theo seed 42, gán sample weight 10 cho negatives được giữ; class weighting cũng chỉ tính từ train.

Tham số chính: **300 estimators, max depth 4, learning rate 0,1**, `objective="binary:logistic"`, `eval_metric="logloss"`, `tree_method="hist"`, `subsample=1`, `colsample_bytree=1`, `n_jobs=1`, seed 42, **monotone constraints `(1,1,1)`**. Không có test early stopping. Nguồn: `fit_exceedance_risk` trong `models.py`, risk spec trong `configs/alibaba_relative_features.json`.

Ngưỡng **`watch`** lấy quantile 99% score của calibration negatives tại **K=64**; **`degrading`** tại **K=16**. Script kiểm `0 < watch < degrading < 1`. G3 packet của `de38fc6b` ghi các giá trị làm tròn: IF **0,7131**, MAD **9,006**, watch **0,485**, degrading **0,665**. Không dùng lại threshold 0,8 của protocol/bundle cũ để mô tả ADR-019.

Đầu ra là **risk score**. ADR-002 không cho gọi là xác suất được hiệu chuẩn khi chưa đo calibration; không thấy quy trình Platt/isotonic calibration hoặc chứng cứ calibration probability trong pipeline hiện hành. Hiệu chỉnh operating threshold theo false-alert budget không đồng nghĩa hiệu chuẩn xác suất.

`models.py` vẫn có primitive `fit_risk` 50 cây/depth 3/learning rate 0,05 và bundle v1 cho fixture/GAMMA lịch sử. **Đó không phải cấu hình fit chính thức `de38fc6b`**; entry point hiện hành gọi `fit_exceedance_risk`.

## 5. Package, load và inference

[scripts/package_alibaba_bundle.py](../../scripts/package_alibaba_bundle.py) gọi `package_fit` trong [src/sentinelops/ml/alibaba_bundle.py](../../src/sentinelops/ml/alibaba_bundle.py). Bundle **`sentinelops-model/v3`**, origin **`admitted-alibaba-v2022`**, chứa ordered features, thresholds, model bytes/hashes, calibration ECDF identity, source/config/commit và library versions. Synthetic gate thất bại thì không được package; bản `--development` cũng không được package thành bundle phục vụ.

Release và activation là đường riêng: `scripts/check_alibaba_source_parity.py` kiểm profile v2 ở môi trường đích; `scripts/build_alibaba_release.py` dựng release sau parity PASS và quyết định G3 của owner. Registry ở [src/sentinelops/ml/registry.py](../../src/sentinelops/ml/registry.py) dùng release bất biến `validated-model-release/v1`, không chấp nhận fixture hoặc policy GAMMA/local retired chỉ vì ai đó đặt cờ PASS.

`SENTINELOPS_MODEL_STORE` và `SENTINELOPS_MODEL_RELEASE_REF` chọn model khi khởi tạo worker/API. Loader kiểm checksum manifest, hash từng artifact và phiên bản thư viện trước deserialize trusted local joblib. Không có upload model từ API hoặc auto-retrain/auto-promote online trong đường này.

Inference Alibaba chạy trong incident worker từ **accepted history**, qua `ml/alibaba_live.py` và `workers/model_signals.py`. Lịch sử được giới hạn theo window+baseline, ràng buộc stream/source identity và thời điểm có dữ liệu. Thiếu model, thiếu history hoặc source không hợp lệ thì signal **UNAVAILABLE**, không sinh điểm 0 giả. Signal `validated-window-model/v3` pin `model_feature_ref`, `source_batch_ref`, `inference_started_at`, `evaluated_at`; replay kiểm lại feature và estimator thật.

Khi không cấu hình model, `/v1/detect` và `/v1/risk` trả 503. Khi đã load Alibaba release, hai endpoint nhận `FeatureWindow` cũ trả **409 `model_requires_stream_history`**; kết quả đúng được đọc từ `service_signals` của incident revision. Đây là ranh giới API được thiết kế, không phải bằng chứng “repo không có inference”. Nguồn: [model pipeline runbook](../../docs/runbooks/MODEL_PIPELINE.md), `src/sentinelops/api.py`, `tests/test_ml_pipeline.py`.

## 6. Đánh giá model: đo gì và kết quả đến đâu?

### 6.1. Công cụ và protocol

| Công cụ / cấu hình | Nội dung |
| --- | --- |
| `scripts/train_alibaba.py` | Chấm evaluation services sau fit, tạo detector event metrics và risk curves theo K. |
| `scripts/alibaba_controls.py` | So IF với MAD, XGB với slope; calibration/frozen thresholds, paired bootstrap theo service ở layer 1. |
| `scripts/evaluate_alibaba_local.py` / `ml/alibaba_local_evaluation.py` | Freeze/chấm epoch local hoàn chỉnh một lần, với model/config pin từ trước. |
| `ml/evaluation.py` | Event matching một-một theo service/onset/deadline; đếm TP/FP/FN, precision/recall/F1, delay và false incidents / healthy service-hour. Alert trùng có thể thành FP; không point adjustment để biến cả đoạn thành đúng. |
| `ml/local_evaluation.py` | Persistence, risk curves/AP/PR-AUC, precursor coverage và bootstrap; giữ unknown/censored/missing denominators rõ ràng. |
| `configs/local_model_evaluation.json`, `configs/local_model_evaluation_v2.json` | Base protocol: entry 2 windows, exit 3, step 60 s, event deadline/horizon 300 s, 1.000 bootstrap replicates, seed 42, confidence 95%; không cấp activation/final access. |
| `configs/alibaba_local_evaluation.json` | Adaptation riêng cho Alibaba v2; dùng threshold risk trong bundle, clock `recorded-source-availability-counterfactual`, slope control 0,05. |

**Test phần mềm không phải benchmark model.** Các test trong `tests/test_ml_pipeline.py`, `test_alibaba_training.py`, `test_alibaba_bundle.py`, `test_alibaba_live.py`, `test_alibaba_local_evaluation.py`, `test_model_signals.py` kiểm transform, fit/load/inference, source parity và rejection paths. Các fixture có fit estimator thật nhưng không đủ để tuyên bố precision/recall sản phẩm.

### 6.2. Layer 1: unseen Alibaba services, model `de38fc6b`

Theo [G3 packet](../../docs/reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md):

| Chỉ số | Kết quả ghi nhận |
| --- | --- |
| IF event precision / recall / F1 | **0,259 / 0,476 / 0,336** |
| IF false incidents / healthy service-hour | **0,447** |
| MAD F1 | **0,300** |
| IF − MAD F1, 95% paired service bootstrap | **[+0,028; +0,045]** |
| XGB tại K=16 | AP **0,0079**, prevalence **0,0017**, recall tại degrading **0,14**, negative alert rate **0,011** |
| XGB so slope tại K=16 | AP **0,0079 vs 0,0063**; khoảng XGB−slope **[−0,0018; +0,0037]**, chưa có ưu thế thống kê rõ ràng |
| Precursor recall với lead ≥60 s, K=16 | **0,43** so slope **0,23**, trên **286 events** đủ điều kiện theo protocol |

Không nên chỉ nêu “IF hơn MAD” mà bỏ precision 0,259, hoặc gọi XGB là dự báo tốt trên production. Production spikes thường không có smooth precursor. Evaluation services tách identity nhưng cùng cluster/time span; label vẫn là latency rule.

### 6.3. Synthetic gate trước capture

G3 packet ghi IF abrupt **60/60**, không false alert, delay **150 s**, minimum saturation margin **+0,021**; MAD cũng 60/60. Risk AP trên ramps **0,995** so slope **0,720**, precursor đủ lead ≥60 s **60/60**, minimum lead 210 s, không warning trên healthy minutes. Đây là kết quả đáp ứng recipe tổng hợp cố định, không phải kết quả local corpus hoặc final holdout.

### 6.4. Layer 2: local epoch `ald`, chấm một lần 14/11/2026

[Báo cáo layer 2](../../docs/reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md) và [JSON evidence](../../docs/reports/evidence/layer2-alibaba-de38fc6b.json) ghi **5 groups × 5 slots = 25 verified runs**, `fitting_rows=0`, không chỉnh threshold, không mở holdout. Split được ghi là **development-validation**.

| Chỉ số | Kết quả và phạm vi |
| --- | --- |
| IF latency events | Checkout 6/6, Inventory 3/3, Payment 3/3: tổng **12/12**, FP=0, FN=0, F1=1,0. |
| So với MAD | MAD cũng 12/12, F1=1,0; IF−MAD interval **[0;0]**, không chứng minh IF tốt hơn MAD trên tập này. |
| False incidents | 0 trong **6,35 eligible healthy service-hours**. |
| IF detection delay | **150 s** cho cả 12 events; clock counterfactual replay, chưa phải independent live emission MTTD. |
| HTTP 5xx | **8/8**, do **SLO rule**, không phải IF học lỗi từ Alibaba; delay 150 hoặc 210 s. |
| XGB AP / slope AP | Checkout **0,950 / 0,835**; Inventory **0,947 / 0,808**; Payment **0,953 / 0,824**. |
| Precursor đủ lead ≥60 s | **20/20 service-run events**, từ 10 gradual runs; minimum lead 269,96 s, gần giới hạn horizon 300 s. |
| Score/label availability | Không có labelled window thiếu prediction; **358 windows** thiếu risk target do chưa đủ future confirmation. |

AP ở local cao hơn nhiều so Alibaba do recipe lỗi khác. XGB flag rate trên negative windows local là **0,36 ở Checkout**, **0,08 ở Inventory/Payment**, dù healthy-run windows không có warning. Negatives còn chứa abrupt faults và phần ramp nằm ngoài horizon; không được diễn giải calibration budget 1% thành bảo đảm chỉ 1% false warning ở mọi môi trường.

Giới hạn chính thức: layer 2 chỉ hỗ trợ S1/S3 development; chưa full S2/S4 quality evaluation, chưa G8 heldout, chưa live/arrival-aware MTTD đầy đủ. Bootstrap theo 5 groups trên một host có tính điều kiện, không chứng minh tổng quát hóa production. Các lead gần 270 s bị ràng buộc bởi protocol, không cho biết model có thể báo sớm tối đa bao lâu.

### 6.5. Lịch sử và bằng chứng integration không được cộng nhầm

- Bundle Alibaba cũ **`badf9da0`**, feature v1, đã thất bại ở local `alb`: IF **1/12** latency events, XGB precursor **0/20**; Q-04/Q-05/Q-07 development FAIL. ADR-019 thay thiết kế/target/subset features và retrain. Current loader từ chối feature v1 này. Nguồn: `docs/reports/LAYER2_LOCAL_ALIBABA_BADF9DA0.md`, ADR-019.
- Local activation report của `de38fc6b` ghi source parity và native integration PASS trên máy B, replay được ba model signals, API/incident có model dependency thật. Đây là integration evidence lịch sử, không chứng minh runtime máy hiện tại đang ready. Nguồn: `docs/reports/LANE_B_ALIBABA_DE38FC6B_ACTIVATION.md`.
- Cùng report giữ một S4 development capture: có model windows/IF flags nhưng RCA **abstain**, RDS đứng thứ ba; không chứng minh tìm đúng root cause. Independent IF alert-emission MTTD vẫn chưa đo. Không được dùng window flag counts thay event precision/recall.

## 7. RCA có training không?

**Không thấy training một model RCA trong code hiện hành.** [configs/rca_policy.json](../../configs/rca_policy.json) đặt trọng số: anomaly strength 0,25; temporal precedence 0,20; dependency trace relevance 0,25; log support 0,15; recent change support 0,15. Minimum score/coverage đều 0,75, minimum margin 0,15. `incidents/rca.py` tính weighted score, giữ missing support, không phân phối lại trọng số và abstain khi thiếu bằng chứng/margin/score.

IF cung cấp anomaly strength và XGB cung cấp risk facts vào IER; chúng không tự xác nhận nguyên nhân. Infrastructure pressure có luật riêng cho RDS, không giả làm output IF của business service. LLM không thay ranking.

Đánh giá RCA dùng `scripts/evaluate_rcaeval_native.py`, `scripts/evaluate_local_rca.py`, với profile/policy/partitions ở `configs/rcaeval_native_profile.json`, `configs/rcaeval_partitions.json`, `configs/local_rca_evaluation.json`. [RCA development report](../../docs/reports/RCA_DEVELOPMENT.md) ghi canonical RCAEval chỉ **3 development cases**: descriptive top-1=0, top-3=1/3, MRR=1/6, cả ba abstain. Trên corpus HTTP local, 10 fault runs có descriptive top-1 10/10 nhưng đều abstain, **qualified top-1=0/10**. Không lấy ranking mô tả làm qualified RCA accuracy hoặc chứng cứ train ML trên RCAEval.

## 8. LLM: có gì, chưa có gì?

### 8.1. Tích hợp có thật trong code

[src/sentinelops/explanations/bedrock.py](../../src/sentinelops/explanations/bedrock.py) có factory `bedrock_provider(region, model_id, converse)` cho Bedrock Converse và runtime boto3 tùy chọn. LLM nhận evidence facts đã qua lọc cùng RCA hypothesis, rồi trả service/score/evidence refs và lời giải thích cho operator; service kiểm tra lại các giá trị có thể đối chiếu trước khi trả response. Không pin sẵn một foundation model cụ thể.

Khi caller thật sự gắn transport, lời gọi Converse dùng system instruction, một user message JSON, `maxTokens=512`, `temperature=0`, không tool configuration. Provider identity hash region/model/prompt/instructions/config để theo dõi phiên bản.

`create_app` ở [src/sentinelops/api.py](../../src/sentinelops/api.py) tự nối Bedrock runtime khi có đủ `SENTINELOPS_BEDROCK_REGION` và `SENTINELOPS_BEDROCK_MODEL_ID`; thiếu cấu hình thì dùng deterministic fallback. Có cấu hình chỉ chứng minh runtime được nối, không chứng minh AWS cấp quyền hoặc model đã được gọi thành công.

### 8.2. LLM được phép làm gì?

Theo implementation `explanations/service.py`, provider nhận incident ID/revision, state/severity, availability, affected services, structured service signals, ranked hypotheses và tối đa **64 evidence cards đã lọc**. Card gồm ID/type/service/time/fact giới hạn; fact có thông tin nhạy cảm hoặc nội dung giống chỉ dẫn sẽ bị loại. Source URI, actor/reason và timeline không được đưa vào remote request.

LLM trả JSON gồm `incident_id`, `revision`, `prompt_version`, **`card_ids`** và
`root_cause`. `root_cause` phải chọn service từ ranked RCA, sao chép RCA score,
human-readable explanation và evidence refs là tập con của hypothesis/cards.
Service khôi phục thứ tự nguồn và render claims cục bộ bằng evidence gốc. LLM
không sửa số/thời gian, tạo bằng chứng, thêm service/reference, biến hypothesis
thành certainty hoặc thực thi Kubernetes/AWS action.

Deadline mặc định 10 s gồm cả queueing, concurrency 4, không retry; ba lần lỗi mở circuit 30 s. Timeout, transport failure, JSON sai, stale revision, unknown/duplicate card IDs, request quá lớn hoặc thiếu provider đều dùng deterministic fallback trên **cùng revision**. Claims giữ evidence refs. Nguồn: `ExplanationProvider`, `_validate_selection`, `_cards`, `_render`, `explain_incident`.

### 8.3. Training và evaluation LLM

**Không có script train/fine-tune LLM, dataset instruction tuning, LoRA hoặc quy trình đánh giá một LLM đã train trong repo được rà soát.** Đây là tùy chọn inference với foundation model bên Bedrock, không phải model do SentinelOps học từ telemetry.

`tests/test_explanations.py`, `tests/test_explanation_bedrock.py`, `tests/test_explanation_api.py` kiểm protocol, bounded claims, fallback, timeout/circuit, reference/revision và response wrapper với transport/fixture. Các test này không chứng minh Bedrock availability hoặc chất lượng giải thích của một model cụ thể. `docs/PROJECT_STATUS.md` và runbook ghi **G5 chưa nghiệm thu**, AWS/Bedrock chưa triển khai; không có số liệu live latency/cost/chất lượng LLM để báo cáo.

Ở working tree hiện tại `package.json` có dependency `@google/gemini-cli` chưa commit. Không thấy backend SentinelOps import/gọi CLI đó để detection, risk, RCA hay explanation; sự có mặt của công cụ phát triển này không đủ để kết luận sản phẩm đang dùng Gemini. Đây là thay đổi có sẵn trước lần rà soát, không thuộc pipeline model chính thức.

## 9. Những điều có thể khẳng định và giới hạn kiểm chứng

1. **Có train:** entry point `scripts/train_alibaba.py` fit scikit-learn IF và XGBoost thực, có preprocessing/calibration/artifact packaging. Không nên viết “repo không có training”.
2. **Nguồn train hiện hành là Alibaba**, không phải `samples/`, local HTTP, RCAEval hay tổng hợp mọi dataset từng được nhắc. Local-data fitting đã retired; GAMMA chưa có verifier admission hợp lệ; SMD chỉ historical.
3. **Synthetic data phục vụ gate/tests**, local injected traffic phục vụ evaluation/integration. Chúng có vai trò khác dữ liệu production dùng fit và khác payload mẫu.
4. **Model hiệu quả theo phạm vi đã đo:** kết quả local development tốt trên S1/S3 nhưng production Alibaba forecast còn yếu; chưa đủ để tuyên bố tổng quát hóa, RCA chính xác, live MTTD đầy đủ hoặc final quality acceptance.
5. **Có Bedrock LLM adapter cho grounded root-cause narrative; mặc định dùng deterministic fallback. Chưa có Bedrock runtime evidence hoặc LLM training.** Model ID phụ thuộc cấu hình môi trường.
6. **Giới hạn của lần rà soát:** không có raw archives/model binaries/store trong checkout, không chạy lại train/evaluation và không kiểm runtime. Các đường `artifact://`/`artifacts/...` trong report được trích như provenance lịch sử, không coi là artifact đã được xác minh trên máy hiện tại.

Các tài liệu nền để đọc tiếp: [model pipeline](../../docs/runbooks/MODEL_PIPELINE.md), [ADR-002](../../docs/adr/ADR-002-ml-models.md), [ADR-016](../../docs/adr/ADR-016-public-production-training-source.md), [ADR-019](../../docs/adr/ADR-019-breach-relative-retrain.md), [G3 model hiện hành](../../docs/reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md), [layer 2](../../docs/reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md), [explanation runbook](../../docs/runbooks/EXPLANATIONS.md).
