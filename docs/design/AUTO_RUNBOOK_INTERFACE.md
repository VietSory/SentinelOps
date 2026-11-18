# Auto runbook (LOW, match ≥ 0.80) — interface chung giữa các lane

Trạng thái: **Proposed**, ngày 2026-11-18. Đây là hợp đồng giữa các lane `sev`, `match`, `ctl`, `eksctl`. Lane nào cần đổi interface thì báo lead (Claude) trước, không tự đổi.

## 1. Quy tắc quyết định

| Điều kiện (xét theo thứ tự) | `decision` |
|---|---|
| Kill switch bật, RCA abstain, thiếu dữ liệu (UNKNOWN), hoặc lỗi bất kỳ khi trích triệu chứng | `engineer` |
| `severity != "low"` | `engineer` (lý do `severity-not-supported`) |
| Top entry có `score >= thresholds.auto_min_score` (0.80), `margin >= thresholds.auto_min_margin` (0.15), `executable == true`, `"low" in allowed_severities` | `auto` |
| Top entry có `score >= thresholds.oncall_min_score` (0.50) | `oncall` (đợt 2: chưa chạy, lane này chỉ ghi quyết định) |
| Còn lại | `engineer` |

- Score là điểm khớp **deterministic**, không phải xác suất. Không dùng LLM.
- `auto` không bỏ qua guard nào: executor vẫn kiểm H4, known-good, dry-run, 1 attempt, cooldown, kill switch.

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
      "allowed_severities": ["low"],
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

```python
def load_catalog(path) -> dict              # validate schema + semantic, raise ValueError
def score(symptoms: dict, entry: dict) -> dict
    # {"id", "score": float 0..1, "required_failed": [field...], "matched": [field...]}
    # bất kỳ required sai → score 0.0
    # còn lại = tổng weight khớp / tổng weight
def rank(symptoms: dict, catalog: dict) -> list[dict]   # sort score giảm, tie theo id
def decide(symptoms: dict, catalog: dict) -> dict        # theo §1
    # {"decision_version": "runbook-decision/v1", "decision": "auto|oncall|engineer",
    #  "runbook_id": str|None, "executor_runbook": str|None, "score": float|None, "margin": float|None,
    #  "reasons": [str], "ranking": rank(...)[:3], "symptoms": symptoms,
    #  "catalog_sha256": str, "fingerprint": str}
def fingerprint(symptoms: dict, runbook_id: str|None) -> str
    # stable_id của (root_service, modality, latency_band, deployment_change, checkout_impacted, runbook_id)
```

`margin` = điểm top − điểm runner-up; chỉ có một entry thì margin = top.

## 4. Severity (lane `sev`)

`configs/severity_policy.json` (`severity/v2`), cùng một implementation cho live và replay:

- **critical:** Checkout `error_rate >= 0.20` hai bucket eligible liên tiếp (giữ nguyên).
- **high:** Checkout `error_rate >= 0.05` **hoặc** Checkout `p95 > 1500` hai bucket liên tiếp, **hoặc** ≥ 2 structural root độc lập.
- **low:** mọi service non-Checkout bị ảnh hưởng có max p95 ∈ (750, 1250] **và** error_rate ≤ 0.05, và Checkout dưới ngưỡng high.
- **medium:** phần còn lại.
- `group_severity(...)` giữ chữ ký, trả về một trong 4 giá trị; policy load một lần, hash ghi vào kết quả (`severity_policy_sha256`) nếu chỗ đó không phá contract strict. Nếu phá contract thì chỉ log.

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
        # 4. auto → Request(incident_id, revision, idempotency_key=stable_id(incident, revision, runbook)[:32])
        #    → executor.execute(); rồi executor.reconcile() tới terminal ở các tick sau
    def authorize(self, request) -> Awaitable[None]:
        # tính lại severity + decision trên snapshot hiện tại; không còn 'auto' thì raise Blocked
```

`scripts/run_remediation_controller.py`:

- Chạy vòng `tick()` mỗi 10 s.
- Tham số: `--catalog`, `--kill-switch` (file tồn tại thì disabled), `--policy-enabled` (bắt buộc truyền tường minh mới bật `Policy(enabled=True)`), và các tham số kết nối giống `scripts/run_remediation_worker.py`.
- `worker.py` (resume-only) giữ nguyên.

## 6. EKS (lane `eksctl`)

- `scripts/render_worker_manifests.py` thêm Deployment `remediation-controller`: 1 replica, image `control-plane`, SA `payment-rollback`, `SENTINELOPS_KUBERNETES_REMOTE_SERVER=https://kubernetes.default.svc`, CA của cluster, token SA. Phải tương thích kiểm tra token "owned private regular file, không symlink" ở `remediation/kubernetes.py:77`. Projected token mặc định là symlink, nên phải dùng cách hợp lệ (ví dụ init container copy token, hoặc theo cách PR #70 đã làm).
- RBAC giữ đúng `resourceNames: [payment-service]`.
- `scripts/run_runbook_demo.py`:
  - Mỗi lượt: kiểm known-good ledger → deploy `payment-regression` → chờ decision `auto` → chờ action `recovered` + `telemetry_verified` → xuất receipt `artifacts/aws-demo/runs/<n>.json`.
  - Chạy 3 lượt, cách nhau ≥ cooldown 600 s.
  - Có cờ `--kill-switch-run` để chứng minh 0 mutation.

## 7. Test bắt buộc
Container Python 3.11:

- `pytest -q` toàn bộ (không được giảm số test pass).
- Test mới của từng lane.
- `python scripts/validate_contracts.py`.
