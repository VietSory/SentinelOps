# Đặc tả thực thi benchmark SentinelOps trong 9 ngày

**Người chạy đầu tiên là A.** B làm ADSketch và đường demo song song. Đầu ra ngày 9 gồm bảng cảnh báo trước vi phạm SLO, bảng phát hiện sự kiện, quyết định model cho từng nhánh và bằng chứng demo local với release đã chọn. Một kết quả âm hoặc chưa phân biệt được hai model vẫn là kết quả nghiên cứu hợp lệ. Chỉ dùng chữ *sẵn sàng thử AWS dev* khi kiểm chứng local và CI của đúng SHA đã đạt. [Kế hoạch đã thống nhất](MODEL_BENCHMARK_PLAN.md) · [Gate chất lượng](../architecture/CONSTRAINTS_AND_GATES.md)

| Ngày | A bàn giao | B bàn giao | Qua chặng khi | Có thể tắt máy |
| --- | --- | --- | --- | --- |
| 1 · 25/11 | Baseline đúng hash, số liệu graph, smoke hai biến thể | Pin mã tác giả, chạy mẫu độc lập | Đối chiếu được P0–P2 bên dưới | Sau khi lệnh kết thúc |
| 2 · 26/11 | Graph trên khoảng thứ hai, cohort có nhãn, source parity, oracle latency | Kết quả tái hiện, phân đoạn train, test nhân quả | D2-A và D2-B có GO/BLOCKED riêng, protocol có SHA | Sau khi ghi receipt |
| 3 · 27/11 | Fit có cạnh và self-only, đối chứng cùng split | Fit mẫu offline, IF/MAD; test mẫu mới | Kết quả F1–F4, checkpoint fit đọc lại được | Giữa hai fit hoặc sau checkpoint khôi phục đã thử |
| 4 · 28/11 | Khóa threshold, candidate và mã chấm; chạy thử capture trên development | Giao artifact theo hash, nối adapter, review manifest | Freeze trước holdout và preflight capture đạt | Trước khi mở nhóm capture |
| 5–6 · 29–30/11 | Một capture chung, chấm bốn hàng forecast | Chấm ba hàng detector trên cùng group/run | Oracle, coverage, raw predictions, đối soát mẫu số và CI thống kê đạt | Sau một nhóm hoàn chỉnh đã xác minh |
| 7 · 01/12 | Bảng paired, ablation, ADR theo từng nhánh | Kiểm image, mất model, rollback release | Số trong bảng tái tạo được từ ledger; quyết định có căn cứ | Sau report/ADR |
| 8 · 02/12 | So replay với worker thật | Rehearsal local, UI/Grafana/thông báo/phê duyệt | Đúng release digest, đủ receipt, phục hồi đã xác minh | Sau hồi phục và lưu receipt |
| 9 · 03/12 | Chốt phạm vi và bàn giao | Xác nhận release trước/sau và CI | Remote SHA đúng, CI thành công, gate còn thiếu ghi rõ | CI chạy ở GitHub sau push |

Ngày trong bảng tính từ 25/11/2026. Các mốc thời gian là ngân sách lập lịch. Nếu một lane BLOCKED, lane kia tiếp tục. A giữ contract feature/worker/registry, B giữ module ADSketch và đóng gói. Trao SHA cùng artifact cho nhau trước giao điểm. [Phân quyền A/B](WORKSTREAM_CONTRACT.md)

## 1. Chạy ngay trên máy A

Các khối dùng Bash. Chạy từ checkout SentinelOps có đặc tả này. Khối dưới dùng Python ML của checkout hiện tại hoặc đường dẫn trong biến `SENTINELOPS_PY`. Giữ terminal này cho các khối tiếp theo. Khi mở terminal mới, chạy lại khối thiết lập. Trong toàn bộ tài liệu, **HIỆN CÓ** là lệnh đã có entrypoint. **CẦN TRIỂN KHAI** là hợp đồng bàn giao cho A hoặc B. Mỗi chặng có cả lệnh kiểm và kết quả đạt.

### P0 · Môi trường, danh tính dữ liệu và baseline — HIỆN CÓ

```bash
set -euo pipefail
export CODE="$(git rev-parse --show-toplevel)"
cd "$CODE"
export PY="${SENTINELOPS_PY:-$CODE/.venv-ml/bin/python}"
export DATA="${SENTINELOPS_ALIBABA:-$HOME/.local/share/sentinelops/public/alibaba-v2022}"
export BENCH="${SENTINELOPS_BENCH_HOME:-$HOME/.local/share/sentinelops/benchmarks/forecast-detector-v1}"
export RAW="${LTFP_RAW_DIR:-$BENCH/input/callgraph}"
export PYTHONPATH="$CODE/src:$CODE"
export BASE=3fc21c978fe9e0597f6fcf294d017ae9950f1693
export BUNDLE_SHA=d52df5846ac5027507d96b67c751f02f2c5519e3ea970fbdc3599207d18eb84e
export FIT_SHA=58a30c257fb6e181215c600dd1385faa16f5bc15ccacb35df87db69e6838f315
mkdir -p "$BENCH/input" "$BENCH/preflight" "$RAW"
test -x "$PY"
git cat-file -e "$BASE^{commit}"
"$PY" - <<'PY'
import os, sys, json
from pathlib import Path
from sentinelops.public_data.alibaba import file_sha256
assert (3, 11) <= sys.version_info[:2] < (3, 13)
root = Path(os.environ['DATA'])
bundle = root/'bundle-20261113/manifest.json'
fit = root/'fit-20261113/report.json'
assert file_sha256(bundle) == os.environ['BUNDLE_SHA']
assert file_sha256(fit) == os.environ['FIT_SHA']
b, f = json.loads(bundle.read_text()), json.loads(fit.read_text())
assert b['provenance']['source_archives'] == f['source_archives']
assert len(f['source_archives']) == 120 and f['config']['seed'] == 42
for name, digest in b['artifacts'].items():
    assert file_sha256(bundle.parent/name) == digest, name
for a in f['source_archives']:
    q = root/'minutes'/a['archive'].replace('.tar.gz', '.parquet')
    assert file_sha256(q) == a['parquet_sha256'], q.name
assert f['fitting_source_commit'] == '3198e708af08e41893c705c9d64bdb6543bb5a8b'
print('P0 PASS | 120 Parquet |', b['model_version'], '| seed 42')
PY
```

Kỳ vọng model `de38fc6bb89896856556e13e7c6142ba21c8d0fea88ebd082f95e96d4e5b0e57`. Bản fit ghim 120 archive. Số 240 trong báo cáo G3 cũ chưa đối soát được với snapshot này. Nếu kiểm tra toàn vẹn **raw MSRTMCR** là điều kiện nhập một bản sao dữ liệu mới, băm từng file raw với `archive_sha256` trong cùng fit report trước khi tái tạo Parquet. Máy thiếu snapshot lấy bundle, fit report và thư mục `minutes` theo cùng manifest từ store của A. P0 dừng ở tên file đầu tiên lệch hash. [Provenance của baseline](../reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md) · [ADR-019](../adr/ADR-019-breach-relative-retrain.md)

### P1 · Kiểm tra graph theo hai cách — HIỆN CÓ

Nếu máy có export pilot đã xác minh, đặt `LTFP_PILOT_EDGES` bằng đường dẫn tuyệt đối tới `train-pilot-edges.json`. Trên máy mới, hàm sau tải trực tiếp raw từ Alibaba. Chỉ cần **một** trong hai đường để tái tạo bảng. Chạy lại với đường kia nếu cần đối soát. Hàm giữ file dở ngoài tên cuối, kiểm gzip và checksum trước khi cấp receipt. Với shard mới 48–51, SHA tính lúc nhận là fingerprint của lần tải qua HTTPS. Giữ cả header, URL và fingerprint để lần sau đối chiếu. [Alibaba v2022](https://github.com/alibaba/clusterdata/tree/master/cluster-trace-microservices-v2022)

```bash
get_graph() (
  set -euo pipefail
  cd "$RAW"
  name="CallGraph_$1.tar.gz"
  expected="${2:-}"
  if [[ -f "$name" && -f "$name.sha256" ]]; then
    recorded="$(awk 'NR==1{print $1}' "$name.sha256")"
    [[ -z "$expected" || "$expected" == "$recorded" ]]
    printf '%s  %s\n' "$recorded" "$name" | sha256sum --check --status
    exit 0
  fi
  test ! -e "$name"
  url="https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2022MicroservicesTraces/CallGraph/$name"
  work="$(mktemp -d "$RAW/receive.XXXXXX")"
  curl --fail --location --silent --show-error --retry 2 --max-time 300 \
    --max-filesize 268435456 --dump-header "$work/headers" --output "$work/$name" "$url"
  gzip -t "$work/$name"
  digest="$(sha256sum "$work/$name" | awk '{print $1}')"
  [[ -z "$expected" || "$expected" == "$digest" ]]
  printf '%s  %s\n' "$digest" "$name" > "$work/receipt"
  printf '%s\n' "$url" > "$work/url"
  mv "$work/headers" "$name.headers"
  mv "$work/url" "$name.url"
  mv "$work/receipt" "$name.sha256"
  mv "$work/$name" "$name"
  rmdir "$work"
)
if [[ -n "${LTFP_PILOT_EDGES:-}" ]]; then
  INPUT=(--pilot-edges "$LTFP_PILOT_EDGES")
else
  get_graph 20 9e262b57b77c0f9d22494cbc2c9cb2eebc80f91c0efb49f581eccce1f9950fbe
  get_graph 21 1f4eff5ce8f51e25af3cfd0f1d0d897715c203dd2b16054b56f886cb569bd4e2
  get_graph 22 8bf0a73d43d6d266aa72408bc88ebe8b249d075b2d7f07abf1db762961232a34
  get_graph 23 365dacdc7c95929dbc08ffeb043ad0c921c2a2c512e931bc0fc84b73077f71bc
  INPUT=(--callgraph "$RAW"/CallGraph_{20,21,22,23}.tar.gz)
fi
"$PY" -m scripts.audit_ltfp_graph_support --metrics-root "$DATA" "${INPUT[@]}" \
  --period 162 191 --period 324 353 > "$BENCH/preflight/support-20-23.part"
mv "$BENCH/preflight/support-20-23.part" "$BENCH/preflight/support-20-23.json"
"$PY" -m json.tool "$BENCH/preflight/support-20-23.json"
"$PY" - "$BENCH/preflight/support-20-23.json" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    value = json.load(stream)
for key, expected in [('coobserved', (170, 223, [6249, 6230], [4577, 4571])),
                      ('trace_topology', (9928, 5401, [6314, 6310], [4633, 4634]))]:
    item = value[key]
    rows = item['support']
    assert [r['period'] for r in rows] == [[162, 191], [324, 353]]
    assert [r['metric_node_minutes'] for r in rows] == [182943, 182200]
    observed = (item['directed_edges'], item['nodes'], [r['on_edge_node_minutes'] for r in rows],
                [r['usable_directed_edge_minutes'] for r in rows])
    assert observed == expected, (key, observed)
print('P1 PASS')
PY
```

| Graph dựng từ phút 60–71 trên train | Cạnh / node | Phút 162–191 · service-phút có hàng xóm / có metrics | Phút 324–353 · cùng mẫu số |
| --- | ---: | ---: | ---: |
| Chỉ cạnh từng có metrics ở hai đầu trong cùng phút dựng graph | 170 / 223 | 6.249 / 182.943 | 6.230 / 182.200 |
| Toàn bộ cạnh RPC/HTTP hợp lệ từng thấy giữa hai service train | 9.928 / 5.401 | 6.314 / 182.943 (3,45%) | 6.310 / 182.200 (3,46%) |

`support[*].usable_directed_edge_minutes` của graph toàn bộ phải là 4.633 và 4.634. Mỗi service-phút có hàng xóm chỉ tính một lần dù service có nhiều cạnh. Số liệu nói về sự có mặt của hàng metrics; cần kiểm tiếp cửa sổ feature, nhãn và thời điểm đến. Phép đo cùng phút trong pilot cũ là 737/50.851 **cạnh-phút**; mẫu số đó khác bảng trên. Hai cách đọc raw và export phải cho cùng `graph_minutes`, `coobserved` và `trace_topology`.

**Thử khoảng dựng graph thứ hai trước D2.** Đọc từ shard 48–51 (phút 144–155). Khóa hai khoảng chấm như trên trước khi xem kết quả. Giữ `RAW` riêng cho một người tải.

```bash
get_graph 48 8f41d49e5e313356881527d1e41681e356cc2b3d22ef7cd7c34653945c76d860
get_graph 49 55e8c09695a5dee10dbcc919b616c70717fe4f1ca9e00dc4f20112783592f823
get_graph 50 a8d90f7f224cec4a57592fa1317c8198662535391fc16f1ea4743904f2a52560
get_graph 51 68b0ef501c7267c13cef9697dfd6dc41e174cc8d981bfbf6f4e8e7a89029b67b
"$PY" -m scripts.audit_ltfp_graph_support --metrics-root "$DATA" \
  --callgraph "$RAW"/CallGraph_{48,49,50,51}.tar.gz \
  --period 162 191 --period 324 353 > "$BENCH/preflight/support-48-51.part"
mv "$BENCH/preflight/support-48-51.part" "$BENCH/preflight/support-48-51.json"
"$PY" -m json.tool "$BENCH/preflight/support-48-51.json"
```

Số đối chiếu đã đo từ 612.604.479 byte raw. Graph toàn bộ có **8.348 cạnh, 4.949 node**. Hai kỳ lần lượt có **5.690/182.943 (3,11%)** và **5.654/182.200 (3,10%)** service-phút có hàng xóm. Graph giới hạn ở cạnh từng có metrics hai đầu có 141 cạnh, 194 node; hai tử số là 5.551 và 5.509. Khi mất kết nối, chạy lại cùng khối để kiểm và dùng lại file đã tải. Với hai khoảng dựng graph đều thưa, bước tiếp theo là đếm sự kiện và negative windows có hàng xóm; tải thêm shard chỉ khi cần trả lời câu hỏi mới về coverage.

### P2 · Bộ test nền và smoke lịch sử — HIỆN CÓ

```bash
baseline_code="$BENCH/baseline-code"
if [[ ! -e "$baseline_code/.git" ]]; then git worktree add --detach "$baseline_code" "$BASE"; fi
test "$(git -C "$baseline_code" rev-parse HEAD)" = "$BASE"
(
  cd "$baseline_code"
  export PYTHONPATH="$baseline_code/src:$baseline_code" OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
  "$PY" -m pytest -q \
    tests/test_alibaba_training.py tests/test_alibaba_local_evaluation.py \
    tests/test_alibaba_live.py tests/test_demo_upgrade_wp2_risk.py \
    tests/test_demo_upgrade_wp0_checkers.py
)
```

PASS khi pytest exit 0, bộ ML thực sự chạy, không bỏ test do thiếu dependency. Kiểm seed/split, feature chỉ đọc quá khứ, label theo từng K, stream sai profile, lỗi nguồn và rule phát episode. Smoke train đã lưu dùng graph từ phút 60–71, fit 72–155, development 240–319, K=16 có 26/24 positive windows. Hai bản đều làm 84 bước và score 15.682 window. Smoke chạy trên node từng ghép được, dùng trọng số tần suất và một đầu ra. Chưa có bằng chứng parity trên telemetry thật hoặc hiệu quả so với XGBoost.

**Mở phiên làm việc sạch cho phần triển khai.** Giữ checkout hiện tại nếu có thay đổi đang làm. Từ checkout chủ có remote origin, dùng một thư mục bền trên ổ đĩa, một lần cho A. B dùng máy và thư mục riêng.

```bash
git fetch origin main
if [[ ! -e "$BENCH/code-a/.git" ]]; then
  git worktree add --detach "$BENCH/code-a" origin/main
fi
export CODE="$BENCH/code-a"
cd "$CODE"
export PYTHONPATH="$CODE/src:$CODE"
git merge-base --is-ancestor "$BASE" HEAD
git status --short --branch
```

Trước fit/capture chính thức, ghi commit thực thi sau khi code đã commit và working tree sạch. Không chuyển revision giữa chừng trong checkout đã đăng ký capture. Nếu cần mở terminal mới tại checkout riêng, đặt `SENTINELOPS_PY` trỏ tới cùng Python tuyệt đối trước P0. Giữ raw, outputs, checkpoints và releases ngoài checkout.


## 2. Ngày 2 — chốt đúng câu hỏi trước khi fit

### D2-A · Thiết kế LTFP và các ca kiểm

Ở phút `t`, mô hình xuất rủi ro theo **từng service** đang khỏe. Nhãn local bằng 1 nếu onset vi phạm SLO **độ trễ** nằm trong `(t, t+300s]`. Oracle độc lập dùng p95 của các request hoàn thành, ngưỡng `>750 ms` trong hai bucket liên tiếp đủ dữ liệu. Onset là bucket đầu của cặp đã xác nhận. Một bucket 5xx đơn thuần không phải onset độ trễ. Tách thời điểm bucket, lúc dữ liệu tới và lúc cảnh báo phát. Bộ chấm local hiện hành dùng SLO gộp cho gradual, chỉ tái sử dụng phần chiếu oracle và scoring phù hợp. [Health policy](../../configs/slo_health_policy.json) · [Evaluator hiện có](../../src/sentinelops/ml/alibaba_local_evaluation.py)

Bài LTFP dự báo xác suất lỗi trong một cửa sổ tương lai và có nhánh xếp hạng service chịu trách nhiệm. Bản SentinelOps dùng metrics, bộ mã hóa theo thời gian, attention trên graph và đầu ra risk theo service. Dùng tên **LTFP thích nghi cho SentinelOps** trong bảng. Ghi rõ việc bỏ log, nội dung trace, nhánh localization và graph tương quan; call graph chỉ cấp topology. [Bài LTFP, mục 4–5](https://www.mdpi.com/1999-4893/19/9/720)

| ID | Dữ liệu thử / thao tác | Kết quả bắt buộc | Nếu trượt |
| --- | --- | --- | --- |
| A01 | p95 tại t+240 và t+300 lần lượt 751, 800 ms, lịch sử khỏe; lặp với 750, 750 | Trường hợp đầu y=1, onset t+240; trường hợp sau y=0 khi đủ follow-up | Sửa phép so và quy ước bucket |
| A02 | Onset tại t, t+300, t+360; thêm 5xx cao nhưng p95 khỏe | Tại t loại khỏi tập pre-event; t+300 dương; t+360 ngoài horizon; riêng 5xx không tạo latency positive | Dùng oracle latency-only cho cả bốn dòng |
| A03 | Mất bucket xác nhận, thiếu tail, thiếu traffic, lag quá policy | UNKNOWN và mã lý do; không chuyển thành âm. Xóa suffix sau cutoff thì feature/prediction tại t giữ nguyên | Sửa mask/cutoff trước fit |
| A04 | Hai service khác split; cửa sổ có baseline/sequence/horizon chạm biên | Không có cạnh truyền message qua split; chỉ giữ anchor khi toàn bộ lịch sử/nhãn thuộc phần đã phân | Xuất ID vi phạm, sửa splitter và chạy lại |
| A05 | Giữ hướng caller→callee trong raw. Cho phép message hai chiều trên cạnh đã biết, thêm self-loop. Với thứ tự Checkout, Payment, Inventory | Mask kỳ vọng `[[1,1,1],[1,1,0],[1,0,1]]`; self-only bằng ma trận đơn vị. RDS thiếu telemetry đứng ngoài graph feature | Kiểm danh sách node, hướng và mask. Cách truyền hai chiều là đề xuất cần khóa trước fit |
| A06 | Ẩn Payment một phút; đổi thứ tự node; hoán đổi tương ứng cả features, mask, graph | Mất score cho target thiếu lịch sử, còn node đủ dữ liệu có thể chạy. Score sau hoán vị trùng theo ID, sai số ≤1e-6. Self-only không phụ thuộc giá trị của node khác | Sửa alignment/mask trước khi so |
| A07 | Phát lại cùng bucket của ba service từ telemetry `ald` bằng adapter offline và đường input của worker, với cùng `as_of`; lặp với một hàng đến muộn | Cùng raw refs, node IDs, mask, đơn vị, graph hash và feature sai số ≤1e-6; hàng đến muộn không xuất hiện ở cả hai | Fixture ba node chỉ xác nhận shape. Giữ D2-A BLOCKED nếu chưa có dữ liệu nguồn thật |
| A08 | Lập bảng hỗ trợ sau lọc lịch sử, mỗi split và mỗi khoảng pilot. Đếm distinct service, onset và số negative windows có hàng xóm; đếm cả service cô lập | Báo cả tử số/mẫu số toàn tập và cohort có cạnh. Đề xuất GO thí nghiệm khi cả hai khoảng có cạnh dùng được, fit/cal có positive và negative dùng được, calibration có ≥100 negative để phân giải budget 1%. Báo số group tạo ra positive | Thiếu một lớp hoặc toàn self-loop thì BLOCKED cho kết luận graph; ghi chi phí lấy thêm dữ liệu |
| A09 | Gọi inference trên service chưa thấy ở fit | Trọng số dùng chung, không phụ thuộc ID embedding của train. Topology local lấy từ manifest đã biết trước. Với service calibration cần topology ngoại sinh có trước cutoff; cấm suy ra cạnh từ nhãn hoặc tương lai | Nếu thiếu topology hợp lệ, báo coverage self-only và chặn claim có cạnh ở split đó |

Nhãn Alibaba dùng mean HTTP latency tương đối với baseline service; rule mặc định có K = 2, 4, 8, 16, 32, 64. Thử nghiệm chính phải ghi cách condition theo K hoặc khoảng cách baseline/SLO, để biết đưa ngưỡng local 750 ms vào model thế nào. K=16 là mốc smoke và mức `degrading`. Mean là input được phép nếu qua parity; oracle local vẫn dùng p95. Mỗi service Alibaba vào đúng bucket `sha256(msname) mod 10`: 0–5 train, 6–7 calibration, 8–9 evaluation. Power/time split nằm trong manifest, có purge cả baseline tối đa 60 phút, chuỗi 5 bước, feature 5 phút, horizon và confirmation tail. Kiểm theo **min/max timestamp thực sự dùng** cho mỗi window. Giữ `ald` ở development; local chỉ đánh giá. [Cấu hình chính xác](../../configs/alibaba_relative_features.json) · [Topology local](../../configs/topology.yaml)

### D2-B · Tái hiện ADSketch và giao diện detector

Bản đã đối chiếu của tác giả là `f7bd642c93ff25612514ed711f47cc095a6a66fd`, giấy phép GPLv3. Repo SentinelOps dùng MIT. Chạy đối chứng trong môi trường riêng; trước khi phân phối source/image có mã tác giả, B ghi rõ cách đáp ứng giấy phép hoặc dùng bản triển khai độc lập từ bài báo đã kiểm parity. README tác giả dùng Python 3.6, requirements thiếu pin và module còn import `tqdm`. Đây là các phụ thuộc của môi trường tham chiếu. [Mã đã pin](https://github.com/OpsPAI/ADSketch/tree/f7bd642c93ff25612514ed711f47cc095a6a66fd) · [Bài ADSketch](https://doi.org/10.1145/3510003.3510085)

```bash
export REF="$BENCH/reference/ADSketch"
mkdir -p "$BENCH/reference"
if [[ ! -d "$REF/.git" ]]; then git clone https://github.com/OpsPAI/ADSketch.git "$REF"; fi
git -C "$REF" checkout --detach f7bd642c93ff25612514ed711f47cc095a6a66fd
test "$(git -C "$REF" rev-parse HEAD)" = f7bd642c93ff25612514ed711f47cc095a6a66fd
head -3 "$REF/LICENSE"
# Dùng môi trường nghiên cứu riêng trên máy B; không cài gói vào môi trường phục vụ.
"$PY" -m venv "$BENCH/reference/env"
BPY="$BENCH/reference/env/bin/python"
"$BPY" -m pip install -r "$REF/requirements.txt" tqdm
"$BPY" -m pip freeze > "$BENCH/reference/deps.txt"
(cd "$REF/data" && "$BPY" -m zipfile -e yahoo.zip .)
(cd "$REF" && MPLBACKEND=Agg NUMBA_NUM_THREADS=2 "$BPY" yahoo_demo.py)
tail -5 "$REF/logs/yahoo_demo.log"
```

Kỳ vọng tác giả ghi precision, recall, F1 hữu hạn ở log; ghi Python, bản thư viện và digest output. Nếu môi trường 3.11 của B gặp lỗi tương thích, tái hiện bằng môi trường Python theo README với lock đã kiểm, ghi lỗi/cách sửa vào receipt. Điểm Yahoo chỉ là test tái hiện. Hàm offline của tác giả tính percentile trên toàn chuỗi thứ hai, dùng cả chuỗi đó để tạo cluster. Khi học pattern, **cả hai chuỗi đều lấy từ Alibaba train**, một phần làm normal reference và phần khác làm discovery. Chấm tại t chỉ đọc pattern đã freeze và dữ liệu tới t. Tách hàng kết quả nếu bật cập nhật online. [Hàm offline ở revision đã pin](https://github.com/OpsPAI/ADSketch/blob/f7bd642c93ff25612514ed711f47cc095a6a66fd/adsketch/motif_operations.py)

| ID | Test bắt buộc của B | PASS |
| --- | --- | --- |
| B01 | Cố định seed và input nhỏ, đối so implementation với reference | Cùng subsequence anomalous và gán pattern sau khi chuẩn hóa tên cluster; lưu input/output/digest |
| B02 | Kiểm ID segment, split, scaler và thời gian học pattern | Tất cả source ID ở Alibaba train, không nối segment qua gap/service/split; có normal và anomalous pattern dùng được, báo số segment mỗi loại |
| B03 | Thay toàn bộ suffix sau t và tráo nhãn test | Prediction tại và trước t giữ nguyên, digest thư viện pattern giữ nguyên; detector có đường xử lý novel pattern |
| B04 | Cho hai alert trùng cùng một onset, thêm 5xx đơn thuần | Một match TP, alert dư bị tính FP; phần latency không nhận TP nhờ 5xx |
| B05 | Mất bucket, quá ít lịch sử, model hỏng | Unavailable có lý do, không phát alert giả; IF/MAD cũng chạy cùng timeline |
| B06 | Một run có mẫu đã học, một run có mẫu mới | Báo riêng known/novel khi đủ support. Giữ phép so chính với thư viện frozen |

**Bàn giao ngày 2.** A/B ghi riêng `GO` hoặc `BLOCKED`, SHA code/config, dataset và split, số liệu support, bằng chứng từng test, môi trường và người xử lý bước kế tiếp. Trước khi xem holdout, khóa cohort, cutoff, budget, matching, clock, seed, số nhóm và quy tắc ở mục 5. Cả hai khoảng graph của A đã đo. Trên một bucket của run development `ald-01-healthy`, đưa raw HTTP thật qua hàm chiếu input của worker với chuỗi batch dựng lại: sáu feature trên mỗi service Checkout/Payment/Inventory trùng với bản ghi offline, sai số tối đa 0; ngay trước khi batch sẵn sàng, kết quả unavailable. A07 vẫn cần kiểm graph/mask và worker đủ luồng. A08 còn thiếu support theo nhãn ở fit/calibration. B chưa giao kết quả tái hiện ở checkout này.

## 3. Ngày 3–4 — đặc tả phần phải xây và đóng băng ứng viên

**CẦN TRIỂN KHAI.** Tại SHA nền `3fc21c9` chưa có trainer/evaluator cuối cho LTFP và ADSketch, cũng chưa có capture holdout với gradual dưới SLO. Các lệnh `bm` bên dưới là **giao diện nghiệm thu đề xuất** để người vận hành gõ y nguyên sau bàn giao. A sở hữu launcher mỏng và contract chung; B cung cấp adapter detector. Dùng lại transform/evaluator/campaign/worker hiện có. Bắt đầu bằng test: bảng A01–A09/B01–B06 là input và kết quả kỳ vọng, phải chạy được cả nhánh lỗi. Không ghi PASS chỉ vì CLI tồn tại.

```bash
cd "$CODE"
bm() { "$PY" -m scripts.model_benchmark --root "$BENCH" --protocol "$CODE/configs/model_benchmark_v1.json" "$@"; }
if [[ -f scripts/model_benchmark.py && -f configs/model_benchmark_v1.json ]]; then
  bm --help
else
  printf '%s\n' 'BLOCKED: A cần giao launcher và protocol mới trước lệnh bm.'
fi
```

| Hạng mục | Chủ trì / đầu vào | Output dưới `$BENCH` | Khi nào qua |
| --- | --- | --- | --- |
| Protocol | A+B; baseline, graph, oracle, ma trận capture | `freeze/protocol.json` và SHA | Ghim code, data/split IDs, cutoff, feature order, K, clocks, scope, test IDs và seeds. Chạy lại trên cùng input cho cùng nội dung |
| F1 · graph vs self-only | A; train đã duyệt | `fit/a/`, hai weights, config, lịch sử loss và checkpoint | Cùng rows, temporal encoder, seed 42, optimizer, số bước, budget. Chỉ thay adjacency. Loss/score hữu hạn. Có thứ tự node/feature và xử lý unavailable |
| F2 · XGBoost và slope | A; cùng protocol | Baseline identities trong `fit/a/` | Dùng release cũ chỉ khi split/nhãn/input tương thích; lệch thì refit XGB trên cùng train. Giữ slope cố định 0,05 cho phép so Q-07. Ghi rõ baseline đã deploy nếu báo thêm |
| F3 · ADSketch, IF, MAD | B; cùng split | `fit/b/`, frozen patterns, config và model IDs | Train/cal chỉ dùng Alibaba; lưu counts từng pattern, chuẩn hóa/scale, window length, threshold, persistence, fit time |
| F4 · hiệu chuẩn | Mỗi lane trên calibration | `freeze/candidates.json`, điểm vận hành từng model | Với score càng lớn càng rủi ro và rule `score >= threshold`, chọn ngưỡng thấp nhất trong các score calibration thỏa ≤1% flag trên negative windows; giữ nguyên các điểm bằng nhau. Nếu chỉ ngưỡng nằm trên max mới đạt, ghi 0 cảnh báo và độ nhạy 0. Với detector chỉ xuất nhị phân, báo điểm vận hành thực. Giữ hàng slope cố định riêng |
| F5 · phục hồi | A+B chạy lại cùng checkpoint | Kết quả test tại `checks/` | Liên tục so với dừng/resume trên cùng CPU, seed và input: cùng số bước, ID và score lệch ≤1e-6. Nếu trainer chưa hỗ trợ resume thì chỉ dừng giữa các fit đã hoàn tất |

```bash
# Sau khi A/B giao interface và bộ test này tại một clean commit.
"$PY" -m pytest -q tests/test_model_benchmark_protocol.py tests/test_model_benchmark_forecast.py
"$PY" -m pytest -q tests/test_model_benchmark_detector.py tests/test_model_benchmark_reports.py
bm audit --lane a
bm audit --lane b
bm freeze-protocol
bm fit --lane a
bm fit --lane b
bm calibrate --lane a
bm calibrate --lane b
bm freeze-candidates
sha256sum "$BENCH/freeze/protocol.json" "$BENCH/freeze/candidates.json"
```

A và B có thể chạy `fit`/`calibrate` trên hai máy. B giao nguyên output `fit/b`, bộ calibration, receipt, environment và SHA cho A; launcher xác minh checksum trước khi freeze. Một lane bị chặn vẫn lưu được kết quả của lane kia. Trong `candidates.json` ghi trạng thái từng lane và release đối chứng được chọn. Cấm đánh dấu một candidate chưa fit là sẵn sàng. Huấn luyện LTFP CPU dùng cùng seed, batch, dtype, optimizer, early-stopping rule và số lần thử của hai biến thể. Máy A đã chạy được PyTorch CPU 2.7.1; ghi phiên bản thực tế kể cả sympy. Tách thời gian chuẩn bị graph, fit, score, p95 inference và bucket-ready đến emission. Quy tắc episode forecast chính phải giống worker `watch/degrading` rồi `degrading` ở phút liên tiếp. Nếu báo persistence kiểu evaluator cũ, để ở hàng riêng.

## 4. Ngày 5–6 — thu holdout một lần và chấm hai bảng

**CẦN TRIỂN KHAI.** `configs/alibaba_local_evaluation.json` hiện chỉ cho `development-validation` và cấm sealed-final. Đổi prefix `ald` hoặc sửa cờ file cũ không tạo được holdout hợp lệ. A thêm protocol/revision được duyệt, oracle p95-only, hai recipe gradual thấp, unique group IDs và chống trộn dữ liệu development. Runner phải từ chối final trước freeze. Dùng một lần capture cho cả hai lane. [Campaign hiện có](../../scripts/capture_local_campaign.py) · [Giới hạn của epoch cũ](../../configs/alibaba_local_evaluation.json)

| Mỗi nhóm mới | Số lượt | Recipe đề xuất để khóa ngày 2 | Oracle / mẫu số |
| --- | ---: | --- | --- |
| Healthy | 1 | Không bơm lỗi, active 900 s | Healthy service-hours và false warnings |
| Abrupt Payment, Inventory | 2 | Active 600 s, ordinal lẻ latency, chẵn 5xx theo recipe hiện có | Tách onset latency, sự kiện 5xx và service bị ảnh hưởng |
| Gradual cao Payment, Inventory | 2 | 1 RPS, active 1.200 s, ramp tới 1.200 ms | Oracle xác định breach thực và onset từng service |
| Gradual thấp Payment, Inventory | 2 | Cùng thời lượng/tải, ramp thử 400 ms trên **development** trước freeze | Oracle xác định no-breach thực; giữ cả lượt bất ngờ breach |

Đề xuất **5 nhóm × 7 lượt = 35 lượt**. Giữ warmup 1.980 s, recovery 360 s mỗi lượt. Với overhead dự trù 180 s/lượt như campaign hiện hành, mỗi nhóm khoảng **6 giờ 49 phút**, cả đợt **34 giờ 05 phút**. Đây là dự toán; epoch trước 25 lượt mất gần 23 giờ. Đặt riêng slot hai ngày hoặc năm slot mỗi slot khoảng 7 giờ có dự phòng. Chạy thử recipe thấp trên development trước khi khóa. Nếu holdout thực tế thiếu no-breach, báo thiếu mẫu, giữ nguyên toàn bộ run và không tuyển bù theo score. Seed/ID, host/context, tách volume, giới hạn thay thế do hỏng nguồn phải ghi trước. Cùng máy và cùng nhóm có thể tạo phụ thuộc thống kê.

```bash
bm capture-preflight
for group in 01 02 03 04 05; do
  bm capture --group "$group"
  bm verify-group --group "$group"
done
bm seal-capture
bm score --lane a
bm score --lane b
bm report
```

Để nghỉ, thay vòng lặp bằng `bm capture --group 01` rồi `bm verify-group --group 01`. Mở phiên sau chạy group kế tiếp, dùng cùng root và freeze SHA. Receipt `capture/01/receipt.json` phải ghi đủ 7 slot, từng run ID, raw refs/SHA, oracle, profile, clock, completeness và terminal status. Retry đã có receipt phải kiểm identity và trả cùng receipt. Nếu mất điện giữa group, giữ raw và receipt dở, đánh dấu interrupted; chỉ thay thế theo protocol đã khóa. `systemd --user`, tmux hoặc giữ terminal chỉ giúp khi máy vẫn bật. Máy sleep/tắt không thể tiếp tục thu telemetry.

| ID | Ca kiểm ngày 5–6 | Qua khi |
| --- | --- | --- |
| H01 | Chạy score trước freeze; đưa vào một group `ald` hoặc trùng run | Từ chối trước khi đọc label cuối |
| H02 | Trùng khóa, thiếu score, UNKNOWN, model mất graph | Không âm thầm bỏ hàng. Ghi availability và reason; coverage toàn bộ, paired intersection và mẫu số oracle độc lập đều tái tạo được |
| H03 | Cùng episode/cùng event bị gửi lại | Match một-một theo service/run và thời gian. Forecast trong [onset−300 s,onset); detector từ onset đến onset+300 s. Match alert phù hợp sớm nhất, dư tính unmatched. Lead=60 đạt, 59 giây là late |
| H04 | Một event chỉ có ở service mà model bỏ score | Giữ event trong mẫu số recall toàn tập, ghi miss/unavailable; so sánh trên giao các window báo riêng |
| H05 | Group không có positive hoặc không có no-breach | AP thiếu một lớp trả NA; mẫu số 0 trả NA kèm lý do. Không dùng 0 hoặc 100% thay thế |
| H06 | Chạy report hai lần, đảo thứ tự row; một model thiếu cả một group | Cùng số liệu cho cùng input; thiếu group hiện ra hoặc dừng đối soát. Bootstrap ghép cùng group cho tất cả model |

**Định dạng trao dữ liệu.** Tệp `predictions.parquet` có một hàng cho mỗi `dataset, group_id, run_id, service, bucket_end, model_id`. Thêm `available_at, decision_at, clock, eligible, reason, y, score, state, episode_id, graph_neighbor_count`; ghi đơn vị UTC/giây và SHA manifest. Tệp `events.parquet` dùng oracle độc lập gồm `event_id, group_id, run_id, service, onset_at, confirmed_at, kind, eligible, reason`. `matches.parquet` ghi model, event, alert, thời gian emission, matched/miss/late/unmatched. Mỗi report kèm `manifest.json` liệt kê đường dẫn, SHA-256, số dòng, số group/run và dataset. Không trộn label Alibaba và label local.

## 5. Ngày 7–9 — đọc số, quyết định và nghiệm thu demo

Bảng A có bốn hàng **slope, XGBoost, LTFP self-only, LTFP graph**. Bảng B có ba hàng **Median/MAD, IF, ADSketch frozen**. Báo riêng Alibaba proxy và local latency, group/service, số có/thiếu score và nguyên nhân. Cả hai bảng phải mở được từ `report/forecast.csv` và `report/detector.csv`.

| Bảng | Cột bắt buộc, đơn vị và cách tính | Quyết định |
| --- | --- | --- |
| A · xếp hạng | AP bằng average precision, prevalence = pos/labeled; so cùng tập window. Ghi PR-AUC trapezoid riêng nếu tính | AP của ứng viên phải cao hơn prevalence và slope cố định trên gradual heldout cho phạm vi Q-07 |
| A · cảnh báo sớm | Sự kiện gradual có lead ≥60 s / tất cả eligible gradual breach; ≥180 s / cùng mẫu số; lead p10/p50 của ca có cảnh báo, cùng miss/late; no-breach warned runs / no-breach runs; false warnings / eligible healthy service-hour | Q-07 cần recall ≥0,70 tại lead ≥60 s. Muốn đề nghị thay XGB còn phải cải thiện recall hoặc lead ở cùng ngân sách cảnh báo sai. Khóa chỉ tiêu dùng quyết định trước holdout |
| A · tác dụng graph | Δ recall graph−self, graph−XGB; hỗ trợ theo có/không hàng xóm, số node cô lập; coverage toàn tập | Chỉ quy cải thiện cho cạnh khi phép ablation trùng các điều kiện còn lại. Báo trung thực giới hạn độ phủ khoảng 3,45% đã thấy trong pilot |
| B · phát hiện | TP/FP/FN sự kiện, precision, recall, F1, FP / eligible healthy service-hour, MTTD p50/p90 từ onset đến emission, miss, p95 inference | Phạm vi Q-04: recall ≥0,85, precision ≥0,80, ≤1 false incident/service-hour; Q-05 đối chiếu IF/MAD; Q-06 p90 ≤300 s cần clock arrival-aware/live. S1/S3 chỉ kết luận cho S1/S3 |
| Cả hai · độ chắc chắn | Δ theo cặp, 1.000 bootstrap, seed 42, 95% percentile CI; lấy lại **nguyên group** local hoặc group service Alibaba. Báo số replicate xác định, số group và denominator. Replay và live có cột clock riêng | CI chứa 0 hoặc dữ liệu thiếu cho câu hỏi chính: ghi INCONCLUSIVE cho cải thiện. Trượt gate: FAIL. Baseline đạt gate vẫn được giữ làm release demo |

**Ví dụ đối soát bằng tay.** Ba event có lead 180 s, 59 s và không có cảnh báo. Recall ≥60 s = 1/3, ≥180 s = 1/3, late=1, miss=1. Hai lượt gradual không breach, một lượt phát hai cảnh báo thì `warned_runs=1/2`. Detector có TP=3, FP=1, FN=2 thì precision=0,75, recall=0,60, F1=2/3. Trước bàn giao, test H03 và report phải tái tạo chính xác các phân số này. Một detector phát hiện sớm nhưng bỏ hết sự kiện khó vẫn phải báo miss.

**Quy tắc chọn đề xuất trước freeze.** Ưu tiên Δ recall ≥60 s. Chỉ dùng lead làm tiêu chí phụ khi recall của ứng viên không thấp hơn baseline; so lead theo cặp trên cùng sự kiện cả hai đã cảnh báo, công bố số cặp và miss của từng bên. Muốn ghi “tốt hơn”, cận dưới CI 95% của chỉ tiêu đã khóa phải lớn hơn 0, cùng budget calibration, đồng thời báo số false warnings/giờ khỏe và số lượt gradual không breach bị cảnh báo trên holdout. Nếu ứng viên mua thêm lead bằng nhiều cảnh báo sai, ghi rõ đánh đổi và giữ baseline cho demo trừ khi có ADR chấp nhận đánh đổi đó.

```bash
bm verify-report
for f in forecast.csv detector.csv manifest.json; do test -s "$BENCH/report/$f"; done
head -5 "$BENCH/report/forecast.csv"
head -4 "$BENCH/report/detector.csv"
"$PY" -m pytest -q tests/test_demo_upgrade_wp0_checkers.py tests/test_demo_upgrade_wp1_approval.py \
  tests/test_demo_upgrade_wp2_risk.py tests/test_demo_upgrade_wp3_notify.py
node --test tests/ui/demo_upgrade_wp4.test.mjs
```

**ADR từng lane.** Ghi `KEEP/REPLACE/INCONCLUSIVE`, model/bundle/data/code/image digest, phạm vi, số tuyệt đối và CI, runtime budget, artifact trước/sau, cách rollback và người quyết định. Registry hiện chỉ load bundle Alibaba v3 đã xác nhận. Nếu model mới thắng benchmark, A phải giao loader kiểm digest/schema, worker có history/graph, test bundle lỗi và parity replay/runtime trước khi đổi release. Trượt parity thì giữ baseline cho demo và ghi chiến thắng offline đúng phạm vi. B kiểm pinned release trong image và đường đến API/UI/Grafana. [Model pipeline](../runbooks/MODEL_PIPELINE.md)

**Gate demo local.** B chạy rehearsal ở môi trường local đã cấu hình quyền, thu receipt từ luồng thật. Kiểm healthy 60 phút có **score khả dụng** cho từng service, UI/Grafana hiển thị warning, lead Payment ≥180 s, `0 < deploy_to_breach ≤600 s`, RCA top-1 Payment, pending đến khi on-call duyệt, đúng một dispatch và recovery từ telemetry. Đối chiếu thông báo early warning, proposal, outcome theo đúng episode/proposal. Loại receipt giả/khác release. Nếu runtime chưa có model được chọn hoặc không có môi trường local thật, gate này là NOT_RUN. [Đặc tả rehearsal, input và receipt](DEMO_UPGRADE_SPEC.md) · [Runner và kiểm tra live](../../scripts/aws_demo/REBUILD.md)

```bash
# Đặt LOCAL_PROMETHEUS và LOCAL_ROUNDS bằng endpoint và thư mục của chính lần rehearsal.
: "${LOCAL_PROMETHEUS:?cần endpoint Prometheus của môi trường local}"
: "${LOCAL_ROUNDS:?cần thư mục receipt của cùng runner invocation}"
"$PY" -m scripts.check_risk_quiet --prometheus "$LOCAL_PROMETHEUS" --minutes 60
"$PY" -m scripts.check_demo_round "$LOCAL_ROUNDS"/round-{1,2,3}.json --require 3
```

Đối chiếu ba receipt với đúng SHA/digest. Checker phải exit 0 và báo `RESULT: PASS` cho ba round cùng invocation. Quy trình AWS L2/L3 có nghiệm thu live riêng sau khi owner mở môi trường. Nếu chỉ checker fixture hoặc campaign development đã chạy thì giữ nguyên trạng thái gate live.

**Bàn giao Git/CI.** Review diff, chỉ stage đúng file spec/code/test/ADR đã review. Kiểm danh tính, fetch remote trước push fast-forward theo workflow dự án. Chạy trên checkout chứa commit đã gửi:

```bash
sha="$(git rev-parse HEAD)"
test "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "$sha"
run="$(gh run list --workflow ci.yml --commit "$sha" --limit 1 --json databaseId --jq '.[0].databaseId')"
test -n "$run"
gh run watch "$run" --exit-status
gh run view "$run" --json headSha,conclusion,url
```

**Hoàn tất** khi reviewer chạy lại report ra cùng số, đối soát được ví dụ và dữ liệu thật, có quyết định cho từng lane, release đã chọn qua demo local và CI đúng commit báo success. Nếu CI chưa xuất hiện, tra lại theo đúng SHA. Ghi rõ gate còn thiếu, người gỡ và dữ liệu cần lấy; không chọn lại threshold hoặc recipe sau khi đã xem holdout.
