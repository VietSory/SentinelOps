# Lane B benchmark và nghiệm thu local

> Cập nhật 27-11-2026: tiêu chí non-inferiority và việc cần làm trước freeze v17 nằm trong [kế hoạch v17](MODEL_BENCHMARK_V17_PLAN.md).
>
> Cập nhật 28-11-2026: khai báo trước Lane B v17 theo SPEC v2 (rule event Alibaba proxy D1 nhãn `> 2,0×` median với warmup từ phút 259, ứng viên `adsketch_mad_or` D2, 9 nhánh gate đối xứng D3 tách `scope='alibaba-proxy-ranking'`, tên hàng "ADSketch, biến thể tìm kiếm giới hạn" và trạng thái B-1..B-8) nằm trong [mục 5 kế hoạch v17](MODEL_BENCHMARK_V17_PLAN.md#5-lane-b-khai-báo-trước-v17) và [ADR-021](../adr/ADR-021-lane-b-v17-preregistration.md).

Worktree `/tmp/sentinelops-model-benchmark-b`, nhánh `feat/model-benchmark-b`, nền `f5615ae`. Đặc tả đối chiếu là [runbook tại 5f9067c](https://github.com/VietSory/SentinelOps/blob/5f9067cac29f05f975a8dddbdc610ea1d8e819d2/docs/delivery/MODEL_BENCHMARK_RUNBOOK.md). B phát triển detector ADSketch frozen, Isolation Forest và Median/MAD ngoài đường inference phục vụ. Quyết định release hiện tại là **KEEP** đối với baseline `de38fc6b`. Hiệu quả của ứng viên mới còn **INCONCLUSIVE**.

Cập nhật development (mốc lịch sử 27-11-2026, trước v17): B01–B06 đã được kiểm tra lại trên source `09b3bef`; B02 và B06 có support từ Alibaba proxy thật. Trong 2.039 anchor calibration, có 65 known và 6 novel mang nhãn proxy dương, cùng 72 anchor thiếu score. Protocol và candidates chung bản tiền v17 (`36a64094…`) đã freeze ở mốc này (v17 chưa freeze). [Evidence và hash](../reports/evidence/model-benchmark-freeze-development.json). Bảng dưới ghi trạng thái bàn giao lịch sử trước cập nhật này.

Cập nhật lịch sử ngày 27-11-2026: A đã đăng ký ma trận chung 35 slot và bắt đầu thu nhóm `bench-final-01` trên capture revision `45294cc` (lúc ghi nhận, slot đầu tiên đang warmup). Đến khoảng 18:00 ngày 27-11-2026, máy khởi động lại khiến capture `bench-final-01` (registration `5a2d7629…`) dừng khi slot healthy đang warmup; registration này bị bỏ, chưa có slot nào hoàn tất và chưa có kết quả detector holdout ([mục 4 kế hoạch v17](MODEL_BENCHMARK_V17_PLAN.md#4-phản-hồi-review-ngày-27-11-và-sự-cố-capture)). B dùng cùng group, run, raw refs và oracle đã xác minh của A sau khi đợt capture mới được seal; không thu lại dữ liệu riêng.

| Chặng | Bằng chứng cần bàn giao | Tình trạng |
| --- | --- | --- |
| Ngày 1 | Pin bản tác giả, môi trường, output Yahoo và SHA | **PASS**. Precision 0,524, recall 0,682, F1 0,554 trên 67 chuỗi Yahoo |
| Ngày 2 | B01 parity nhỏ, B02 ranh giới train, B03 nhân quả, B04 matching, B05 mất nguồn/model, B06 known/novel | B01 khớp 10 cửa sổ bất thường, 50 gán pattern và bán kính cụm trong sai số `1,8e-15`. **BLOCKED** cho GO thực nghiệm khi thiếu B02/B06 trên dữ liệu thật |
| Ngày 3–4 | Fit train Alibaba, hiệu chuẩn theo dịch vụ rời train, pin fit/checkpoint, đối chiếu launcher | **BLOCKED** cho số liệu thật. B đã có pipeline thử với fixture và verifier đọc lại checkpoint |
| Ngày 5–6 | Ba dòng detector và ledger cùng group/run của A | **NOT_RUN**. Phần score development đã có. A giữ protocol, freeze ứng viên và receipt capture chung |
| Ngày 7 | So kết quả theo cặp, quyết định model, kiểm bản phục vụ và rollback | **KEEP** baseline. Round-trip store đã đạt ở local. Hai host release rollback lịch sử vắng mặt |
| Ngày 8–9 | Rehearsal ba lượt cùng release, 60 phút healthy có score, UI/Grafana, approval, dispatch, phục hồi và CI đúng SHA | **NOT_RUN** cho rehearsal mới và CI remote. Chưa đủ image và target nghiệm thu đã ghim |

## Phương pháp

Cả ba detector dùng cùng cửa sổ latency scalar, log-relative theo median tối đa 60 phút liền trước của đúng service. Gap đặt lại history. Hash service Alibaba theo split ADR-019. Bucket 0–2 làm normal reference, 3–5 làm discovery và 6–7 làm calibration. Lưu SHA từng cohort và số segment hỗ trợ. Fit chỉ đọc train/calibration. Điểm chuẩn hóa, pattern, radius và model digest được cố định trước chấm. Mẫu ngoài radius của pattern gần nhất được ghi novel.

ADSketch dùng các bước matrix profile và gom pattern của bài ICSE 2022. Đường fit benchmark dùng tìm kiếm có giới hạn, scaler từ reference, nhãn proxy để xác nhận mẫu bất thường và thư viện frozen. So parity B01 dùng chế độ offline của thuật toán trên input nhỏ đã ghim. Mã GPLv3 của tác giả và Python tham chiếu nằm trong thư mục riêng. [Bài báo](https://arxiv.org/abs/2201.02944) · [Mã tác giả đã ghim](https://github.com/OpsPAI/ADSketch/tree/f7bd642c93ff25612514ed711f47cc095a6a66fd)

Hiệu chuẩn yêu cầu ít nhất 100 negative window có score cho mỗi model, giữ nguyên điểm bằng nhau và dùng `score >= threshold` ở budget 1%. Window hiệu chuẩn có khoảng tách theo chiều dài history cộng mẫu. Fit lưu model và các điểm vận hành trong `fit/b/fit.json`. F5 chạy lại dự đoán sau khi đọc checkpoint hoàn chỉnh và so score với tolerance `1e-6`. Điểm dừng an toàn nằm giữa các fit hoàn chỉnh.

Chấm theo oracle riêng, dùng cửa sổ match `[onset, onset+300s]` và một alert cho một event cùng run/service. Event thiếu score vẫn nằm trong recall. Ledger lưu event 5xx để đối chiếu với bảng latency. Báo coverage toàn phần và giao ba model, số alert/miss, FP/service-hour, MTTD, p95 thời gian inference, phân nhóm service và bootstrap 1.000 lần seed 42 ở nguyên group. Clock replay và arrival-aware tạo hai report riêng.

## Lệnh local

Chạy từ worktree B. Dữ liệu và report ở ngoài checkout. Trên NixOS đặt đường thư viện runtime dùng cho các wheel Python đã cài.

```bash
export PY=/home/nvtank/year4/new/SentinelOps/.venv/bin/python
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-${NIX_LD_LIBRARY_PATH:-}}"
export PYTHONPATH="$PWD/src:$PWD" OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
export BENCH=/home/nvtank/year4/new/SentinelOps/artifacts/lane-b/model-benchmark-20261125
export PROTOCOL="$BENCH/protocol.json"
bm() { "$PY" -m scripts.model_benchmark --root "$BENCH" --protocol "$PROTOCOL" "$@"; }

"$PY" -m pytest -q tests/test_model_benchmark_detector*.py tests/test_model_benchmark_launcher.py
node --test tests/ui/demo_upgrade_wp4.test.mjs
bm audit --lane b
```

A bàn giao protocol development và nguồn Parquet `MCRRTUpdate_*.parquet` kèm sidecar JSON của đúng tập archive. Dùng `input/fit-b.json` dưới benchmark root với các trường sau. Chốt các giá trị config cùng A trước fit. Đây là mẫu định dạng.

```json
{
  "schema_version": "detector-fit-input/development-v1",
  "aggregate_dir": "input/minutes",
  "provenance": {
    "path": "input/fit-report.json",
    "sha256": "58a30c257fb6e181215c600dd1385faa16f5bc15ccacb35df87db69e6838f315"
  },
  "config": {"bucket_seconds": 60, "window_length": 5, "seed": 42},
  "persistence": 2
}
```

```bash
bm fit --lane b --lane-b-input "$BENCH/input/fit-b.json"
sha256sum "$BENCH/fit/b/fit.json"
```

Đặt SHA vừa nhận vào `input/calibration-b.json` với `schema_version` bằng `detector-calibration-input/development-v1` và trường `fit_sha256`. `calibrate` đối soát lại điểm vận hành từ ledger trong snapshot fit. Fit thử để lấy bằng chứng B02/B06 có thể ghi checkpoint khi protocol còn BLOCKED. Kết quả CLI giữ `status=blocked` và ghi riêng `fit_status` cùng lý do audit.

```bash
bm calibrate --lane b --lane-b-input "$BENCH/input/calibration-b.json"
```

Với archive ADR-016 development đầy đủ đã materialize, dùng một bản local mà B có quyền ghi certificate dẫn xuất. Đầu vào rehearsal cuối sử dụng runner và gate chung đã nêu trong runbook.

```bash
bm prepare-detector-local --archive "$LOCAL_DEV_ARCHIVE" --fit-sha256 "$FIT_SHA256"
bm freeze-protocol
export SCORE_INPUT="$BENCH/input/detector-local-replay/score.json"
bm score --lane b --lane-b-input "$SCORE_INPUT"
bm report --lane b --lane-b-input "$SCORE_INPUT"
bm verify-report --lane b --lane-b-input "$SCORE_INPUT"
```

Score development đọc protocol freeze và descriptor `detector-score-input/development-v1` gồm `dataset`, `clock`, `fit_sha256` cùng các pin `windows`, `events`, `admission`. Hai đầu vào đầu là Parquet. Mỗi pin gồm đường dẫn tương đối và SHA-256. Dataset là `alibaba-proxy` hoặc `local-development`. Window lưu khóa dataset/group/run/service/bucket, latency mean `value` theo ms, cutoff `decision_at`, thời điểm nguồn đến `available_at`, `eligible`, `reason`, nhãn oracle `y`, `label_reason` và cờ `healthy`. Event lưu khóa cùng `event_id`, `onset_at`, `confirmed_at`, `kind`, `eligible` và `reason`. Các clock dạng UTC. Với clock arrival-aware, lấy cutoff từ nguồn ghi lại.

Receipt `detector-projection/development-v1` ghim cùng dataset, clock, windows, events, `holdout_opened=false` và `source_identity`. Với Alibaba proxy, `source_identity.data_manifest_sha256` khớp protocol. Với local, `source_identity` là audit có đủ source result và oracle refs của archive ADR-016. `prepare-detector-local` xuất receipt này khi tái audit archive và phát projection replay. Rerun trên cùng input trả lại cùng bộ artifact. Các file `detector.csv`, Parquet và `summary.json` đi cùng `manifest.json` dưới `report/b/<dataset>/<clock>`. `verify-report` chạy lại inference và đối soát ledgers, số dòng, kết quả và các SHA. Timing runtime lấy từ lần score đã lưu.

## Handoff để qua gate còn thiếu

### Kiểm chứng local trước PR

| Kiểm tra | Kết quả |
| --- | --- |
| ADSketch gốc `f7bd642c` | 67 chuỗi Yahoo. Precision 0,524, recall 0,682, F1 0,554. SHA-256 của log `339f5f19c2bee239142c4798cde0523de88852d86b188bf30d886a2f4f464cc9` |
| B01 | 10/10 vị trí subsequence bất thường và 50/50 gán pattern khớp bản tham chiếu. Sai số bán kính lớn nhất `1,776e-15` |
| Fit và phục hồi trên fixture | 306 score tái tạo với sai số 0. Mỗi detector có 101 negative và 1 positive calibration. Rule giữ tie ở ngân sách 1% đã được kiểm |
| Regression Python | 127 test benchmark đạt. Ca scoring bundle `de38fc6b` đạt khi chạy riêng với đường dẫn local. Bộ LTFP CPU còn skip vì môi trường local thiếu PyTorch |
| Demo checker và UI | 58 test Python và 1 test Node đạt. Baseline load và archive/restore thành công. Image chạy offline trả `503 model_unavailable` khi thiếu model |

Log, input B01, receipt image và SHA-256 được giữ trong `artifacts/lane-b/model-benchmark-20261125` trên máy B.

### Dữ liệu và môi trường cần từ A và B

- A giao đủ 120 Parquet và sidecar của snapshot P0, source manifest, split, protocol và tập event oracle. Manifest bundle và fit report trên máy B đã khớp hash P0. Dataset source chưa materialize trên máy B. B dùng cùng split để đối chiếu B02/B06, fit thật, hiệu chuẩn và giao `fit/b` cùng SHA.
- A+B khóa ứng viên và mở capture chung theo protocol. B chấm ba detector trên cùng group/run, đối chiếu denominator và số mẫu. Nếu làm thí nghiệm không có đủ sự kiện, dùng `INCONCLUSIVE` và giữ baseline phục vụ.
- B mang đúng cặp Payment image `e520e702…` và `766dd5f5…` từ kho nội bộ được duyệt hoặc cùng owner chốt cặp image đang có trong cache local. Cần namespace/testbed riêng, UID deployment, known-good ledger, stream đã commit và approval của on-call. `scripts.check_risk_quiet` và `scripts.check_demo_round` đọc endpoint và receipt của cùng lần rehearsal.
- Khi có release đã chọn và code sạch, đối chiếu digest runtime/rollback, thu receipt API/UI/Grafana và xác minh CI của đúng SHA theo workflow remote.
