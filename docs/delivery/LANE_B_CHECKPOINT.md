# Lane B checkpoint — trạng thái, việc cần A, bước tiếp theo

Baseline: `main` `d605826`, 2026-11-05. Đây là checkpoint giao việc giữa hai lane,
không phải báo cáo PASS. Mọi số liệu dưới đây lấy từ Git/CI/runtime thật trên
máy B; **không suy ra gate nào đã nghiệm thu từ số test**.

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
| **H1 model** | Ba service bundle, `training-manifest.json`, dataset/policy refs, checksums, library/code versions, evaluation/model card, source-parity status; URI materialize được, không phải path máy A | Wiring đã xong; model thiếu thì `/v1/detect` 503 và UI vẫn đọc incident |
| **H4 action eligibility** | IER/revision kèm RCA/evidence có scope, freshness, coverage, confidence/abstention thật | Executor/guard/journal/RBAC đã có; auto action giữ blocked |

B không cần raw train của A để hoàn thành UI. Chỉ bước **bật inference thật** và
**rollback sản phẩm thật** chờ hai boundary trên.

## 5. Bước tiếp theo, theo thứ tự

A đã chốt và sửa §3.1; lỗi `public-data` cũ cũng đã rerun thành công (§3.2).
B hiện có thể thực hiện bước 1 mà không chờ model hoặc AWS. Các bước sau cần
handoff thật, không chỉ một bản code/fixture đã pass.

1. **B áp dụng bản sửa source v2:** nâng immutable host release, stop/start
   health và incident theo [rollover runbook](../runbooks/OBSERVABILITY.md#prometheus-source-identity-and-stream-rollover),
   chọn đúng READY stream rồi chạy lại `check_lane_b_live`. Giữ DB/checkpoint
   lịch sử; không cần A ra thêm quyết định về hash.
2. **A giao H1 local** → B activate model theo release đã duyệt, pin release và
   restart có kiểm soát, giữ release trước để rollback. H1 cần model/evaluation
   thật; job thu thập và fitting có điều kiện đã chạy lại dưới systemd trên máy A,
   chưa phải một model được chấp nhận hay artifact B tải được.
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

## 7. Những gì checkpoint này KHÔNG tuyên bố

- **AWS chưa triển khai.** Không `apply`, không gọi AWS API, không có credential.
- **Chưa có model được chấp nhận.** G2/G3 chưa nghiệm thu; không model nào được
  phép activate.
- **G4 đầy đủ chưa nghiệm thu.** Ranking là suspect kèm evidence, không phải
  nguyên nhân đã xác nhận.
- **G5 chưa nghiệm thu.** Explanation dùng deterministic fallback thật, không
  phải Bedrock.
- **G6 chưa nghiệm thu.** Auto action off; chưa có approved known-good ledger và
  chưa có lần rollback sản phẩm nào được verify.
- **Production authentication chưa triển khai.** Token local là static development
  credential; môi trường khác fail closed. Grafana anonymous Viewer là sandbox.
- Health checkpoint backlog vẫn **UNKNOWN**, không phải healthy/caught-up.
