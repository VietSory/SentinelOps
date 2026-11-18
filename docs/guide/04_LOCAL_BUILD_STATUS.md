# SentinelOps — kiểm thử build local

Ngày: 2026-11-18 (Asia/Bangkok). Người thực hiện: `build`.
Checkout: `08844452d0b37c603e0c481c9119e16264edba03`.

**Kết quả cuối: PASS phần mềm local** — Python **2225/2225 pass**, UI JavaScript **25/25 pass**, không skipped; contracts **53/53**, Compose **4/4**, installed wheel smoke PASS. Không thấy lỗi code còn tái hiện sau khi sửa ownership môi trường tạm. Còn **1 warning** dependency. Đây không phải nghiệm thu full-stack/live hoặc G4–G8; backlog và các phép kiểm chưa chạy được liệt kê rõ bên dưới.

## Phạm vi và bảo toàn repo

- Đã đọc `README.md`, `Makefile`, `pyproject.toml`, `setup.py`, `package.json`, bốn `docker-compose*.yml`, các Dockerfile và `.github/workflows/ci.yml`.
- Đối chiếu backlog với `docs/PROJECT_STATUS.md`, `docs/TEAM_HANDOFF.md`, `docs/delivery/DELIVERY_PLAN.md`, `docs/delivery/LANE_B_CHECKPOINT.md`, `docs/delivery/AWS_DEMO_PLAN.md`, `docs/architecture/CONSTRAINTS_AND_GATES.md` và runbook liên quan.
- Không sửa source/config/test/docs của repo, không commit/push, không spawn agent. File báo cáo này là output duy nhất do lượt kiểm tra tạo trong repo. Dependency, cache và build/test artifacts được đặt trong bản sao tạm ở container.
- Trước khi chạy đã có `package.json` bị sửa, `node_modules/`, `package-lock.json` và `.herdr/` chưa được Git theo dõi. Giữ nguyên các thay đổi có sẵn; test UI chạy với `package.json` hiện tại.

## Môi trường và cách chạy

- Windows PowerShell; Node `v26.8.1` (repo yêu cầu >=20).
- Shell sandbox không thấy `rg`, Python, Make, Docker hoặc `hc`. Dùng `git ls-files`, `git grep`, `Get-Content`, `Select-String` thay `rg`.
- Kiểm tra ngoài sandbox thấy Python Windows `3.15.0`, Python WSL Ubuntu `3.14.4`: đều ngoài `requires-python = ">=3.11,<3.13"`. Không dùng chúng để kết luận suite Python.
- WSL Ubuntu có Docker Engine `29.9.2`, Compose `v5.6.1`. Các container có sẵn `tixflow-postgres-1`, `tixflow-redis-1` không bị thao tác.
- Môi trường test: image `python:3.11-slim-bookworm`, digest `sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39`, Python `3.11.18`; container riêng `sentinelops-local-build-20261118`.
- Repo gốc được bind-mount **read-only** tại `/source`; sao chép vào `/tmp/sentinelops` (loại `node_modules/`, `.herdr/`) trước khi cài/chạy. Venv: `/tmp/sentinelops-venv`. Cài Git/Make trong container để giữ môi trường gần CI Linux và hỗ trợ các test identity/packaging.
- Không khởi động full stack, không gọi AWS, không chạy training/capture/holdout hoặc bật action policy.

## Lệnh và kết quả

| Lệnh | Kết quả |
| --- | --- |
| `git status --short`; `git rev-parse HEAD`; `git diff -- package.json` | Đã ghi nhận SHA và thay đổi có sẵn như trên. |
| `node --version`; `npm.cmd run test:ui` | **PASS: 25 tests, 0 fail, 0 skipped, 0 cancelled, 0 TODO**; 215,6427 ms theo Node runner. |
| `python --version`; `py --list-paths`; `wsl.exe -d Ubuntu -- sh -lc 'python3 --version'` | Phát hiện Python 3.15/3.14 không tương thích; chuyển sang container 3.11. |
| `wsl.exe --list --quiet` trong sandbox | **BLOCKED môi trường:** `Wsl/E_ACCESSDENIED`. Chạy lại với quyền được duyệt thành công. |
| `docker version`; `docker compose version`; `docker image ls`; `docker ps` qua WSL | **PASS:** daemon hoạt động, kiểm kê tài nguyên sẵn có; không dừng/sửa stack khác. |
| `docker compose -f docker-compose.yml config --quiet` | **PASS**, cấu hình base (8 service). |
| `docker compose -f docker-compose.control.yml config --quiet` | **PASS**, control PostgreSQL riêng. |
| `docker compose -f docker-compose.yml -f docker-compose.control.yml -f docker-compose.b.yml config --quiet` | **PASS**, tổ hợp lane B (9 service), hỗ trợ tag `!override`. |
| `docker compose -f docker-compose.yml -f docker-compose.s4.yml config --quiet` | **PASS**, tổ hợp S4. Tổng **4/4 cấu hình**, không đồng nghĩa service/runtime đã ready. |
| `python -m venv /tmp/sentinelops-venv`; `. /tmp/sentinelops-venv/bin/activate` | **PASS**, Python 3.11.18 trong container. |
| `python -m pip install --disable-pip-version-check -e '.[dev,ml]'` | **PASS**, editable `sentinelops==0.2.1`, `pytest==8.3.4` và các dependency dev/ML pin trong pyproject đã cài. |
| `python -m pip check` | **PASS:** `No broken requirements found.` |
| `python scripts/validate_contracts.py` | **PASS: 34 schemas + 16 samples + 3 configurations** (53 đối tượng), không lỗi. |
| `make test` → `pytest -q`, lần 1 | **FAIL: 2215 passed, 10 failed, 0 skipped, 1 warning**, 159,96 s. Cả 10 failure ở `tests/test_service_registry_single_source.py`; Git chặn bản sao vì ownership (chi tiết bên dưới). `make` báo Error 1, container thoát 1. |
| Chuẩn bị rerun dùng biến shell qua WSL | **FAIL môi trường:** `SCRATCH_WORKSPACE=` rỗng, `mkdir: Permission denied`; đổi sang đường dẫn `/tmp/...` cố định. Không sửa repo gốc. |
| Rerun trên bản sao tạm sau `chown -R 0:0 /tmp/sentinelops`; `make test` | **PASS: 2225 passed, 0 failed, 0 skipped, 1 warning**, 151,02 s; container thoát **0**. Không đổi test/assertion/source, không thêm skip. |
| `git rev-parse HEAD`; `git rev-parse --is-shallow-repository` trên bản sao sau sửa ownership | **PASS:** đúng SHA `08844452…`, `false` (history đầy đủ). |
| `python -m pip install --disable-pip-version-check setuptools==75.8.0 wheel==0.45.1` | **PASS:** build tools đúng pin `[build-system]` trong venv bản sao. |
| `python scripts/check_installed_package.py` | **PASS:** `INSTALLED WHEEL SMOKE TEST: PASS`; build `sentinelops-0.2.1-py3-none-any.whl` (382781 bytes), cài vào site tạm và chạy Python `-I` kiểm embedded schemas, API/UI/assets, no-model/auth/fallback. Lần 1 chưa chạy vì `set -e` dừng sau suite fail. |
| So SHA-256 nội dung 539 tracked files trước/sau kiểm thử; `git status --short` | **PASS: 539/539 không đổi**, không file bị xóa. Trạng thái tracked/untracked vẫn như ban đầu; `.herdr/out/build.md` nằm trong thư mục output chưa tracked. Các output khác trong `.herdr/out/` do tác vụ khác tạo, không được lượt này chỉnh sửa. |
| Tìm `ruff/flake8/pylint/black/mypy/lint` trong pyproject/Makefile/package.json/CI | Không có lint target/config/job được khai báo: **N/A**, không tự thêm linter hoặc sửa format. |
| Rà `TODO/FIXME/NotImplemented(Error)/TBD/XXX/[ ]` trong 294 tracked files thuộc `src/`, `services/`, `scripts/`, `docs/` | **0 marker**. Các `pass` đã xem là exception classes/handling, không đủ căn cứ gọi là implementation thiếu. Backlog thực nằm trong delivery docs bên dưới. |

Lệnh dựng môi trường thực thi qua `wsl.exe -d Ubuntu -- sh -lc <script>`; nội dung chính:

```sh
docker run --name sentinelops-local-build-20261118 \
  --mount type=bind,source=/mnt/d/year4/AI4life/SentinelOps,target=/source,readonly \
  -e PYTHONDONTWRITEBYTECODE=1 python:3.11-slim-bookworm sh -lc '
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends git make >/tmp/apt.log
mkdir -p /tmp/sentinelops
cd /source
tar --exclude=./node_modules --exclude=./.herdr -cf - . | tar -xf - -C /tmp/sentinelops
cd /tmp/sentinelops
python --version
python -m venv /tmp/sentinelops-venv
. /tmp/sentinelops-venv/bin/activate
python -m pip install --disable-pip-version-check -e ".[dev,ml]"
python -m pip check
python scripts/validate_contracts.py
make test
python scripts/check_installed_package.py
'
```

Lần 2 dùng `docker cp` lấy `/tmp/sentinelops/.` và `/tmp/sentinelops-venv/.` từ container lần 1 sang `/tmp/sentinelops-local-build-20261118-recheck/{repo,venv}` trong WSL. Container `sentinelops-local-build-rerun-20261118` dùng cùng image, mount hai thư mục tạm về đúng paths `/tmp/sentinelops` và `/tmp/sentinelops-venv`. Lệnh bên trong lần 2:

```sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends git make >/tmp/apt.log
chown -R 0:0 /tmp/sentinelops
cd /tmp/sentinelops
. /tmp/sentinelops-venv/bin/activate
git rev-parse HEAD
git rev-parse --is-shallow-repository
python -m pip install --disable-pip-version-check setuptools==75.8.0 wheel==0.45.1
python scripts/check_installed_package.py
make test
```

## Lỗi, giới hạn và các phép kiểm chưa chạy

- Các lỗi PATH/permission ban đầu là giới hạn shell sandbox, đã xử lý bằng WSL/Docker được duyệt; không phải lỗi source.
- Lần 1: Git báo `fatal: detected dubious ownership in repository at '/tmp/sentinelops/.git'`, `git clone --shared --no-checkout` thoát 128 trên bản sao giữ UID từ mount Windows. Sáu case đầu thuộc kiểm registry/import closure và bốn biến thể identity; bốn case còn lại là telemetry-pipeline edits. Thông báo `ADR-017 identity needs full git history` ở case đầu chưa chứng minh checkout thiếu history: Git đang từ chối đọc repo. Rerun sửa ownership **chỉ bản sao tạm**, không bỏ assertion hoặc thêm skip.
- Danh sách 10 case fail lần 1:
  - `test_repository_registry_matches_git_and_current_system`
  - `test_scoring_code_closure_follows_imports_into_feature_generation`
  - `test_identity_procedure_detects_each_measurement_input[feature-generation|risk-threshold|workload-delay|slo-policy]` (4 case)
  - `test_identity_records_the_telemetry_pipeline_and_detects_a_collector_edit[observability/otel-collector-config.yaml|observability/prometheus.yml|docker-compose.yml|observability/rules/sentinelops-workers.yml]` (4 case)
- Warning lần 1: Starlette testclient dùng alias `anyio.abc.BlockingPortal` đã deprecated; nên chuyển sang `anyio.from_thread.BlockingPortal` khi nâng dependency tương thích. Đây là cảnh báo dependency, không phải failure của logic SentinelOps.
- Lần 2: **cả 10 case trên đều pass**, toàn suite 2225 pass; chỉ warning deprecation trên còn xuất hiện. Xác nhận lỗi lần 1 do quyền sở hữu bản sao tạm, không phải thiếu full Git history hay regression source.
- Chưa đo runtime full stack: commerce/control PostgreSQL thật, durability/restart/concurrency, live workers, source outages, actual Kubernetes rollback/verify và host systemd recovery. Các CI job này độc lập với `pytest -q`.
- Chưa chạy browser Playwright/Chromium. Node tests kiểm client/workspace; không thay walkthrough browser hoặc incident thật.
- Chưa chạy Terraform 1.14 `init/validate/fmt/test`, collector binary validation, Prometheus `promtool`, Grafana runtime queries hoặc kubeconform 1.34. Các unit test liên quan không thay kiểm binary/live tương ứng.
- Không tải public production/RCAEval corpus hoặc model bundle ngoài Git. Không đánh giá số liệu model/RCA, source parity, G3–G8 bằng số test pass.

## DANH SÁCH VIỆC CÒN CẦN BUILD / HOÀN THIỆN

Các mục sau là backlog có nguồn, không phải lỗi pytest tự suy ra. Phân biệt code đã có nhưng thiếu runtime/acceptance với capability chưa cung cấp. Chỉ phản ánh tài liệu/code local tại SHA nêu trên; không xác minh trạng thái PR/AWS/host từ xa.

| Ưu tiên | Việc còn thiếu | Điều kiện hoàn tất / nguồn |
| --- | --- | --- |
| P0 | Có môi trường Python hỗ trợ cho lần chạy local tiếp theo | Dùng 3.11/3.12 + venv hoặc container; Windows 3.15 và WSL 3.14 hiện không dùng trực tiếp được. `README.md`, `pyproject.toml`. |
| P0 | Khôi phục đường telemetry tươi → worker → protected API/UI trên host bàn giao | Checkpoint 2026-11-16 ghi consumer đã resume nhưng stream vẫn dừng 2026-11-14; health worker chưa chạy, gap 47 giờ UNKNOWN; protected API/UI rollout vẫn mở. Cần quyết định owner trước ghi bucket mới, kiểm freshness/readiness/DB history trên đúng host. Không coi các mốc inactive cũ là trạng thái host hôm nay. `docs/delivery/LANE_B_CHECKPOINT.md`, phần host recovery và trial 2026-11-16. |
| P0 | Hoàn tất Q-09/G6 cho **release mới `de38fc6b`**, gồm failed-verify | Checkpoint mới nhất giữ 2 receipts cùng release, 1 recovered, 1 attempt `TRIAL_FAILED` không bỏ mẫu số; 0 failed-verify, 1 mutation không unsafe. Còn 9 golden-path + 1 failed-verify theo checkpoint, từng trial cần operator known-good/policy authorization; summary/audit theo release và owner acceptance. Không kế thừa 9/10 của `badf9da0`. `LANE_B_CHECKPOINT.md` trial 2026-11-16; `CONTROLLED_REMEDIATION.md`. |
| P0 | Hoàn tất full G4 / RCA Q-08 và independent detector MTTD | S4 v2 đã capture/source review, nhưng RCA abstain và RDS xếp thứ ba; chưa chứng minh RCA đúng. Bổ sung bằng chứng trên supported scenarios, coverage/abstentions/missed và arrival-aware clock; không coi SLO onset/IER clock là IF MTTD. `LANE_B_CHECKPOINT.md` §10; `CONSTRAINTS_AND_GATES.md`. |
| P0 | Hoàn tất nghiệm thu model Q-04…Q-07, G8 | Model `de38fc6b` chỉ có G3 duyệt có điều kiện D-27; lớp 2 `ald` S1/S3 là development, thiếu phạm vi S2/S4, MTTD live và untouched holdout. Freeze protocol/release, được cấp quyền trước final evaluation; ghi denominators/uncertainty và fail/limitation thật. Không train/tune lại bằng local evaluation. `PROJECT_STATUS.md`, D-27, `LAYER2_LOCAL_ALIBABA_DE38FC6B.md`. |
| P1 | Xây đường final protocol/capture/evaluator A4 sau freeze và authorization | Công cụ hiện tại chỉ thu nonfinal, `group_name(..., role='sealed-final')` chủ động raise `sealed-final capture/access requires a later owner-authorized final protocol` (`src/sentinelops/experiments/local_corpus.py:133`). Không có quyền mở holdout chỉ vì test pass; cần đường final riêng theo delivery plan §3, không tắt guard hoặc dùng nonfinal CLI để né. |
| P0 | Giao đường demo AWS thực, ba lượt liên tiếp không sửa tay | Kế hoạch demo 2026-11-17/18 thay ghi chú “AWS offline only” cũ trong phạm vi demo: A được giao apply/deploy, region `ap-southeast-1`, EKS 1.34/2 node, RDS micro, ECR/S3. Còn phải kiểm image digests, deploy workload/observability/API/workers/Secrets, incident thật/UI, operator-approved rollback và recovery/escalation trên EKS. Đây là việc deployment/runtime; local report này không thực thi hoặc xác nhận AWS. `docs/delivery/AWS_DEMO_PLAN.md` §2–5. |
| P1 | Đóng các quyết định demo và production identity | Chốt Cognito/OIDC hay API trên máy operator, có token/login thật; tạo runtime DSN TLS và Kubernetes Secret theo phương án được review; xác định người duyệt known-good. Demo có ngoại lệ role `control_writer LOGIN` hết hạn 2026-11-25 UTC, cần thu hồi; không nhầm adapter fixture với provider runtime. `AWS_DEMO_PLAN.md` §2, §5; `AWS_BASELINE.md`. |
| P1 | Nghiệm chứng remote worker/source/rollback transport trên EKS | Code hiện có `render_worker_manifests.py`, source policy `in-cluster`, remote rollback transport và `kubernetes_testbed.py --target eks`. Docs ghi PR #70 OPEN/code-only; cần đối chiếu đúng commit/merge và runtime trước bàn giao, không gọi phần này là chưa viết toàn bộ hoặc đã chạy AWS. `PROJECT_STATUS.md`, `LANE_B_CHECKPOINT.md` follow-up 2026-11-17, `AWS_DEMO_PLAN.md`. |
| P1 | H3 / G7: nguồn AWS thật và model source compatibility | Collector/exporter/CloudWatch/KMS/EMF/X-Ray có offline implementation nhưng chưa có target delivery/parity proof trong tài liệu đã đọc. Cần IAM/IRSA bindings, histogram/time/unit/source quality, parity trên AWS, bundle S3 và action+verify+escalate evidence. Demo dùng Prometheus trong cluster và loại CloudWatch/X-Ray khỏi demo; không tính demo đó là full G7. `AWS_DEMO_PLAN.md` §3, §6; `AWS_BASELINE.md`; gate G7. |
| P1 | Thu thập logs ngoài business events | Opt-in business OTLP emitter đã có; stdout infrastructure/DB/uvicorn chưa có collection đầy đủ. Chọn/wire shipper hoặc nguồn tương ứng và chứng minh delivery, retention, quyền đọc; stdout/flush không chứng minh CloudWatch lưu. `PROJECT_STATUS.md`, `LANE_B_CHECKPOINT.md` Business OTLP logs. |
| P1 | G5 / Bedrock provider runtime nếu giao scope sản phẩm đầy đủ | Bounded async provider adapter và deterministic fallback có implementation; Bedrock/account/model access/credentials/client/runtime validation chưa giao. Demo hiện chốt Bedrock tắt, giải thích deterministic thật; ghi rõ đây không phải LLM. Không cần bật Bedrock để hoàn thành phạm vi demo đã chốt. `EXPLANATIONS.md`, `src/sentinelops/explanations/bedrock.py`, `AWS_DEMO_PLAN.md` §2, §6. |
| P1 | Release/handoff docs, state/cost/teardown và acceptance sign-off | Đồng bộ các đoạn status cũ “offline only”, “AWS chưa triển khai”, start-next B1 với cập nhật demo mới; giữ lịch sử có ngày. Đối chiếu đúng SHA/CI/runtime và PR #70, tránh kế thừa kết quả cũ. Giao immutable versions, operator guide, evidence refs và limitations; full G4–G8 vẫn cần bằng chứng riêng. Chuẩn bị thu hồi access entries trước 2026-11-24, tắt demo login/xóa Secrets, ECR images, bucket versions, RDS protection/snapshots/log group và KMS retention theo quyết định owner. `AWS_DEMO_PLAN.md` §8; delivery plan §6; gates G8. |
| P2 | Chốt các decision register còn mở theo nguồn hiện hành | D-05/06/11/14/15 còn PROPOSED/UNKNOWN trong gates; D-12 region/budget đã có quyết định mới ở AWS demo plan, không coi mọi UNKNOWN lịch sử còn chặn. Nếu cần competition sign-off phải có authoritative rules/DOCX/traceability (D-14). `CONSTRAINTS_AND_GATES.md` §11; `AWS_DEMO_PLAN.md`. |

Không đưa các đường legacy bị cố ý chặn vào backlog bắt buộc: `src/sentinelops/ml/readiness.py` từ chối GAMMA tự khai chứng nhận vì chưa có source-backed verifier; README/ADR-016/019 đã chọn Alibaba và D-23 đã gỡ fitting local. Không “hoàn thiện” bằng cách bỏ guard hoặc hồi sinh training source retired. Tương tự, UI time-range/search/global aggregation, public action dispatch và auto EXPLAINED lifecycle chưa có contract/authority tương ứng; đó là giới hạn scope, không phải yêu cầu tự bổ sung cho lần kiểm thử này.

## Bàn giao

Hoàn tất suite và báo cáo; đã đối chiếu 539 tracked files không đổi. Không commit/push repo. Các commit phát sinh bên trong test identity là fixture trong clone tạm do chính suite tạo, không phải commit vào checkout gốc.

Đã xóa đúng hai container test đã dừng bằng `docker container rm sentinelops-local-build-20261118 sentinelops-local-build-rerun-20261118` (thành công). Image/cache và bản sao/venv trong WSL `/tmp/sentinelops-local-build-20261118-recheck/` được giữ để tái hiện, ngoài repo; không thao tác hai container `tixflow` có sẵn.

Đã chạy **`hc send t1 'done build'`**, exit **0**, phản hồi `{"started":null,"status":null,"turn":0}`. Lệnh gửi hoàn tất được CLI chấp nhận; không suy rằng người nhận đã đọc.
