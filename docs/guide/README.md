# SentinelOps — Hướng dẫn tổng hợp

> Tổng hợp ngày 18/11/2026 tại commit `0884445`. Chi tiết, trích dẫn file đầy đủ nằm trong 4 tài liệu con:
> [01 Tổng quan & kiến trúc](01_OVERVIEW.md) · [02 AI, train & dữ liệu](02_AI_TRAINING_DATA.md) · [03 Quy trình xử lý sự cố](03_INCIDENT_FLOW.md) · [04 Kết quả build local & backlog](04_LOCAL_BUILD_STATUS.md)

## 1. Repo này làm gì?

SentinelOps là **nền tảng AIOps dựa trên bằng chứng** cho một hệ microservice nhỏ (Checkout, Payment, Inventory, PostgreSQL). Nó:

1. Thu metrics / logs / traces (OpenTelemetry, Prometheus) từ các service.
2. Chuẩn hóa và kiểm tra chất lượng dữ liệu (thiếu/stale → `UNKNOWN`, không đoán).
3. Đánh giá SLO (p95 > 750 ms hoặc error rate > 5%) và bổ sung tín hiệu ML.
4. Gom các triệu chứng thành incident, gán severity.
5. Xếp hạng nguyên nhân gốc (RCA) có quyền "abstain" khi không chắc.
6. Sinh giải thích (deterministic, tùy chọn Bedrock LLM).
7. Remediation duy nhất: **rollback Payment về image known-good đã duyệt**, có journal, guard và verify bằng telemetry.

Cấu trúc: `services/` = hệ thống bị giám sát (testbed); `src/sentinelops/` = control plane (`telemetry/`, `incidents/`, `workers/`, `ml/`, `explanations/`, `remediation/`, `experiments/`, API + UI); `contracts/` = JSON schema; `infra/` = SQL, Kubernetes, Terraform AWS.

## 2. AI được train như nào, dữ liệu ở đâu?

| Thành phần | Loại | Vai trò |
| --- | --- | --- |
| Isolation Forest | ML không giám sát | Phát hiện latency bất thường so với phân bố healthy |
| XGBoost (`ExceedanceRisk`) | ML có giám sát | Dự báo latency vượt ngưỡng trong 300 s tới (`watch` / `degrading`) |
| Median/MAD, slope | Thống kê | Baseline đối chứng |
| SLO health, RCA, severity | Luật deterministic | Không train |
| Bedrock LLM | Tùy chọn, mặc định tắt | Chỉ chọn thẻ bằng chứng cho phần giải thích; không quyết định gì |

**Dữ liệu train:** trace production công khai **Alibaba cluster-trace-microservices-v2022 (bảng MSRTMCR)** — 4 cột `timestamp, msname, http_mcr, http_rt`; 240 archive có checksum (raw và trọng số model **không nằm trong Git**). Telemetry local thật dưới fault injection chỉ dùng để **đánh giá**, không dùng để train. `samples/` chỉ là dữ liệu minh họa.

**Pipeline:**
```sh
python -m scripts.aggregate_alibaba --raw <root>/raw --output <root>/minutes
python -m scripts.train_alibaba --minutes <root>/minutes --output <root>/fit-<date>
python -m scripts.package_alibaba_bundle --fit <root>/fit-<date> --output <root>/bundle-<date>
```
- Chia split theo hash tên microservice (60/20/20 train/calibration/eval), tránh leakage.
- Feature: cửa sổ 5 phút, latency/traffic tương đối so với trailing median baseline (`http-relative-5m/v2`).
- Nhãn sinh tự động bằng luật (vượt K × baseline liên tiếp 2 phút), không phải nhãn người gán.
- Ngưỡng lấy quantile 99% trên tập calibration (ngân sách báo sai 1%).
- Bundle hiện hành `de38fc6b`: mới duyệt G3 **có điều kiện**; model không tự activate hay tự retrain.

Lưu ý: `scripts/download_public_data.py` chưa hỗ trợ tải Alibaba — phải tự acquisition raw.

## 3. Quy trình xử lý một sự cố

```
Telemetry → Kiểm tra chất lượng → Detect (SLO + ML) → Triage (gom, severity, OPEN)
→ Diagnose (RCA, RCA_READY) → Explain → Decide
   ├─ RCA yếu / ngoài runbook → ESCALATED → con người
   └─ Payment + có deployment change + known-good đã duyệt + policy bật
        → Guard/preflight → Rollback 1 lần (journal) → Verify (rollout + 3 bucket SLO)
             ├─ OK → recovered
             └─ fail/timeout → escalate + quarantine → operator
→ Close (natural recovery) / bàn giao
```

### Có cần con người không? — **Có, ở các điểm then chốt**
Máy tự làm: thu thập, health, gom nhóm, RCA, giải thích, kiểm tra guard. Con người phải:
- Chọn/xác thực nguồn telemetry gắn đúng workload.
- Duyệt model release và **duyệt image known-good** (có actor/thời gian).
- Bật policy cho phép runbook (mặc định **tắt**, có kill switch).
- Điều tra mọi ca RCA ambiguous / ngoài scope / DB / nhiều lỗi.
- Review và release quarantine sau khi action thất bại.
- Cấp quyền DB/RBAC, AWS, Bedrock, nghiệm thu gate G4–G8.

## 4. Xếp hạng vấn đề như nào?

**Severity** (luật trong code, `incidents/rca_replay.py`):
- `critical`: Checkout có error rate ≥ 20% trong 2 quan sát liên tiếp.
- `high`: ảnh hưởng Checkout hoặc ≥ 2 business service.
- `medium`: còn lại (một service đơn lẻ).
- `low`: có trong contract nhưng hiện chưa sinh.

Chưa có priority queue / paging / SLA theo severity; API sort theo thời gian tạo.

**RCA score** (`configs/rca_policy.json`) — mức hỗ trợ giả thuyết, *không phải* severity hay xác suất:

| Thành phần | Trọng số |
| --- | ---: |
| anomaly_strength | 0.25 |
| temporal_precedence | 0.20 |
| dependency_trace_relevance | 0.25 |
| log_support | 0.15 |
| recent_change_support | 0.15 |

Chỉ kết luận khi top score ≥ 0.75, coverage ≥ 0.75, cách ứng viên thứ hai ≥ 0.15 và không mơ hồ; ngược lại **abstain** → con người.

## 5. Giải quyết được những vấn đề nào?

| Vấn đề | Phát hiện/chẩn đoán | Tự khắc phục? |
| --- | --- | --- |
| Deploy image Payment lỗi (S2) | Có | **Có** — rollback known-good (runbook tự động duy nhất) |
| Payment latency/lỗi đột ngột (S1) | Có | Không — chờ tự hồi hoặc operator |
| Latency tăng dần (S3) | Có, XGBoost cảnh báo sớm | Không (trừ khi trùng ca S2) |
| Lỗi Inventory / Checkout, lan truyền | Có | Không |
| DB pressure / lock (S4) | Có (RCA có thể xếp RDS) | Không — escalate DBA |
| Nhiều lỗi đồng thời (S5) | Tách nhóm, abstain khi mơ hồ | Không |
| Thiếu/stale telemetry | Báo `UNKNOWN` | Không bịa dữ liệu |
| Worker/DB của control plane crash | Resume an toàn (journal, CAS) | Có, trong control plane |
| LLM lỗi | Fallback deterministic | Có |

**Không làm:** restart/scale tùy ý, sửa code/config, sửa dữ liệu nghiệp vụ, xử lý bảo mật/mạng/disk/OOM, dự báo 5xx, tự duyệt known-good, tự retrain model, multi-cluster, paging/on-call.

## 6. Kết quả kiểm thử local (Codex `build`)

Chạy trong container `python:3.11-slim` (máy có Python 3.15/3.14 — **ngoài yêu cầu `>=3.11,<3.13`**):
- **pytest 2225/2225 pass**, UI JS 25/25, contracts 53/53, compose config 4/4, wheel smoke PASS. Không có lint được cấu hình.
- Lần đầu 10 test fail chỉ do Git "dubious ownership" trên bản sao tạm — không phải lỗi code.
- 1 warning: `anyio.abc.BlockingPortal` deprecated.
- Chưa chạy: full stack thật, Playwright, Terraform/promtool/kubeconform, AWS.

### Việc còn cần build / hoàn thiện (chi tiết ở [04](04_LOCAL_BUILD_STATUS.md))
**P0**
- Cài Python 3.11/3.12 (venv) để chạy local trực tiếp.
- Khôi phục luồng telemetry tươi → worker → API/UI trên host (stream dừng từ 14/11).
- Hoàn tất Q-09/G6 trial rollback cho release `de38fc6b` (còn 9 golden-path + 1 failed-verify).
- Hoàn tất G4/RCA Q-08 (RCA S4 đang abstain) và đo MTTD detector độc lập.
- Nghiệm thu model Q-04…Q-07/G8 (holdout chưa mở).
- Demo AWS thật (EKS `ap-southeast-1`), chạy 3 lần liên tiếp không sửa tay.

**P1**
- Final protocol/evaluator sau freeze; quyết định auth (Cognito/OIDC), DSN TLS, Secrets; **thu hồi login `control_writer` trước 25/11**.
- Kiểm chứng remote worker/rollback trên EKS (PR #70); nguồn AWS thật (CloudWatch/X-Ray parity, G7).
- Thu log hạ tầng ngoài business events; Bedrock runtime (nếu cần full scope).
- Đồng bộ docs trạng thái cũ, kế hoạch teardown/chi phí.

**P2** — Chốt các decision D-05/06/11/14/15.
