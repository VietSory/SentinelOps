# Lane B checkpoint — trạng thái, việc cần A, bước tiếp theo

Các mục 1–3 lưu baseline trên máy B tại `d605826` (2026-11-05) và bản sửa tiếp
sau. Mục 4–5 cập nhật tình trạng bàn giao local ngày 2026-11-08. Bằng chứng trên
máy A và xác nhận trên môi trường B được ghi riêng.

**Trạng thái hiện hành 2026-11-12 ở mục 9.** Các mục cũ giữ mốc đo và giới hạn
của thời điểm đó; không dùng câu “chưa có model/rollback/OIDC” của baseline để
ghi đè báo cáo tích hợp mới.

Đọc cùng [plan hai người](DELIVERY_PLAN.md), [contract](WORKSTREAM_CONTRACT.md)
và [PROJECT_STATUS](../PROJECT_STATUS.md).

## 1. Mức bằng chứng — đọc bảng này trước

Bốn mức khác nhau, không được gộp:

| Mức | Nghĩa |
| --- | --- |
| **runtime-verified** | Đã chạy trên process/DB/browser/Prometheus thật trên máy B |
| **fixture-tested** | Chỉ chứng minh phần mềm; fixture không phải dữ liệu nghiên cứu hay kết quả runtime sản phẩm |
| **code-only** | Có implementation, chưa quan sát trên runtime |
| **blocked** | Cần handoff hoặc quyền từ bên khác |

## 2. B đã giao gì

| Capability | Mức | Nguồn kiểm |
| --- | --- | --- |
| Isolated B stack, 9 container, port riêng, volume riêng | runtime-verified | [LOCAL_TESTBED](../runbooks/LOCAL_TESTBED.md) |
| Operator UI read-only: list/filter/cursor, detail pin revision, evidence, timeline | runtime-verified trên IER SLO thật revision 4 | [INCIDENT_API](../runbooks/INCIDENT_API.md) |
| H2 explanation HTTP/UI, deterministic fallback, refs theo revision | runtime-verified (`deterministic-facts/v1`, 12 claims, 13 evidence) | [EXPLANATIONS](../runbooks/EXPLANATIONS.md) |
| Local read-only operator credential tách producer | runtime-verified (401/403/503, systemd, PostgreSQL thật) | [INCIDENT_API](../runbooks/INCIDENT_API.md#local-setup) |
| Host release v2: wheel/dependency copy, checksum guard, systemd start/stop/restart | runtime-verified (SIGKILL→restart, checkpoint bền) | [HOST_PROCESSES](../runbooks/HOST_PROCESSES.md) |
| Immutable image/config deployment lock | runtime-verified | [LOCAL_TESTBED](../runbooks/LOCAL_TESTBED.md) |
| Operator Prometheus rules (10 rules) | runtime-verified (Prometheus thật load + alert chuyển firing) | [OBSERVABILITY](../runbooks/OBSERVABILITY.md#operator-worker-rules) |
| Grafana console provisioned read-only, 11 panel | runtime-verified (Grafana 12.2.0, 11/11 expression evaluate thật) | [OBSERVABILITY](../runbooks/OBSERVABILITY.md#operator-console) |
| AWS IaC baseline: network/EKS/RDS/S3/IAM/observability | **code-only, chưa apply** | [AWS_BASELINE](../runbooks/AWS_BASELINE.md) |
| Guarded executor, action journal, RBAC, quarantine review | fixture-tested + Kubernetes/PostgreSQL thật trên workload fixture | [CONTROLLED_REMEDIATION](../runbooks/CONTROLLED_REMEDIATION.md) |
| Demo guide local | runtime-verified, trừ một bước ghi rõ blocked | [DEMO_LOCAL](../runbooks/DEMO_LOCAL.md) |
| Rollback Payment sản phẩm thật | **blocked** | cần H4 (§4) |
| Triển khai AWS | **blocked** | cần quyền cloud (§4) |

### Commit và CI đúng head đã publish

| PR | Head | Merge | CI |
| --- | --- | --- | --- |
| #37 auth | `ef76a9e` | `5a5864b` | `42031550091` success |
| #38 rules | `ae24294` | `e8e6e65` | `42041277113` success |
| #39 AWS IaC | `72853ec` | `1f0a0ce` | `42047760225` success |
| #40 console | `765ccb7` | `ba39c62` | `42052447722` success |
| #41 demo guide | `ec8cf48` | `d605826` | `42058783170` success |

CI của #41 là scope docs: `changes`, `foundation` (`validate_contracts`, `pytest -q`
toàn bộ, `check_installed_package`) và `ci-summary` chạy thật; 10 job native/UI/iac
**skipped vì ngoài scope**. Job skipped **không** được mô tả là native measurement
đã pass.

### Artifact chỉ tồn tại trên máy B

`artifacts/` nằm ngoài Git. Các host release (`e4cc72d8…` source `ef76a9e`,
`c302da62…` source `03d3c78`, đều `credential_profile=local-operator-read/v1`) và
mọi report runtime là **local, không bàn giao được**. Consumer phải tự build và
verify release của mình theo runbook; path trên máy người gửi không đủ là handoff.

## 3. Việc cần lane A quyết định

Hai mục, đều thuộc code/semantics của A. B **không** sửa.

### Cập nhật xử lý A — 2026-11-06

Bản sửa source `1cc7da4` + publisher `23a015e` đã có
[CI đúng head 23a015e](https://github.com/VietSory/SentinelOps/actions/runs/42149412823)
**success 14/14**: native/public-data/observability/S5/remediation đều chạy thật.
Local regression 1.313 test, contracts và installed wheel đã pass. Đây là bằng
chứng phần mềm/source integration của bản sửa, không phải xác nhận đã nâng
release trên máy B hay H1/H4/G3/G4/G6 đã nghiệm thu.

- **§3.1 có bản sửa:** identity source v2 bỏ riêng `rule_files`/`alerting`, giữ
  source settings; native evidence dùng cùng phép kiểm. B live checker chọn duy
  nhất consumer khớp nguồn hiện hành và kiểm identity/policy/root, giữ nguyên các
  consumer/checkpoint lịch sử. B cần nâng host release và stop/start sang stream
  mới đúng [quy trình rollover](../runbooks/OBSERVABILITY.md#prometheus-source-identity-and-stream-rollover).
  Đây là bản sửa phần mềm, chưa phải xác nhận đã apply trên máy B hay B1/G4 PASS.
- **§3.2:** main `694e9df` đã có CI `42061564345` success; lỗi cũ `42041999448`
  đã rerun **failed jobs** và **success ở attempt 2**, không chạy lại mọi job. Metadata GAMMA
  thêm tối đa hai retry cho transport/408/429/5xx tạm thời; auth/not-found,
  invalid JSON, byte budget và version/checksum/lock mismatch vẫn fail closed.
  CI/head mới và kết quả rerun phải kiểm riêng, không lấy status cũ làm bằng chứng.
- **H1/H4 vẫn cần evidence thật:** chưa có model được chấp nhận hoặc authority
  deployment-change được chứng minh. Không bật model/action từ bản sửa identity.
  AWS vẫn chờ các quyết định cụ thể ở §6; không có `terraform apply` trong sửa này.

Phần mô tả §3.1 bên dưới giữ lại để giải thích môi trường legacy đã tạo ra blocker.

### 3.1 `configuration_hash` quá rộng làm fork health stream

`src/sentinelops/telemetry/prometheus.py:113` lấy source identity từ
`stable_id(<toàn bộ running Prometheus config>)` đọc từ `/api/v1/status/config`.
B thêm `rule_files` cho operator alert rules (PR #38) → hash đổi:

| Config | `configuration_hash` |
| --- | --- |
| có `rule_files` | `44c63e2cd7d6d73b41bd034b67e852b09aedc6cd3baf847baed6dd1c2e4beab9` |
| không `rule_files` | `06dce0d48a07e3cd2e7252513afe42d36533a6ec8d0183cef94e8d84ce728e10` |

**`scrape_configs` và `global` byte-identical** giữa hai trường hợp — nguồn thật
không đổi. Hệ quả trên DB B:

| Stream | Last bucket |
| --- | --- |
| `0b6f3101…` | 2026-11-01 03:06Z — dừng advance |
| `a2a5cbad…` | hiện tại |

Không mất dữ liệu: stream cũ, checkpoint và consumer của nó còn nguyên. Nhưng
giờ có hai consumer nên `scripts/check_lane_b_live.py:55` từ chối chạy
(`len(consumers) != 1`), tức đường nghiệm thu B1 end-to-end bị chặn trong môi
trường này.

Fork khi source config đổi là **đúng thiết kế** — chặn trộn dữ liệu khác nguồn.
Câu hỏi cho A: hash có nên phủ **toàn bộ** config, hay chỉ những input định nghĩa
source semantics (`global`, `scrape_configs`)? Hiện tại một thay đổi
alerting/dashboard của B fork stream của A mà nguồn không đổi.

B đã **từ chối** hai cách lách: xóa checkpoint consumer cũ, và nới guard
`len(consumers) != 1`. Cần A cho hướng: đổi phạm vi hash, hoặc cung cấp consumer
migration protocol, hoặc xác nhận cách retire một stream đã bỏ.

### 3.2 `public-data` fail trên main vì fetch publisher ngoài

Run post-merge `42041999448` trên `e8e6e65` fail job `public-data` tại
`scripts/inventory_gamma_archive.py`: `GAMMA archive unavailable: HTTPStatusError`.
Cùng job **pass** trên `5a5864b` và trên PR #39 (`72853ec`), và diff giữa các
commit đó không chứa file GAMMA/public_data nào → lỗi tải archive tạm thời, không
phải regression. Cần A hoặc owner rerun. Thuộc scope A, B không chạm.

## 4. B đang chờ gì từ A

| Boundary | B cần nhận | Trước khi nhận, B vẫn làm được |
| --- | --- | --- |
| **H1 model** | A đã thu đủ 30 lượt nonfinal, xác minh nguồn, huấn luyện ba service bundle và đóng gói kèm model cards, checksums, evaluation và dependency pins. Gói review đi qua kênh riêng | Khi nhận gói, B kiểm checksum, chạy replay và kiểm telemetry trên môi trường đích. Bật inference khi G3 và source parity được nghiệm thu |
| **H4 action eligibility** | A cần giao IER/revision cùng RCA đã xác minh trên Kubernetes, đủ freshness, coverage và confidence. Lượt Payment local đã có `RCA_READY`, kết quả vẫn abstain | B có thể chuẩn bị hồ sơ known-good, policy và dry-run. Guard, journal và RBAC đã có. Dispatch chờ H4 đủ điều kiện và phê duyệt operator |

[Hướng dẫn model hiện hành](../runbooks/MODEL_PIPELINE.md#source-parity-and-serving-release)
tách kiểm nhận gói, replay, source parity và nghiệm thu G3. [Guard H4](https://github.com/VietSory/SentinelOps/actions/runs/42455708564)
kiểm tra revision, tham chiếu RCA và health hiện tại trước khi gọi Kubernetes.
[Runbook H4](../runbooks/CONTROLLED_REMEDIATION.md#kubernetes-incident-evidence-source)
mô tả nguồn và feed để B chuẩn bị tích hợp sau khi điều kiện dispatch được duyệt.

Cập nhật 2026-11-08: nguyên nhân gốc của H1/H4, quyết định ADR-015 và việc B làm
song song nằm ở [mục 7](#7-handoff-a--b-ngày-2026-11-08-adr-015-và-việc-b-làm-song-song).

## 5. Bước tiếp theo, theo thứ tự

A đã chốt và sửa §3.1; lỗi `public-data` cũ cũng đã rerun thành công (§3.2).
B hiện có thể thực hiện bước 1 mà không chờ model hoặc AWS. Các bước sau cần
handoff thật, không chỉ một bản code/fixture đã pass.

1. **B áp dụng bản sửa source v2:** nâng immutable host release, stop/start
   health và incident theo [rollover runbook](../runbooks/OBSERVABILITY.md#prometheus-source-identity-and-stream-rollover),
   chọn đúng READY stream rồi chạy lại `check_lane_b_live`. Giữ DB/checkpoint
   lịch sử; không cần A ra thêm quyết định về hash.
2. **A giao H1 local.** B kiểm gói và xác minh telemetry trên môi trường đích
   trong khi A xử lý nghiệm thu G3. Khi đủ cả hai điều kiện, chọn cùng release
   cho API và worker. Dùng [`--stream-epoch`](../runbooks/LOCAL_TESTBED.md) để
   tạo stream mới, giữ release và stream trước cho rollback.
3. **A giao H4** → B chạy rollback Payment thật trên Kubernetes local với
   known-good digest, policy/dry-run/approval, idempotency/lock/audit, verify bằng
   telemetry và escalation. Đây là G6; thiếu source/qualified RCA/approval vẫn block.
4. **AWS để cuối**, theo owner ngày 2026-11-06: chỉ sau khi hoàn tất các mục local
   mới chốt region/quota/budget/quyền apply (§6), apply baseline, giao workload
   manifest pin theo digest và H3 live source cho A verify parity (G7).
   Không tự apply trong lúc chờ H1/H4.

## 6. Điều kiện owner phải xác nhận trước khi apply AWS

Không mục nào được cấp bởi tài liệu này: region; account (`allowed_account_ids`);
quota EKS/NAT/EIP/RDS/instance type; budget và địa chỉ nhận cảnh báo; Bedrock
model access theo model và region; ai được chạy `terraform apply` và quyết định
state backend. Chi tiết và dự toán chi phí ở [AWS_BASELINE](../runbooks/AWS_BASELINE.md).

## 7. Handoff A → B ngày 2026-11-08: ADR-015 và việc B làm song song

### 7.1 Bối cảnh: vì sao H1 và H4 còn bị chặn

H4 phụ thuộc trực tiếp vào H1:

- `remediation/authority.py` chỉ cấp dispatch authority khi registry có model
  hợp lệ (`registry.available`).
- Ở ca S2 Kubernetes `local-s2-unknown-retry-20261108`, RCA abstain vì thiếu
  thành phần `anomaly_strength` (IF). Payment chỉ đạt score 0.549, coverage 0.55.
- Tính offline với điểm IF của candidate, Payment đạt score 0.799, coverage 0.80,
  margin 0.40, vượt cả ba ngưỡng 0.75/0.75/0.15. Đây là projection, chưa phải
  qualification.

H1 bị chặn ở G3 vì phép so sánh IF với MAD trên bộ development **không chứa thông
tin**:

| Model | Kết quả development (candidate `38bcc766…`) |
| --- | --- |
| XGBoost (pre-SLO risk) | AP 0.93–1.00, precursor recall 1.0 trên cả 3 service |
| IF / MAD (detector) | 0/20 event, cả hai F1 = 0 |

Nguyên nhân: capture plan v1 chỉ có run healthy và gradual. Trong run gradual, IF
đã báo ở pha precursor, trung bình khoảng 10 phút trước SLO onset. Theo luật đã
freeze, alert trước onset không được tính là detection (CONSTRAINTS §7), nên 0/20
là kết quả **đúng theo protocol**. Nhưng bộ dữ liệu không có kịch bản lỗi đột
ngột, tức kịch bản mà spec giao cho IF (Q-04 trên S1–S4; S1 kỳ vọng
*detection, không dự báo*).

### 7.2 Quyết định và phần A đã giao

[ADR-015](../adr/ADR-015-detector-event-evaluation-coverage.md) được owner phê
duyệt ngày 2026-11-08:

- **Không đổi:** luật chấm v1 và kết quả 0/20, vai trò model, dòng train và
  calibration, threshold, RCA policy, các gate G3/G8.
- **Bổ sung:** một epoch riêng `det-NN`, chỉ để đánh giá, gồm run S1 lỗi đột ngột
  của Payment và Inventory. Nhóm số lẻ dùng latency 1200 ms, nhóm số chẵn dùng
  error. Tối thiểu 5 group. Dòng của epoch này không bao giờ đi vào fitting.
- **Protocol `local-model-development/v2`** chạy bên cạnh v1. IF và MAD được chấm
  trên onset của run lỗi đột ngột. Episode bắt đầu trong cửa sổ precursor đã xác
  minh được đếm riêng, không tính vào giờ healthy.
- **H4** vẫn đi đường Kubernetes S2 riêng, qua các guard hiện có.

Phần mềm A đã có: plan
[`local_http_detector_events_plan.json`](../../configs/local_http_detector_events_plan.json),
protocol [`local_model_evaluation_v2.json`](../../configs/local_model_evaluation_v2.json),
`scripts/audit_detector_events.py`, `scripts/evaluate_detector_events.py` và
campaign `--plan`. Cách chạy xem
[corpus runbook](../runbooks/LOCAL_HTTP_CORPUS.md#adr-015-detector-event-epoch-evaluation-only)
và [model pipeline](../runbooks/MODEL_PIPELINE.md#evaluation).
Audit lại toàn bộ corpus đã freeze bằng code mới cho report giống hệt
`dataset-freeze.json`: 30 run, 3.060 dòng.

Tiếp theo A sẽ thu 5 group `det-NN` (khoảng 5 giờ máy), audit, freeze, chạy v2 và
giao hồ sơ G3 để owner quyết định. A không tự nghiệm thu G3.

### 7.3 Hợp đồng ranh giới cho đợt này

| Hạng mục | A sở hữu | B sở hữu |
| --- | --- | --- |
| Corpus, epoch `det-NN`, evaluator, model, RCA, worker | Triển khai và vận hành | Chỉ đọc và review; báo lỗi cho A |
| Kubernetes testbed, executor, known-good, policy, RBAC | Không sửa | Triển khai và vận hành |
| Auth, packaging, host release, AWS | Không sửa | Triển khai và vận hành |
| Quyết định G3, activation, dispatch | Cung cấp bằng chứng | Owner quyết định; B thực thi sau khi được duyệt |

Nguyên tắc cho cả hai bên:
- Không bật model và không dispatch rollback trước khi owner duyệt G3 và H4.
- Không dùng run S1, S2 hay smoke làm dữ liệu train.
- Không xóa volume hay checkpoint của bên kia.

### 7.4 Việc B làm ngay, không cần chờ A

| # | Việc | Input / lệnh | Xong khi |
| --- | --- | --- | --- |
| B-a | **Review độc lập ADR-015 và code v2.** Hiện chỉ có self-review của A, G3 cần reviewer thật | Commit ADR-015 và commit triển khai trên `main`; đọc `ml/detector_evaluation.py`, `ml/local_data.py` (`audit_detector_events`), `experiments/local_corpus.py`, `experiments/traffic.py` | Có kết luận ghi rõ phạm vi đã xem; lỗi (nếu có) gửi A, A sửa. **Trạng thái 2026-11-09: mục tiêu đã bị thay thế.** Candidate `38bcc766` đã gỡ (D-23), `evaluate_detector_events` đã xóa; G3 hiện hành là release Alibaba (D-22). Nhu cầu còn lại là một reviewer độc lập cho đường Alibaba (`alibaba_controls`, `check_alibaba_source_parity`, `alibaba_release`) và cho bảng Q-10 trong [báo cáo G6](../reports/G6_LOCAL_ALIBABA_BADF9DA0.md) |
| B-b | **Kiểm gói H1 trên máy B** | `python -m scripts.verify_model_candidate --candidate handoff --expected-id 38bcc7668851249c7b2246c24d0a2eb4fbeacb6b0fe306703ae91386d17956bd --replay` với gói nhận qua kênh riêng | Checksum và replay tái hiện đúng điểm development; telemetry trên môi trường đích được đối chiếu source profile `prometheus-increase-60s-5s/v1` |
| B-c | **Áp dụng source v2 và rollover stream** (mục 5, bước 1) | [Rollover runbook](../runbooks/OBSERVABILITY.md#prometheus-source-identity-and-stream-rollover), sau đó `python -m scripts.check_lane_b_live` | B1 PASS trên máy B, đủ cả ba direct route; stream và checkpoint cũ còn nguyên |
| B-d | **Chuẩn bị S2 Kubernetes cho H4** | `scripts/review_payment_image.py` (`open` / `complete` / `open-change` / `complete-change`) lập hồ sơ image trước và sau; policy và dry-run qua [controlled remediation](../runbooks/CONTROLLED_REMEDIATION.md) | Hồ sơ known-good chờ operator duyệt, dry-run pass. **Không dispatch** |
| B-e | **Production auth adapter** | API hiện fail closed (503) ngoài `local`/`test` | Có thiết kế và adapter, kèm test; token tĩnh không dùng trên AWS. **Code xong 2026-11-09** (`sentinelops/oidc.py`, [runbook](../runbooks/INCIDENT_API.md#oidc-for-non-local-environments)): JWT qua JWKS, scope map sang producer/operator, token tĩnh bị từ chối ngoài local/test. Còn: tạo user pool/app client thật ở giai đoạn AWS |
| B-f | **Policy remediation cho EKS** | `Target` đang hardcode namespace `sentinelops-b-actions` | Có policy version mới cho môi trường đích và chạy lại ma trận safety; policy local giữ nguyên. **Owner 2026-11-09: để sang giai đoạn AWS.** Phạm vi thật: (1) `Target`/`Policy` được pin trong hash journal (`asdict`), nên chỉ thêm profile theo namespace, không thêm field; (2) adapter Kubernetes chỉ cho endpoint loopback, nên cần adapter EKS (IRSA token, CA cluster, endpoint https từ xa); (3) cần chốt namespace của Payment trên EKS (IaC hiện có `sentinelops` cho service account), số replica (policy bắt `max_pods=1`) và RBAC `resourceNames` |

Đầu ra của B-b là một record `model-source-parity/v1`, theo đúng format trong
[operator serving release](../runbooks/MODEL_PIPELINE.md#source-parity-and-serving-release).
Record này chỉ được ghi `PASS` khi replay và phần đối chiếu telemetry trên môi
trường đích đều đạt.

Khi owner duyệt G3:
1. Operator chạy `python -m scripts.build_model_release` với gói H1, report v2 của
   A và cặp `parity.json`, `verification.json` của B. Lệnh tạo store release mới, tự load để kiểm,
   và không bật gì.
2. B trỏ `SENTINELOPS_MODEL_STORE` và `SENTINELOPS_MODEL_RELEASE_REF` cho cả API
   lẫn worker, dùng `--stream-epoch` mới.
3. B chạy S2 thật để kiểm H4/G6.

### 7.5 Chuỗi lệnh đầu–cuối để gỡ H1 và H4

> **Lịch sử.** Chuỗi lệnh này là của candidate local `38bcc766`. Các script của
> nó đã gỡ ngày 2026-11-09 khi bỏ train trên dữ liệu local; đường hiện hành ở
> [§7.6](#76-cập-nhật-2026-11-09-model-chính-thức-là-model-alibaba-adr-016).

Các bước có thứ tự. Mỗi bước chỉ chạy khi bước trước đạt. Docker workload chỉ
chạy khi không có corpus capture nào đang chạy trên host (các script tự kiểm).

| # | Ai | Lệnh | Đầu ra |
| --- | --- | --- | --- |
| 1 | A | `scripts/audit_detector_events.py --root <det-root> --output … --freeze` | Epoch `READY_FOR_EVALUATION`, đã freeze |
| 2 | A | `scripts/evaluate_detector_events.py … --detector-report-ref <ref> --output <v2-store>` | `report_ref` của báo cáo v2 |
| 3 | A | `scripts/g3_review_packet.py --candidate <handoff> --expected-id <ID> --v2-store <v2-store> --v2-report-ref <report_ref> --output packet.md` | Hồ sơ G3 ghi rõ báo cáo cần duyệt |
| 4a | A | `scripts/export_source_shapes.py --corpus <frozen corpus> --output shapes.json` | Manifest hình dạng nguồn (không có observation thô) + SHA-256, gửi B qua kênh riêng |
| 4 | B | `scripts/check_model_source_parity.py --kind <kind-v0.30.0> --candidate <handoff> --expected-id <ID> --source-shapes shapes.json --source-shapes-sha256 <SHA> --environment <tên> --verified-by <tên> --output …` | `parity.json` (`PASS` hoặc `FAIL`) kèm `verification.json` được ghim bằng hash |
| 5 | Owner | Đọc hồ sơ G3 và quyết định | Quyết định G3 gắn với candidate và `report_ref` |
| 6 | Operator | `scripts/build_model_release.py … --v2-store <v2-store> --v2-report-ref <report_ref-đã-duyệt> --parity-record parity.json --parity-verification verification.json --confirm-owner-decision` | Store release mới; không bật gì |
| 7 | B | Trỏ `SENTINELOPS_MODEL_STORE` / `_RELEASE_REF` cho API và worker, dùng `--stream-epoch` mới | H1 bật trên B |
| 8 | A hoặc B | `scripts/run_s2_h4.py --kind … --model-store … --model-release-ref …` | Đánh giá H4 trên sandbox **dùng một lần**: chứng minh incident S2 có model đủ điều kiện dispatch authority. Sandbox bị xóa khi xong |
| 9 | B + operator | Trial G6 trên một target Kubernetes **bền vững** do B sở hữu (xem dưới) | G6 |

Bước 4a/4 có thể chạy ngay khi Docker rảnh, không cần chờ bước 1–3. Bước 8 khi
chưa có release vẫn chạy được (đường no-model) để kiểm thử phần mềm.

Bước 8 không thể nối thẳng sang bước 9. `run_s2_h4` xóa cluster, control DB và
credential khi kết thúc, và known-good phải được duyệt **trước** toàn bộ khoảng
thay đổi xấu. Vì vậy G6 cần một trình tự riêng trên target bền vững của B:

1. Dựng target, health worker và incident worker có release model.
2. Ghi ít nhất ba bucket healthy gắn nguồn.
3. Lập hồ sơ image tốt bằng `review_payment_image.py`, operator duyệt known-good.
4. Bật policy, sau đó mới deploy image xấu và ghi change (`open-change` / `complete-change`).
5. Executor dùng `NativeH4Authority` trên đúng incident đó, trong thời hạn freshness 180 giây.
6. Rollback, verify bằng telemetry, hoặc escalate.

`scripts/run_g6_trial.py` chạy đúng trình tự này trên **một** sandbox được giữ sống
suốt trial:
- Nó dừng ở `AWAITING_OPERATOR_APPROVAL` để operator chạy
  `scripts.approve_known_good`, rồi ghi ledger ref vào `approved-ledger-ref`.
- Executor chỉ patch khi có cờ `--enable-rollback-policy` và không có file
  `KILL_SWITCH`.
- Script cần một release H1 hợp lệ.
- Nếu còn `action-audit-pending.json` sau khi trial dừng, script giữ control DB
  để operator truy xuất journal theo request trong file đó trước khi dọn DB.
- Đã kiểm CLI và chữ ký ghép nối; **chưa chạy native**.

### 7.6 Cập nhật 2026-11-09: model chính thức là model Alibaba (ADR-016)

Mục 7.5 vẫn đúng cho candidate local `38bcc766`, nhưng đó chỉ còn là bằng chứng
tích hợp. Model chính thức là **một bundle dùng chung cho cả ba service**, train
trên trace production Alibaba v2022 (`sentinelops-model/v3`, origin
`admitted-alibaba-v2022`, model `badf9da0…`). Ba bundle theo service không còn
là đường giao hàng.

| # | Ai | Lệnh | Trạng thái |
| --- | --- | --- | --- |
| 1 | A | `scripts.train_alibaba` → `scripts.package_alibaba_bundle` | Xong; manifest SHA-256 `2d1a5376…` |
| 2 | A | `scripts.alibaba_controls` (lớp 1, chạy một lần) | Xong; [hồ sơ G3](../reports/G3_CANDIDATE_ALIBABA_BADF9DA0.md) |
| 3 | A/B | `scripts.check_alibaba_source_parity --kind … --bundle … --manifest-sha256 …` (profile v2, tiêu chí P1–P6) | PASS 2026-11-09 trên `local-wsl-shared-host`; môi trường khác phải đo lại |
| 4 | Owner | Quyết định G3 | **Duyệt có điều kiện** (D-22), chỉ cho tích hợp local H4→G6 |
| 5 | Operator | `scripts.build_alibaba_release … --confirm-owner-decision` | Đã build, chưa bật |
| 6 | B | `scripts.lane_b_host start --role health --stream-epoch <epoch-mới> --source-profile prometheus-increase-60s-5s/v2`, rồi `--role incidents --health-stream <READY-v2> --model-store … --model-release-ref …`; kiểm bằng `check_lane_b_live --stream-epoch <epoch> --source-profile …/v2` | Code đã có trên `main` (`28a1184`, `d81431b`); stream v1 cũ giữ nguyên để rollback |
| 7 | A | `scripts.run_s2_h4` / `scripts.run_g6_trial` với release Alibaba: health v2 và ≥11 phút healthy trước thay đổi xấu (`41d0f74`) | H4 native đang chạy; sau đó 9 trial G6 do operator được ủy quyền |

Ràng buộc mới cho B:

- Model chỉ có tín hiệu khi service đã có ít nhất 5 phút baseline trước một cửa
  sổ 5 phút. Trước thời điểm đó, tín hiệu là *unavailable*, không phải 0.
- Model không học lỗi 5xx; lỗi do luật SLO và MAD đảm nhận.
- Kết quả chính thức của model là lớp 2 (đợt đánh giá local v2 đã khóa trước khi
  chạy) và G8, không phải số development trong hồ sơ G3.

## 8. Giới hạn nghiệm thu hiện hành

- **AWS chưa triển khai.** Không `apply`, không gọi AWS API, không có credential.
- **Model Alibaba đã có inference trên máy B**, theo [báo cáo 2026-11-09](../reports/LANE_B_ALIBABA_V2_ACTIVATION.md).
  G3 chỉ duyệt có điều kiện D-22; lớp 2 đã đo và Q-04/Q-05/Q-07 FAIL. Không coi
  inference hoạt động là chất lượng đã đạt hoặc là xác nhận runtime hôm nay.
- **G4 đầy đủ chưa nghiệm thu.** Ranking là suspect kèm evidence, không phải
  nguyên nhân đã xác nhận.
- **G5 chưa nghiệm thu.** Explanation dùng deterministic fallback thật, không
  phải Bedrock.
- **G6 chưa nghiệm thu.** Trials với known-good được duyệt trước thay đổi xấu đã
  có rollback thật (Q-09: 9/10 recovered và 1/1 failed-verify escalated). Q-10 đã
  được B review lại; vẫn cần quyết định gate của owner, không bật auto dispatch.
- **OIDC adapter đã có code/tests**, chưa cấu hình identity provider thật trên
  môi trường triển khai. Token static chỉ local/test; thiếu OIDC ngoài local thì
  fail closed. Grafana anonymous Viewer là sandbox.
- Checkpoint backlog trong các phép đo cũ là **UNKNOWN**, không phải bằng chứng
  healthy/caught-up hiện tại.

## 9. B follow-up — 2026-11-12

| Mục | Kết luận / phần tiếp theo |
| --- | --- |
| B-a review model hiện hành | Codex đọc `alibaba_controls`, `check_alibaba_source_parity`, `ml/alibaba_release` và caller/guards. 102 tests Alibaba training/release/bundle/live/evaluation software PASS với ML dependencies hoạt động. Không chạy lại evaluation một lần trên corpus thật, không thay threshold/approval. Review kiểm software admission, không chứng nhận độ đúng của raw bytes do operator cung cấp hoặc nghiệm thu G3 |
| B-b / B-c | Candidate local `38bcc766` đã retired D-23. Handoff hiện hành là bundle Alibaba chung `badf9da0`; parity/build/rollover v2 trên máy B đã có [báo cáo](../reports/LANE_B_ALIBABA_V2_ACTIVATION.md). Máy/release mới vẫn phải đo parity; không chạy lại CLI local đã gỡ |
| B-d / Q-10 | Review lại ba nhóm tests bổ sung đã hoàn tất. Thêm 8 executor/HTTP-adapter checks, remediation suite 111 PASS; PostgreSQL journal check riêng PASS, gồm outage/concurrency/quarantine. Fixture outcomes không tính thêm vào 9/10 trial thật. [Phạm vi review và lệnh tái hiện](../reports/G6_LOCAL_ALIBABA_BADF9DA0.md#b-follow-up-review--2026-11-12) |
| B-e | OIDC/JWKS adapter và scopes đã có. UI nhận operator/OIDC access token; heartbeat kiểm protected readiness khi tab visible, xóa private state khi quan sát 401/403 `insufficient_scope`, giữ error/UNKNOWN khi provider outage. Browser checker không ép model-unavailable trên deployment đã chọn model. Còn provider/app client thật + kiểm trên deployment được phép; HTTP fixtures không chứng minh provider cloud |
| B2 CI | Job có ML dependencies nay chạy riêng `tests/test_alibaba*.py` sau khi import bắt buộc NumPy/Pandas/PyArrow/sklearn/XGBoost; trước đó foundation không cài ML còn public-data không chọn các suite Alibaba, nên chúng có thể bị skip. Test identity dùng PATH của môi trường thay vì hardcode `/usr/bin:/bin`, giữ nguyên Git fixture identity và assertions |
| B-f / AWS | Giữ quyết định owner hoãn EKS policy/adapter sang AWS. Chưa apply; region/quota/budget/quyền vẫn cần chốt trước deployment |

API-only preview PR #45 đã kiểm trên máy B ngày 2026-11-12: release `c1e600f4`,
source `3782984`, browser đọc incident SLO cũ revision 4 (19 evidence/12 claims).
Restart API giữ nguyên IER và 16 control tables, model/credentials; hai worker
vẫn dừng và session/checkpoint không đổi. Đây là đọc lịch sử ngày 2026-11-09,
không phải detection/inference mới; [chi tiết rollout](../runbooks/HOST_PROCESSES.md#api-only-ui-rollout).

B có thêm [renderer business manifests offline](../runbooks/AWS_BASELINE.md#offline-business-service-manifests):
ba service, image digest, Secret reference, probes và pod hardening. Đã kiểm input,
determinism/no-overwrite và schema 1.34; không apply, chưa kiểm registry/DB/collector
trên AWS, không thay EKS remediation policy hoặc cấp model/action authority.

Thứ tự còn lại: owner review hồ sơ G6/Q-10; B chuẩn bị deployment/provider và
H3 theo phạm vi cloud được cấp; A chốt hướng detector và giao release hợp lệ.
ADR-018 vẫn **Proposed**: capture `alc` có thể chạy theo ADR nhưng evaluation
chờ phê duyệt; B không đổi serving method hoặc gate theo proposal.
