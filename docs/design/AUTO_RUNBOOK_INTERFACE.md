# Runbook có operator duyệt (mọi severity, match ≥ 0.80)

Quy tắc chủ repo cập nhật ngày **2026-11-18**. Đây là hợp đồng giữa các lane `sev`, `match`, `ctl`, `eksctl`.

## 1. Quy tắc quyết định

| Điều kiện (xét theo thứ tự) | `decision` |
|---|---|
| Kill switch bật, RCA abstain, thiếu dữ liệu (UNKNOWN), hoặc lỗi bất kỳ khi trích triệu chứng | `engineer` |
| Top entry có `score >= max(0.80, thresholds.auto_min_score)`, `margin >= max(0.15, thresholds.auto_min_margin)`, `executable == true` | `oncall` (proposal chờ operator duyệt từng incident, áp dụng cho `low/medium/high/critical`) |
| Còn lại | `engineer` |

- Score là điểm khớp **deterministic**, không phải xác suất. Không dùng LLM.
- Mọi incident ở mọi severity đều phải được operator duyệt trước khi chạy runbook. Severity dùng để hiển thị và xếp ưu tiên; không chặn proposal, không cộng điểm hay mở quyền thực thi. Khớp thấp hoặc runbook không chạy được chỉ hiển thị bước làm tay cho engineer.
- `decide()` không bao giờ trả `auto`. Điểm dưới 0.80, margin dưới 0.15 hoặc runbook thủ công đều đi `engineer`.
- Tên khóa catalog `auto_min_score/auto_min_margin` được giữ để tương thích; chỉ có thể siết ngưỡng, không hạ sàn 0.80/0.15. `oncall_min_score` là khóa tương thích cũ, không mở đường chạy.
- Approval không bỏ qua guard nào: executor vẫn kiểm H4, known-good, dry-run, 1 attempt, cooldown, kill switch và RCA ≤ 900 giây với policy mới (180 giây vẫn hợp lệ khi đọc action lịch sử).

## 2. Triệu chứng — `incident-symptoms/v1` (lane `match` sở hữu)

`src/sentinelops/remediation/matching.py`

```python
SYMPTOM_VERSION = "incident-symptoms/v1"

def extract_symptoms(*, derived: dict, observations: dict[str, list[dict]]) -> dict:
    """derived = kết quả incident đã verify (có 'rca', 'severity', 'deployment_change' tùy chọn).
    observations = {service: [observation payloads của các bucket opening, đã sort theo bucket_end]}
    Raise ValueError khi thiếu dữ liệu; caller đổi thành decision 'engineer'."""
```

Dict trả về (mọi khóa đều bắt buộc, giá trị đúng kiểu):

| Khóa | Kiểu | Nguồn |
|---|---|---|
| `symptom_version` | `"incident-symptoms/v1"` | hằng |
| `severity` | `low\|medium\|high\|critical` | `derived["severity"]` |
| `rca_abstained` | bool | `rca["abstained"]` |
| `root_service` | str \| None | `rca["ranking"][0]` nếu không abstain |
| `affected_services` | list[str] đã sort | group của `rca["group_id"]` |
| `modality` | `latency\|errors\|both\|none` | observation eligible của root: `p95_latency_ms > 750` → latency; `error_rate > 0.05` → errors |
| `root_p95_ms` | float \| None | max p95 trên observation eligible của root |
| `root_error_rate` | float \| None | max error_rate trên observation eligible của root |
| `latency_band` | `none\|mild\|moderate\|severe` | ≤750 / (750,1250] / (1250,1500] / >1500 |
| `onset` | `abrupt\|gradual\|unknown` | `gradual` nếu có model risk `watch/degrading` trước onset; `abrupt` nếu không; `unknown` nếu model không có |
| `deployment_change` | bool | candidate root có `components.recent_change_support.value > 0` |
| `checkout_impacted` | bool | `"checkout-service" in affected_services` |
| `db_lock` | bool | có candidate hạ tầng DB (RDS/PostgreSQL) với `log_support` hoặc `anomaly_strength` > 0 |

Ngưỡng 750 ms và 5% đọc từ `configs/slo_health_policy.json`, không hardcode lần hai.

## 3. Catalog — `configs/runbook_catalog.json` + `contracts/runbook-catalog.schema.json` (lane `match`)

```json
{
  "catalog_version": "runbook-catalog/v1",
  "thresholds": {"auto_min_score": 0.80, "auto_min_margin": 0.15, "oncall_min_score": 0.50},
  "entries": [
    {
      "id": "payment-bad-deploy-rollback",
      "title": "Payment chậm sau deploy image mới → rollback về image known-good",
      "executable": true,
      "executor_runbook": "payment-rollback-known-good/v1",
      "allowed_severities": ["low", "medium", "high", "critical"],
      "required": {"root_service": "payment-service", "deployment_change": true, "rca_abstained": false},
      "features": [
        {"field": "modality", "equals": "latency", "weight": 0.30},
        {"field": "latency_band", "in": ["mild", "moderate"], "weight": 0.25},
        {"field": "db_lock", "equals": false, "weight": 0.20},
        {"field": "onset", "in": ["abrupt", "unknown"], "weight": 0.15},
        {"field": "checkout_impacted", "in": [true, false], "weight": 0.10}
      ],
      "manual_steps": ["..."]
    }
  ]
}
```

- Có đúng 5 entry. 1 entry `executable: true` là entry ở trên. 4 entry thủ công (`executable: false`, `executor_runbook: null`): `inventory-bad-deploy`, `payment-errors-no-change`, `db-lock-contention`, `checkout-latency-downstream`. Mỗi entry có `manual_steps` tiếng Việt cho engineer.
- Schema strict: `additionalProperties: false`, trọng số > 0, `field` thuộc tập khóa ở §2. Entry executable phải có `executor_runbook` nằm trong allow-list `{"payment-rollback-known-good/v1"}`.
- Mọi entry phải khai báo đủ bốn `allowed_severities`. Schema không cho `severity` trong `required` hoặc `features`, để catalog không biến severity thành điều kiện khớp.

```python
def load_catalog(path) -> dict              # validate schema + semantic, raise ValueError
def score(symptoms: dict, entry: dict) -> dict
    # {"id", "score": float 0..1, "required_failed": [field...], "matched": [field...]}
    # bất kỳ required sai → score 0.0
    # còn lại = tổng weight khớp / tổng weight
def rank(symptoms: dict, catalog: dict) -> list[dict]   # sort score giảm, tie theo id
def decide(symptoms: dict, catalog: dict) -> dict        # theo §1
    # {"decision_version": "runbook-decision/v1", "decision": "oncall|engineer",
    #  "runbook_id": str|None, "executor_runbook": str|None, "score": float|None, "margin": float|None,
    #  "reasons": [str], "ranking": rank(...)[:3], "symptoms": symptoms,
    #  "catalog_sha256": str, "fingerprint": str}
def fingerprint(symptoms: dict, runbook_id: str|None) -> str
    # stable_id của (root_service, modality, latency_band, deployment_change, checkout_impacted, runbook_id)
```

`margin` = điểm top − điểm runner-up; chỉ có một entry thì margin = top.

## 4. Severity (lane `sev`)

`configs/severity_policy.json` mã hóa policy gốc `severity/v1` theo `docs/architecture/CONSTRAINTS_AND_GATES.md` §4 và `group_severity` trên main trước commit `d1eee79`. Live và replay dùng chung `src/sentinelops/incidents/severity.py`:

- **critical:** hai bucket Checkout mới nhất đều eligible, cách nhau 60 giây, và mỗi bucket có `error_rate >= 0.20`.
- **high:** group có Checkout bị ảnh hưởng **hoặc** có ít nhất hai service bị ảnh hưởng, khi chưa đạt critical. Đếm affected services, không đếm structural roots.
- **medium:** phần còn lại. `low` vẫn là severity hợp lệ cho watch-level record nếu được mở theo policy; hàm này không tạo band LOW theo latency.
- Không dùng ngưỡng p95 để phân severity, không chọn ngưỡng theo lỗi demo. Payment p95 khoảng 981 ms kéo theo Checkout là **high**, vẫn được đề xuất runbook nếu đủ điểm và RCA không abstain.
- `group_severity(...)` giữ chữ ký. Policy load một lần, hash `stable_id(policy)` ghi vào kết quả `severity_policy_sha256` và log. SLO health policy vẫn xác định suy giảm; các band latency ở §2 chỉ phục vụ khớp runbook.

## 5. Executor hook + controller (lane `ctl`)

`src/sentinelops/remediation/executor.py`:

```python
RollbackExecutor(..., authorize: Callable[[Request], Awaitable[None]] | None = None)
# gọi ngay sau self._enabled() trong _prepare, và ngay sau self._enabled() trước kube.patch(dry_run=False).
# authorize raise Blocked('<reason>') để chặn. None = hành vi cũ (mọi test cũ phải pass nguyên).
```

`src/sentinelops/remediation/controller.py`:

```python
class AutoRunbookController:
    def __init__(self, *, incidents, evidence, executor, catalog, kill_switch: Path,
                 store, clock=..., max_actions_per_tick=1): ...
    async def tick(self) -> list[dict]:
        # 1. incident RCA_READY chưa có decision
        # 2. extract_symptoms → decide
        # 3. ghi decision bất biến: store.write('manifests', decision) + log JSON
        # 4. oncall ở mọi severity → proposal bất biến + event runbook_proposal, chưa gọi executor
        # 5. tick sau: approval hợp lệ → Request + executor.execute()
        #    rồi executor.reconcile() tới terminal ở các tick sau
    def authorize(self, request) -> Awaitable[None]:
        # kiểm lại approval + revision + severity + toàn bộ decision hiện tại;
        # phải còn oncall và khớp proposal; severity tăng giữa chừng thì Blocked trước PATCH
```

`scripts/run_remediation_controller.py`:

- Chạy vòng `tick()` mỗi 10 s.
- Tham số: `--catalog`, `--kill-switch` (file tồn tại thì disabled), `--policy-enabled` (bắt buộc truyền tường minh mới bật `Policy(enabled=True)`), và các tham số kết nối giống `scripts/run_remediation_worker.py`.
- `worker.py` (resume-only) giữ nguyên.

## 5b. Proposal và approval bất biến

- Proposal là decision `oncall` trong `manifests`, thêm `proposal_version=runbook-proposal/v1`, `proposal_id=stable_id([incident_id, incident_revision, runbook_id])`, `created_at`, `expires_at=created_at+15 phút`. Giữ nguyên `runbook_id`, `executor_runbook`, `score`, `margin` và toàn bộ quyết định làm bằng chứng.
- Proposal ghim bốn trường dưới đây từ nguồn server, trước khi ghi artifact. Tên trường giữ ổn định cho lane UI; không lấy target/image từ payload operator hay LLM.

| Trường | Kiểu và nội dung | Nguồn |
| --- | --- | --- |
| `target` | Object gồm `cluster_uid`, `namespace`, `namespace_uid`, `deployment`, `deployment_uid`, `container` | Target của policy, đối chiếu với Deployment quan sát được và known-good ledger |
| `current_image` | Chuỗi image URI đầy đủ, bắt buộc `repository@sha256:<64 hex>` | Image của Deployment hiện tại |
| `rollback_image` | Chuỗi image URI đầy đủ, bắt buộc `repository@sha256:<64 hex>` | Image approved trong known-good ledger, được resolve theo thời điểm onset của incident |
| `known_good_ledger_ref` | Chuỗi `artifact://manifests/<sha256>.json` | Reference của đúng ledger đã resolve cho `rollback_image` |

- Không đọc/verify được target hoặc ledger khi tạo proposal thì ghi decision `engineer`, lý do `proposal-target-unavailable`, không phát hành proposal thiếu các trường này.
- Log JSON `event=runbook_proposal` chứa proposal cùng `decision_ref`. Tick tạo proposal không dispatch, kể cả có receipt sẵn.
- On-call đọc proposal rồi chạy lệnh trong pod controller (đường dẫn store là đường dẫn **trong pod**, mặc định triển khai `/artifacts/remediation`):

```sh
kubectl -n sentinelops-b-actions exec deploy/remediation-controller -- \
  python scripts/approve_runbook.py --store /artifacts/remediation \
  --proposal-id <proposal_id> --approved-by <ten-oncall> \
  --decision approve --reason 'Đã kiểm tra bằng chứng và runbook' \
  --confirm-oncall-approval
```

- Có thể thay `approve` bằng `reject`. Danh tính bắt buộc, không rỗng; cờ xác nhận bắt buộc. CLI chỉ ghi artifact, không gọi Kubernetes/AWS hay executor.
- Approval `runbook-approval/v1` ghi `proposal_id`, `proposal_sha256` của toàn proposal, `approved_by`, `approved_at`, `decision`, `reason`. Một proposal chỉ nhận một receipt, không ghi đè hoặc đổi từ reject sang approve. Quyền ghi volume/controller exec phải giới hạn cho on-call; `approved_by` là audit identity do người vận hành nhập.
- `proposal_sha256` bao gồm cả `target`, `current_image`, `rollback_image`, `known_good_ledger_ref`; receipt không cần lặp lại các trường này hay đổi version. Proposal cũ thiếu bất kỳ trường ghim nào không được dispatch, kể cả đã có approval.
- Artifact được kiểm hash; slot create-only trong `runbook-approvals/{approval,used,outcome}` được công bố bằng hard link nguyên tử. Claim `used` được ghi bền trước `execute`, chỉ execution hiện tại mới dùng claim đó trong hook; khởi động lại không tái sử dụng approval. Crash có thể mất một lượt chạy và cần engineer xử lý; không tự retry mutation.
- Chỉ approval `approve` đúng ID/hash, thời điểm hợp lệ, chưa dùng mới được xét. Hook kiểm lại ngay trước PATCH thật: incident revision không đổi và quyết định tính lại vẫn khớp toàn bộ proposal, bao gồm severity. Controller lấy severity cao nhất giữa kết quả đã verify, incident hiện tại và health snapshot mới nhất; severity tăng làm proposal cũ không còn khớp và bị chặn trước PATCH. Kill switch và các guard executor vẫn áp dụng ở mọi severity.
- Trước dispatch và trong hook ngay trước PATCH thật, controller đọc lại Deployment và resolve lại known-good ledger. Target/UID, `current_image`, `rollback_image` hoặc ledger reference khác proposal, hay ledger không còn verify được, đều chặn và ghi escalation `approval-target-changed`. Ledger mới có cùng rollback digest vẫn làm approval cũ hết hiệu lực. Hook còn đối chiếu các trường này với `intent.evidence.before` và `intent.evidence.known_good` của executor: ledger đổi rồi trở về giá trị cũ vẫn bị chặn nếu executor đã chuẩn bị theo ledger khác. Không sửa proposal/receipt để khớp target mới.
- Reject ghi escalation `approval-rejected`; quá hạn ghi `phase=escalated`, `reason=approval-expired`. Revision/severity/decision thay đổi đều bị chặn. Outcome/log escalation được ghi riêng, không sửa proposal/approval hay lịch sử action terminal.
- Owner chọn freshness 900 giây cho policy mới để operator có đủ thời gian phản ứng; validator giữ 180 giây để đọc action lịch sử với policy/hash gốc. TTL proposal 15 phút tính từ lúc tạo proposal, còn freshness tính từ RCA/health/Deployment observation; vì vậy TTL chưa hết vẫn có thể bị chặn. Executor chặn bằng chứng quá ngưỡng policy; controller ghi escalation `approval-after-evidence-freshness` liên kết action bị chặn, giữ nguyên reason gốc trong action journal. Khoảng bất định receipt deployment change vẫn tối đa 180 giây.

## 6. EKS (lane `eksctl`)

- `scripts/render_worker_manifests.py` thêm Deployment `remediation-controller`: 1 replica, image `control-plane`, SA `payment-rollback`, `SENTINELOPS_KUBERNETES_REMOTE_SERVER=https://kubernetes.default.svc`, CA của cluster, token SA. Phải tương thích kiểm tra token "owned private regular file, không symlink" ở `remediation/kubernetes.py:77`. Projected token mặc định là symlink, nên phải dùng cách hợp lệ (ví dụ init container copy token, hoặc theo cách PR #70 đã làm).
- RBAC giữ đúng `resourceNames: [payment-service]`.
- `scripts/run_runbook_demo.py`:
  - Mỗi lượt: kiểm known-good ledger → deploy `payment-regression` → chờ proposal `oncall` → in lệnh duyệt cho on-call → chờ action `recovered` + `telemetry_verified` → xuất receipt `artifacts/aws-demo/runs/<n>.json`.
  - Mặc định không tự duyệt. Chỉ khi người dùng truyền tường minh `--approve-as <tên>` mới ghi approval tự động cho tập dượt (vẫn qua controller và mọi guard).
  - Chạy 3 lượt, cách nhau ≥ cooldown 600 s.
  - Có cờ `--kill-switch-run` để chứng minh 0 mutation.

## 7. Test bắt buộc
Container Python 3.11:

- `pytest -q` toàn bộ (không được giảm số test pass).
- Test mới của từng lane.
- `python scripts/validate_contracts.py`.
