# SentinelOps — Ràng buộc, quyết định và tiêu chí nghiệm thu

| Thuộc tính | Giá trị |
| --- | --- |
| Phiên bản | 1.1 — Architecture constraints and provisional quality targets |
| Ngày | 2026-10-25 |
| Baseline đã đọc | `main@20edcae` |
| Phạm vi | Ràng buộc kiến trúc/dữ liệu/vận hành và gate G0–G8 |
| Trạng thái | Chưa có gate được xác nhận PASS bởi lần lập tài liệu này |

Tài liệu này đi cùng [DELIVERY_PLAN.md](DELIVERY_PLAN.md) và [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md). Các target mới là **đề xuất nội bộ**, không phải điểm đã đo hoặc tiêu chí chính thức của cuộc thi.

## 1. Phân loại và quy tắc quyết định

| Loại | Ý nghĩa | Cách thay đổi |
| --- | --- | --- |
| F — Frozen | Quyết định đã có trong PRODUCT_SPEC/ADR Accepted | ADR mới trước implementation nếu đổi trách nhiệm/ý nghĩa |
| C — Constraint | Quy tắc correctness, leakage, safety hoặc compatibility cần enforce | Review tác động; không giảm âm thầm để demo pass |
| P — Proposed | Thiết kế, target hoặc config trong bộ tài liệu mới | Ghi owner, lý do, version và gate freeze trước evaluation |
| U — Unknown | Chưa có nguồn/bằng chứng đủ | Ghi rõ, không tự biến thành giả định đã xác nhận |

Status gate: `NOT_RUN`, `IN_PROGRESS`, `PASS`, `FAIL`, `BLOCKED`. `PASS` phải có artefact/evidence refs, commit/config và người review. Checklist có sẵn hoặc CI workflow được khai báo không tự là PASS.

Thứ tự xử lý mâu thuẫn: xác định requirement/source liên quan → kiểm tra wire contract đang được consumers dùng → ghi quyết định/migration → ADR nếu breaking → producer/consumer tests → cập nhật tài liệu. Không chọn một ví dụ trong spec để âm thầm làm invalid payload hiện tại.

## 2. Ràng buộc kiến trúc đã chốt

| ID | Ràng buộc MUST | Nguồn | Bằng chứng implementation cần có |
| --- | --- | --- | --- |
| F-01 | Pipeline chia trách nhiệm telemetry → ML → correlation/RCA → IER → explanation/remediation | ADR-001 | Module/data flow + integration tests |
| F-02 | Isolation Forest là primary anomaly detector; statistical method giữ vai trò control/fallback nghiên cứu | ADR-002 | Per-service model bundle + baseline report |
| F-03 | XGBoost là degradation-risk model; không hứa fixed failure time | ADR-002, PRODUCT_SPEC §5.5 | Label protocol, risk/lead-time evaluation |
| F-04 | Inference tại t chỉ dùng thông tin quá khứ/hiện tại | ADR-002 | Cutoff/arrival-aware replay tests |
| F-05 | RCA deterministic/scored, output là suspected causes và evidence refs | ADR-003 | Scoring config, ablation, refs validator |
| F-06 | IER là authoritative incident result | ADR-001/004 | Revisioned IER và linked projections |
| F-07 | Bedrock chỉ giải thích facts có sẵn; không quyết root, sửa metric hay gọi infra tools | ADR-004 | Bounded input, output validation, permission checks |
| F-08 | LLM fail/invalid/timeout phải có deterministic fallback | ADR-004 | Provider-failure và invalid-output evidence |
| F-09 | Auto-action chỉ qua approved runbook, least privilege, blast radius, idempotency, cooldown | ADR-005 | Policy/RBAC/audit + safety tests |
| F-10 | Action phải verify bằng telemetry; fail phải rollback hợp lệ hoặc escalate | ADR-005 | Post-action samples và failed-verification path |
| F-11 | Kubernetes restart/HPA đơn thuần không được tính là closed-loop SentinelOps | ADR-005 | Action do controller khởi tạo, có incident/audit link |
| F-12 | v1 AWS single region/cluster; không tự sửa commerce data hoặc IAM policy/role | PRODUCT_SPEC §1.3 | Deployment boundary + denied-operation tests |
| F-13 | CloudWatch Metrics/Logs và X-Ray là canonical AWS observability interfaces | PRODUCT_SPEC, STEP3_OBSERVABILITY | AWS source refs và end-to-end capture |
| F-14 | Threshold/window/weights/hyperparameters là tunable; required fields/responsibility/safety semantics là architecture changes | README change policy | Versioned config, ADR khi breaking |

## 3. Dữ liệu, metric và mô hình

| ID | Ràng buộc MUST | Acceptance/negative case |
| --- | --- | --- |
| C-D01 | UTC, units và event-time/window boundaries được định nghĩa/version | Boundary fixtures; không mix seconds với milliseconds |
| C-D02 | Rate dùng counter deltas/reset handling; p95 aggregate distribution đúng | Known-request fixture + counter reset + multi-pod case |
| C-D03 | Health/internal traffic bị loại khỏi business SLO/model queries | Query/feature fixtures không tăng khi chỉ gọi `/health` |
| C-D04 | N=0, missing source và stale data không được coi là zero errors/healthy | No-traffic/missing/stale tests; UNKNOWN metadata |
| C-D05 | Không fabricate CPU/memory/DB signals; optional missing vẫn thiếu | Missingness report, provenance đủ cho mỗi số |
| C-D06 | Raw/normalized/features tách riêng; immutable dataset/run/model IDs có hashes | Mutation detection; reproduce từ manifest |
| C-D07 | Serving không đọc fault truth/scenario target/intensity/future breach | Feature allow-list, IAM/module boundary và leakage audit |
| C-D08 | Run/time split; services và overlapping windows cùng run không sang split khác | Split intersection check và session/purge rule |
| C-D09 | Preprocessing fit trên train; calibration/threshold chọn trên validation | Train/calibration manifest refs; test không được dùng để tune |
| C-D10 | Batch/online/replay dùng cùng feature implementation và feature version | Same-input parity và recorded runtime versions |
| C-D11 | Availability time không bị bỏ qua khi đo online lead time/MTTD | Arrival-aware replay; analytical results ghi nhãn riêng |
| C-D12 | Source/environment shift cần compatibility/evaluation trước model activation | Local/AWS parity report; không silent reuse incompatible bundle |
| C-D13 | `risk_score` không gọi calibrated probability khi chưa có calibration evidence | Model card/UI wording và calibration report nếu claim probability |
| C-D14 | Statistical output không giả là Isolation Forest trong frozen schema | No-model path không trả fake `model_name`/healthy score |
| C-D15 | Final holdout khóa; xem/tune trên test phải khai báo và tạo holdout mới | Evaluation manifest + access/run history |
| C-D16 | Báo independent runs và uncertainty, không coi overlapping windows là độc lập | Run-level denominators/bootstrap |
| C-D17 | RDS infra observations không giả HTTP signals để thỏa TelemetryPoint | Separate infra contract và real database evidence |

## 4. Hợp đồng, incident và evidence

| ID | Ràng buộc MUST | Acceptance/negative case |
| --- | --- | --- |
| C-E01 | Existing strict JSON Schemas giữ nguyên shape nếu chưa có ADR/migration | Current samples và consumer fixtures vẫn validate |
| C-E02 | Metadata/timeline/lifecycle/action details dùng supplemental contracts | Không thêm unknown fields vào payload đang `additionalProperties:false` |
| C-E03 | Evidence IDs unique; mọi ranking/explanation ref resolve trong đúng IER revision | Unknown/duplicate/cross-revision refs bị reject |
| C-E04 | IER revision/lifecycle cập nhật atomic theo expected revision | Concurrent updates không overwrite nhau |
| C-E05 | Source refs có timestamp/range và evidence snapshot khi source hết retention | Release bundle vẫn kiểm chứng được sau live source expiry |
| C-E06 | Score/ranking deterministic cùng inputs/config; scores không tự là causality proof | Replay/ablation report và uncertainty labels |
| C-E07 | Không gộp independent faults chỉ theo cùng thời gian/shared Checkout | S5 distinguishing-evidence cases |
| C-E08 | LLM text không ghi ngược vào evidence/ranking/approval | Permission/data flow test |
| C-E09 | Explanation invalid/stale không trình bày như current verified output | Fallback/revision UI cases |
| C-E10 | Severity và SLO do policy xác định, không do LLM | Versioned policy + boundary tests |

Severity policy đề xuất: `critical` khi Checkout ≥20% 5xx trong hai eligible buckets; `high` khi objective incident ảnh hưởng Checkout hoặc ít nhất hai business services; `medium` khi một service degrading nhưng chưa đạt critical/high; `low` cho watch-level record nếu được mở theo policy. Policy phải xử lý precedence rõ, không suy ra severity chỉ từ anomaly score.

## 5. Remediation, quyền và chi phí

| ID | Ràng buộc MUST | Acceptance/negative case |
| --- | --- | --- |
| C-R01 | `remediation_enabled=false` mặc định; enable có actor/config/audit | Disabled state không dispatch mutation |
| C-R02 | Chỉ approved runbook + allow-listed target/namespace/resource identity | Wrong namespace/UID/image/runbook bị block |
| C-R03 | Client `approved=true` hoặc LLM recommendation không cấp quyền | Forged approval payload không vượt policy |
| C-R04 | Evidence freshness/confidence/known-good/blast-radius/dry-run kiểm tra ngay trước action | Stale revision hoặc changed target bị block |
| C-R05 | Persistent idempotency và per-target lock; retries/crashes được reconcile | Concurrent/duplicate/crash test không tạo uncontrolled repeat |
| C-R06 | Audit/intent phải durable trước side effect; DB failure chặn dispatch | Không mutate khi không persist được action |
| C-R07 | Success phải có rollout readiness và đủ telemetry sau action | Patch 200 hoặc một clean request không kết luận recovered |
| C-R08 | Verify thiếu dữ liệu/timeout là inconclusive/escalated, không success | Drop telemetry/stop traffic test |
| C-R09 | Failed rollback không tự tái deploy known-bad image | Failed-verification path giữ safe target/escalate |
| C-R10 | Controller không cluster-admin, không IAM write/commerce data write | IAM/RBAC review và denied-operation evidence |
| C-R11 | Internal fault/admin/exporter endpoints không public trên AWS | Network/auth review và external reachability check |
| C-R12 | Secrets ngoài git; local dev DSN không reuse trên AWS | Secret handling và config review |
| C-R13 | Region/quota/budget/teardown owner được xác nhận trước tính phí | IaC plan/cost review và budget configuration |
| C-R14 | Action/model/dataset/config changes có version và audit | Release manifest resolve được mọi component |

## 6. Default cấu hình đề xuất để thử nghiệm

| Nhóm | Default | Freeze point |
| --- | --- | --- |
| Bucket/window/step | 60s / 300s / 60s | G1, trước dataset freeze |
| Coverage/traffic | ≥4/5 buckets, ≥30 requests/eligible bucket | G1/G2 sau healthy feasibility |
| Source lateness | Local 30s; AWS đề xuất 120s, phải đo | G1 local, G7 AWS |
| SLO | p95 >750ms hoặc 5xx >5%, hai eligible buckets liên tiếp | Trước label/dataset freeze G2 |
| Severity | Policy ở §4, version riêng | G2 |
| Correlation window | 120s với dependency/trace/change support | G4 development, trước holdout |
| RCA weights | 0.25 anomaly + 0.20 time + 0.25 dependency + 0.15 log + 0.15 change | G4 development, trước holdout |
| Action eligibility | RCA ≥0.75, top-2 margin ≥0.15, evidence/policy đủ | G6 development, trước final demo/evaluation |
| Action freshness | Latest required observation ≤180s | G6/G7; nếu source lag không đáp ứng thì block |
| Verify | Ba post-action eligible buckets liên tiếp, timeout 10m | G6 |
| Cooldown/attempt | 10m; một auto-attempt/incident/target | G6 |
| Random seed | 42 cho primary reproducibility; supplementary seeds ghi riêng | G2/G3 |

Các thresholds anomaly/risk được fit/chọn bằng validation theo metric budget; không coi giá trị demo là đã tối ưu. Thay feature/SLO/source semantics sau freeze phải tạo dataset/model/config versions tương ứng và đánh giá lại.

## 7. Metric definitions

| Metric | Định nghĩa và mẫu số |
| --- | --- |
| Event detection recall | Số eligible ground-truth incident events có alert đúng trong matching interval / tổng eligible incident events |
| Event precision | Số emitted incident alerts match ground-truth event / tổng emitted incident alerts; duplicate alerts thừa vẫn tính penalty |
| Event F1 | Harmonic mean của event precision và recall; matching một-một theo policy đã freeze |
| False incidents/service-hour | Unmatched incident alerts / eligible healthy service-hours; đồng thời báo wall-clock hours và phần bị UNKNOWN |
| MTTD | Alert emission wall-clock time trừ independently measured incident/SLO onset; báo median/p90 và missed-event count |
| PR-AUC risk | Area dưới precision-recall curve trên eligible pre-incident/healthy windows; báo positive prevalence, run counts và run bootstrap |
| Precursor recall | Gradual heldout runs có sustained risk trước breach / tổng gradual runs có breach; no-breach runs báo riêng |
| Lead time | Breach event time trừ risk alert emission time; báo phân phối cùng missed/late/no-breach counts, không chỉ chọn ca dự báo thành công |
| RCA top-1/top-3 | Đúng ground-truth component trong top-k / mọi evaluated single-root incidents, kể cả abstained/missed diagnosis |
| RCA MRR | Mean reciprocal rank của true root, 0 nếu không có; multi-root scenarios báo riêng bằng set/grouping metrics |
| Abstention rate | Incidents engine không đủ căn cứ chọn root/action / evaluated incidents; không loại khỏi denominator để nâng accuracy |
| Recovery success | Actions đạt full post-action verify / mọi dispatched eligible actions; blocked/escalated counts báo riêng |
| Time to recovery | SLO onset tới thời điểm recovery được xác nhận; báo thêm action-effective→verify-complete |
| Unsafe action count | Mọi mutation vượt target/approval/blast-radius/quyền hoặc thiếu mandatory prechecks |
| Explanation validity | Accepted outputs có schema/refs/numbers/entities hợp lệ / provider outputs; báo reject/fallback rates riêng |
| Cost | AWS usage/cost theo run và environment uptime, gồm idle/training/observability; không ước lượng thay invoice/usage evidence |

Event matching interval, grouping và deadline phải được config trước đánh giá. Trước onset, alert chỉ được tính precursor ở risk evaluation nếu đủ protocol; không backdate emitted time. Missed events không biến thành MTTD=0 hoặc bị lặng lẽ bỏ khỏi report.

Q-04/Q-05 đo detector IF cùng persistence, tách statistical/SLO-rule outputs. Q-06 báo MTTD riêng cho detector và combined operational pipeline, kèm trigger sources. Một incident được SLO rule phát hiện không tự được tính là IF true positive; risk results được báo riêng theo gradual protocol.

Recovery comparison dùng repeated matched no-action controls với traffic/fault profile tương đương và cùng measurement boundaries. Không dùng một lần manual fault reset làm đối chứng để tuyên bố giảm MTTR nhờ controller.

## 8. Targets nội bộ đề xuất và điều kiện sử dụng

Targets chốt tại G0 sau review khả thi; có thể điều chỉnh sau pilot G2 với lý do ghi rõ **trước** model comparison/final holdout. Không hạ target vì đã xem final test fail. Báo confidence intervals và N; target prototype không tạo SLA production.

| ID | Target ban đầu | Phạm vi/bằng chứng cần |
| --- | --- | --- |
| Q-01 | 100% accepted records hợp schema/semantic rules; invalid records quarantine | Deterministic fixture và live capture; raw invalid counts vẫn được báo |
| Q-02 | Eligible data coverage ≥95% thời gian có traffic trong supported runs | Báo riêng UNKNOWN/no-traffic/source-outage intervals |
| Q-03 | Replay cùng frozen environment có identical IDs/grouping và scores sai khác ≤1e-6 | Same input/config/model/runtime; bound khác phải ghi justification |
| Q-04 | Event recall ≥0.85, precision ≥0.80 và false incidents ≤1/service-hour | S1–S4, supported traffic; bootstrap theo run |
| Q-05 | IF event F1 không thấp hơn MAD control ở cùng false-alert budget | Cùng split/features/persistence; ghi cả chênh lệch và uncertainty |
| Q-06 | MTTD p90 ≤300s sau SLO onset | Arrival-aware/live; gồm source lag; báo missed events riêng |
| Q-07 | XGBoost PR-AUC cao hơn prevalence và fixed slope/persistence control; precursor recall ≥0.70 với lead ≥60s | Chỉ gradual heldout runs; báo actual alert budget và mọi misses/no-breach |
| Q-08 | RCA top-1 ≥0.80, top-3 ≥0.95 | S1–S4 single-root, denominator gồm abstentions; S5 đánh giá grouping riêng |
| Q-09 | ≥9/10 golden-path action trials phục hồi; 100% failed-verify trials có escalation/audit | Local/AWS trials báo riêng, không gộp tùy ý để pass |
| Q-10 | Unsafe action count =0; toàn bộ mandatory safety cases pass | Negative/crash/concurrency tests và audit review |
| Q-11 | 100% accepted explanation outputs pass bounded schema/ref/value validators | Không được diễn giải thành bảo đảm semantic truth trên mọi input |
| Q-12 | Required gate reports có đầy đủ versions, denominators, sources và limitations | Reviewer resolve được evidence từ manifest |

Minimum release data ở TECHNICAL_SPEC là collection floor, không bảo đảm uncertainty đủ hẹp. Nếu N nhỏ hoặc interval quá rộng, ghi kết quả **provisional** và bổ sung run; không tuyên bố độ chính xác production/generalization ngoài test envelope.

## 9. Non-functional requirements đề xuất

| ID | Requirement/target | Cách đo |
| --- | --- | --- |
| NFR-01 | Internal inference API p95 ≤500ms khi model resident | Đo ít nhất 100 requests trên ghi rõ hardware/concurrency; loại source fetch latency nhưng báo riêng |
| NFR-02 | Process một closed service window ≤10s | End-to-end worker timing; source available lag báo riêng |
| NFR-03 | Worker restart không mất accepted incident/action intent | Crash/restart + durable checkpoint/outbox evidence |
| NFR-04 | Side-effect retries không tạo uncontrolled duplicates | Unique keys, target lock và observed-state reconciliation tests |
| NFR-05 | Bedrock unavailable không ngắt detection/RCA/incident read | Fault test với fallback và UI |
| NFR-06 | Secrets/PII không có trong model input, public reports hoặc application errors | Field allow-list, redacted fixtures, permission review |
| NFR-07 | IaC và manifests tái tạo được môi trường, image/config có version | Clean-environment deploy record và teardown procedure |
| NFR-08 | Current state và immutable audit/evidence revisions truy vấn được | Read/list/revision API + retention check |
| NFR-09 | Chi phí nằm trong budget do owner chốt | Budget cap, usage report, resource inventory, teardown owner |
| NFR-10 | Các service/model/worker phát own health, lag, latency/error metrics | Dashboard/query refs; không có high-cardinality IDs trong labels |

Chưa có availability SLA production hoặc throughput claim đã xác nhận. Load profiles thử nghiệm đề xuất 1/3/5 checkout requests/giây, phải warmup và đo healthy feasibility trước dùng để gắn nhãn fault.

## 10. Gate checklist và evidence bắt buộc

| Gate | Điều kiện entry | Điều kiện PASS | Evidence tối thiểu |
| --- | --- | --- | --- |
| G0 — Baseline | Code/config baseline hiện tại | Existing contract/API/observability tests và real Docker smoke pass; owners, blocking decisions có kết luận | Baseline command logs, commit/images/runtime, trace/log/metric snapshots, decision log |
| G1 — Telemetry/features | G0 | Q-01 và phần feature determinism của Q-03 pass; raw→feature lineage, true p95/ratios, cutoff/late/no-traffic xử lý đúng | Feature registry, schemas mới, fixtures, parity/quality report; model determinism hoàn thiện ở G3 |
| G2 — Dataset | G1 | Development dataset có ≥300 healthy train và ≥100 healthy calibration windows/service, gradual labels trong train/validation; run/time split/leakage checks pass; dataset/split/labels/SLO freeze | Run inventory, checksums, split report, label protocol, exclusion reasons; pilot thiếu điều kiện giữ IN_PROGRESS |
| G3 — Models | G2 | Statistical/IF/XGB development comparison, versioned bundle, source compatibility và no-model/error paths pass | Fold/validation metrics với denominators, model cards/manifests, replay outputs |
| G4 — Correlation/RCA/IER | G3; repository phần mềm có thể làm sớm | Durable IER revisions, semantic refs, lifecycle, cascade/independent grouping và abstention đúng | API/contract/concurrency tests, RCA ablation/development report, evidence-linked IERs |
| G5 — Explanation/UI | G4 | Evidence/explanation tách rõ, fallback/revision/auth/error cases pass | UI walkthrough, validator adversarial fixtures, provider/fallback outputs |
| G6 — Local closed loop | G4 + local Kubernetes | Actual Payment image rollback, all safety cases pass, Q-09/Q-10 local | Known-good ledger, action/audit logs, Kubernetes before/after, post-action telemetry |
| G7 — AWS integration | G5/G6 + budget/quota/permission ready | CloudWatch/X-Ray adapters/source quality, AWS model compatibility/training, real action+verify+escalation | IaC/deploy logs, CW/X-Ray/S3 refs, SageMaker/model manifest, RBAC/IAM matrix, cost |
| G8 — Release/evaluation | G7 + frozen release + release dataset minimum | Relevant Q/NFR targets trên untouched holdout hoặc limitation/fail được ghi rõ; không sign off full acceptance nếu gate fail | Final metrics/CI/E2E, denominators/CI, immutable release manifest, demo/operator guide |

Trước P11, G3/G4 metrics là development evidence. Q-04…Q-08 final claims chỉ được xác nhận từ heldout evaluation G8. Các actions trials Q-09 và model holdout report ghi rõ tập run; không dùng outcome test để điều chỉnh model/RCA thresholds rồi vẫn gọi là untouched holdout.

### 10.1 Evidence bundle convention — PROPOSED

```text
artifacts/<release-or-run-id>/
  manifest.json
  environment.json
  commands.log
  checksums.sha256
  telemetry/             raw/normalized/feature refs hoặc captures
  predictions/           detector/risk outputs với emitted times
  incidents/             IER revisions, timeline, RCA detail
  actions/               policy/execute/verify/audit
  evaluation/            metrics.json, run-level table, uncertainty
  reports/               human-readable findings/limitations
```

`artifacts/` là local ignored output; S3 là archive AWS. Git lưu sanitized manifests, configs và reports cần review, không lưu raw/model binaries/secrets. Path trên là convention đề xuất, chưa phải files đã được tạo.

Mọi gate report ghi `NOT_RUN/PASS/FAIL/BLOCKED`, execution time/environment, commit/config hashes, người chạy/review và link evidence. Nếu không chạy được Docker/AWS, ghi đúng phạm vi đã kiểm tra; static validation không thay E2E.

## 11. Decision register cần chốt

| ID | Vấn đề | Đề xuất cụ thể | Status khi lập tài liệu | Owner / hạn chốt |
| --- | --- | --- | --- | --- |
| D-01 | Wire format của schema khác ví dụ PRODUCT_SPEC (`1.0`, `service`, `state` vs examples) | Giữ schemas hiện tại; dùng envelopes/contracts bổ sung; ADR nếu thực sự cần breaking migration | ACCEPTED 2026-10-26, ADR-007 | A + P review / G0 |
| D-02 | Versioning rule "ignore optional unknown" mâu thuẫn strict `additionalProperties:false` | Giữ strict v1; wrapper mới có version; không tự đổi rule compatibility | ACCEPTED 2026-10-26, ADR-007 | I + A/P / G0 |
| D-03 | Kho time series local | Prometheus scrape Collector, retention 7 ngày | ACCEPTED 2026-10-26, ADR-006 | P / G0 |
| D-04 | Incident/action persistence AWS chưa được chốt | PostgreSQL control DB/schema + S3 objects; tách commerce role; không suy từ DynamoDB placeholder | ACCEPTED 2026-10-26, ADR-006 | P + A / G0 |
| D-05 | Timestamp/window/SLO/quality defaults | Theo §6 và technical spec; tune từ healthy/pilot trước dataset freeze | PROPOSED | A + P / G1–G2 |
| D-06 | P95 AWS không được giả định từ arbitrary histogram statistic sets | CloudWatch Logs rollup → canonical CW metric; source-profile spike và parity | PROPOSED | P + A / design G0, proof G7 |
| D-07 | RDS không có meaningful HTTP latency/error bắt buộc bởi telemetry schema | InfrastructureObservation riêng; business IF baseline chỉ ba services | ACCEPTED 2026-10-26, ADR-007 | A + P / G0 |
| D-08 | Rich lifecycle/timeline/remediation chưa có trong IER schema | Versioned IncidentEnvelope; explicit coarse-state projection | ACCEPTED 2026-10-26, ADR-007 | A / G0–G4 |
| D-09 | Storage/deployment/process topology chi tiết | Modular Python control plane, API/worker processes riêng; workload services giữ riêng | ACCEPTED 2026-10-26, ADR-006 | P + A / G0 |
| D-10 | Golden remediation action | `payment-rollback-known-good/v1`, immutable digest, one target, no scale/data mutation | ACCEPTED 2026-10-26, ADR-009 | P + A / G0, policy freeze G6 |
| D-11 | Metric targets và supported load envelope | Q-01…Q-12, benchmark 1/3/5 rps sau feasibility | PROPOSED | I + A/P / G0, final freeze G2 |
| D-12 | Region, Bedrock model, AWS quota/budget | Chọn từ actual account availability; chưa xác nhận `ap-southeast-1` hoặc model access | UNKNOWN | P + I / trước cloud spend |
| D-13 | Owner names, capacity và deadline | Gán hai lane và reviewer, calendar thực tế | UNKNOWN | I / G0 |
| D-14 | Rules/DOCX nguồn và competition scoring | Bổ sung authoritative files/links, tạo traceability matrix; TechX chỉ tham khảo | UNKNOWN | I / trước compliance/release sign-off |
| D-15 | Targets về availability, public access và retention chính thức | Private demo access mặc định; SLA production để ngoài scope; retention/budget được chốt | PROPOSED/UNKNOWN | I + P / G0/G7 |

Không cần chốt mọi UNKNOWN để đọc/review kế hoạch. Chỉ công việc phụ thuộc quyết định đó mới bị chặn. Khi quyết định được chốt, thêm ngày, actor, rationale và reference; không sửa PROPOSED thành Accepted mà không có bằng chứng review.

## 12. Điều kiện giảm scope hoặc hoãn release

- Nếu data quality/lineage hoặc split sai: dừng model evaluation, sửa pipeline và tạo dataset version mới.
- Nếu learned model chưa đạt control: báo kết quả trung thực, tiếp tục cải thiện theo validation; không đổi model family/nhãn chỉ để giữ câu chuyện demo.
- Nếu root ambiguity/evidence freshness không đạt: giữ detection/UI/evidence, chặn automatic runbook và escalate; không coi đây là full closed-loop acceptance.
- Nếu Bedrock không available: deterministic explanation giữ operator flow; đánh dấu phần Bedrock integration chưa đạt khi chưa có bằng chứng thật.
- Nếu AWS budget/quota/quyền chưa sẵn: tiếp tục local/replay work độc lập; không gọi local evidence là AWS PASS.
- Nếu thời hạn ngắn hơn estimate: ưu tiên một golden path và safety proof, giảm polish/UI extras/model search; mọi acceptance bị dời ghi rõ trong release limitations.

G8 full acceptance yêu cầu các gate bắt buộc đạt. Một báo cáo có limitation được bàn giao vẫn hữu ích nhưng không được gọi là toàn bộ MVP đã nghiệm thu.
