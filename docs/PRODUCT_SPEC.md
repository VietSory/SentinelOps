# SentinelOps Product & Technical Specification v1.0

**Status:** Approved baseline — Architecture & Contract Freeze.

This Markdown companion mirrors the decision baseline in the DOCX. The DOCX is the review-friendly artifact; once approved, the repo should store this specification plus machine-readable contracts.

XÂY DỰNG HỆ THỐNG AIOPS CHO PHÁT HIỆN SỚM
VÀ PHỤC HỒI SỰ CỐ CLOUD-NATIVE MICROSERVICES

SentinelOps | Team Xbrain | AI4Life 2026 | AWS-based implementation baseline

Version: 1.0 – Approved Baseline
Status: Architecture & Contract Freeze
Date: 06/10/2026


# 0. Document Control


## 0.1 Source reconciliation

Bản spec này hợp nhất các quyết định đã xuất hiện trong đề cương AWS chi tiết, script thuyết trình 16 slide, thể lệ AI4Life 2026 và các tài liệu TechX Phase 2/Phase 3 đã được dùng làm tham chiếu kiến trúc. Tài liệu TechX chỉ là nguồn tham khảo về cách đặt contract, evaluation và safety; không được coi là yêu cầu chính thức của AI4Life.


## 0.2 Resolved ambiguities


# 1. Product Definition


## 1.1 Product vision

SentinelOps is an AWS-based operations intelligence system for cloud-native microservices. Its job is not merely to raise an alert. For each incident, the system must produce a traceable operational answer: what is abnormal, which component is the most likely root suspect, what evidence supports that ranking, what the current impact is, and whether an approved recovery action can be executed safely.

The product follows a closed operational loop: Observe → Detect/Predict → Diagnose → Explain → Remediate → Verify. Each stage produces structured output consumed by the next stage; no single AI model is allowed to infer the entire incident end-to-end.


## 1.2 Product outcomes

- Detect multi-signal service anomalies using a per-service learned normal profile rather than a single global static threshold.
- Identify gradual degradation risk from historical behavior and recent telemetry trends using XGBoost.
- Correlate cascading signals into one incident and rank a suspected root service using topology, traces, timing and log evidence.
- Create an Incident Evidence Record that is the authoritative incident result and can be audited back to original evidence.
- Use Amazon Bedrock only to translate the evidence-backed incident result into concise human-readable explanation.
- Automatically execute at least one approved recovery runbook with pre-check, blast-radius guard, verify, rollback/escalation and audit.

## 1.3 Non-goals for v1

- No multi-cloud, multi-region or multi-cluster product.
- No general-purpose autonomous SRE agent and no LLM tool-calling into Kubernetes/AWS.
- No automatic modification/deletion of application data in RDS and no IAM policy/role modification.
- No promise to predict every abrupt failure before it happens.
- No automatic model retraining pipeline; model training is controlled and versioned.
- No attempt to auto-remediate every incident class. Unknown or high-risk cases must escalate.

# 2. Architecture Invariants (Frozen Decisions)

The following are architecture invariants. They are intentionally stricter than ordinary implementation preferences because they protect the project from major rewrites after development starts.


## 2.1 Tunable versus breaking changes


# 3. End-to-End System Flow


```text
User traffic
   |
   v
ALB -> EKS: Checkout -> Payment -> RDS
             |          \-> Inventory -> RDS
             |
             +--> OpenTelemetry instrumentation
                      |
              +-------+--------+
              |                |
        CloudWatch         AWS X-Ray
       metrics + logs        traces
              |                |
              +-------+--------+
                      v
              Telemetry Processor
                      v
                Feature Windows
                 |           |
                 v           v
         Isolation Forest   XGBoost
          anomaly score   degradation risk
                 \           /
                  v         v
             Incident Correlation
                      v
                  RCA Engine
                      v
          Incident Evidence Record
             |                 |
             v                 v
        Bedrock Explain   Policy/Runbook Match
             |                 |
             v                 v
           UI/API       Remediation Controller
                               |
                               v
                        Verify telemetry
                         /           \n                    Recovered    Rollback/Escalate
```

Training is offline from the online inference path. Historical normalized windows and experiment manifests are written to S3. SageMaker is the AWS training environment for reproducible Isolation Forest and XGBoost training jobs. Model artifacts are versioned and loaded by the online AI Engine.


# 4. AWS Reference Architecture


## 4.1 Namespace and permission separation


# 5. Component Specifications


## 5.1 Experimental workload

The workload is deliberately small but must contain real dependencies that can produce a cascade. Checkout orchestrates the customer-facing request. Payment depends on RDS. Inventory depends on RDS. Checkout depends on Payment and Inventory. Each service exposes a health endpoint and emits trace context on outbound calls.


## 5.2 Telemetry Processor

Telemetry Processor is the normalization boundary between AWS observability sources and the ML/RCA pipeline. It does not make incident decisions. Its output is deterministic, versioned and replayable.

- Normalize service name, environment, timestamp and experiment/run identifiers.
- Query/receive metrics and compute aligned time windows per service.
- Extract log-derived counters/templates; retain source references for later evidence display.
- Retain trace/span references and dependency edges for RCA; trace evidence does not need to enter the ML vector in v1.
- Produce FeatureWindow objects and write historical copies to S3.

## 5.3 Feature Pipeline

v1 uses fixed-size sliding windows. The window duration and step are configuration parameters. The feature definitions are frozen at category level; exact aggregation intervals may be tuned after healthy-run observation.


## 5.4 Anomaly Detection — Isolation Forest

One Isolation Forest model is trained per service on healthy historical windows. It produces an anomaly score normalized to the product contract. A robust statistical baseline (rolling median/MAD or equivalent) is maintained only as a control/fallback for evaluation, not as a replacement for the chosen model.


## 5.5 Degradation Risk — XGBoost

XGBoost is a supervised degradation-state model. It does not promise a fixed “failure in X minutes” forecast. Slow-degradation experiment runs are divided into healthy, degrading and incident phases using experiment ground truth for labels only. Online inference sees only current/recent telemetry features.


## 5.6 Incident Correlation

Correlation groups related abnormal signals into one incident before RCA. A configurable correlation window and service topology are used to avoid creating one alert per metric. Multiple independent incidents remain separate even if their time windows overlap.


## 5.7 Context-aware RCA Engine

RCA v1 is deterministic evidence scoring and ranking. It is intentionally not an LLM. It combines independent evidence sources so that a downstream service with many errors is not automatically treated as the root cause.

The RCA output is a ranked suspect list with evidence references. Exact weights are configuration parameters and may be tuned through replay experiments; the evidence dimensions and output contract are frozen.


## 5.8 Incident Evidence Record (IER)

IER is the central domain object and the product source of truth. UI, Bedrock explanation, remediation planning and evaluation all consume the same record or a versioned projection of it.


## 5.9 Grounded Incident Explanation — Amazon Bedrock

Bedrock is a language rendering layer over verified incident facts. It improves operator comprehension and handoff; it does not own diagnosis.


## 5.10 Controlled Remediation

Remediation is rule/policy controlled. v1 acceptance requires one concrete closed-loop action driven by Xbrain AIOps, not Kubernetes native self-healing alone.


## 5.11 Operator UI

The UI must show two distinct surfaces: authoritative incident evidence and generated explanation. This separation must be visible, not only described in documentation.


# 6. Contract Pack (Freeze Candidate)

These contracts are the integration surface. After v1.0 approval, required fields and semantic meaning are frozen. Optional fields may be added in a backward-compatible manner.


## 6.1 Telemetry Contract


## 6.2 FeatureWindow Contract


```text
{
  "schema_version": "feature-window/v1",
  "window_start": "2026-10-06T10:15:00Z",
  "window_end": "2026-10-06T10:20:00Z",
  "service_name": "payment-service",
  "features": {
    "request_rate_mean": 121.4,
    "p95_latency_ms_mean": 438.0,
    "p95_latency_ms_slope": 31.2,
    "error_rate_mean": 0.032,
    "timeout_count": 14,
    "cpu_mean": 62.1,
    "memory_mean": 71.4,
    "db_connections_pct": 87.0,
    "log_error_count": 21
  },
  "evidence_refs": ["METRIC-...", "LOG-..."]
}
```


## 6.3 Detection API Contract


## 6.4 RCA Contract


```text
POST /v1/rca
Input:
{
  "incident_candidate_id": "IC-...",
  "signals": [...],
  "topology_snapshot": {...},
  "trace_evidence": [...],
  "log_evidence": [...],
  "change_events": [...]
}

Output:
{
  "root_cause_ranking": [
    {"service":"rds","score":0.89,"evidence_refs":["E-101","E-104"]},
    {"service":"payment-service","score":0.58,"evidence_refs":["E-107"]}
  ],
  "rca_engine_version": "rca/v1"
}
```


## 6.5 Incident Evidence Record Contract


```text
{
  "schema_version": "ier/v1",
  "incident_id": "INC-0014",
  "status": "OPEN",
  "severity": "HIGH",
  "detected_at": "2026-10-06T10:20:00Z",
  "affected_services": ["payment-service", "checkout-service"],
  "service_signals": [...],
  "root_cause_ranking": [...],
  "evidence": [
    {
      "id": "E-101",
      "type": "metric",
      "service": "rds",
      "fact": "DB connection utilization reached 94%",
      "source_ref": "cloudwatch:..."
    }
  ],
  "timeline": [...],
  "remediation": {...}
}
```


## 6.6 Explanation Contract


```text
{
  "incident_id": "INC-0014",
  "summary": "...",
  "claims": [
    {
      "text": "RDS degradation preceded Payment timeouts.",
      "evidence_refs": ["E-101", "E-104"]
    }
  ],
  "model_provider": "amazon-bedrock",
  "validation": {"passed": true, "errors": []}
}
```


## 6.7 Remediation Contract


## 6.8 Contract versioning rules

- Major version changes only for breaking fields or semantics.
- Every request/record carries schema_version or API version in the path.
- Consumers must ignore unknown optional fields, but must reject missing/invalid required fields.
- Mock fixtures for every contract are committed under contracts/fixtures and used by integration tests.
- A contract change is not considered merged until producer and consumer tests pass.

# 7. Data & ML Experiment Specification


## 7.1 Experiment run manifest

Every fault/healthy run has one immutable manifest stored separately from model features. This gives reproducible ground truth without leaking it into serving.


```text
scenario_id: rds-conn-gradual-v1
run_id: run-2026-10-06-001
start_at: ...
fault_start_at: ...
slo_breach_at: ...
recovery_at: ...
target: rds
root_cause_ground_truth: rds
fault_method: load-script
intensity_profile: gradual
slo_config_version: slo/v1
change_id: null
notes: ...
```


## 7.2 Dataset lifecycle

1. Collect stable healthy runs first; use them for feature validation and Isolation Forest training.
1. Generate repeated fault runs at multiple intensities; store raw/normalized/features as separate S3 prefixes or versioned datasets.
1. Freeze a dataset version before model comparison. Do not regenerate the same dataset ID with different records.
1. Split by run/time. Hold out at least one run or intensity per key scenario for evaluation.
1. Keep a replay harness capable of reading a dataset/run and reproducing detector/RCA output without live AWS traffic.

## 7.3 Baseline and model comparison


## 7.4 Required evaluation metrics


# 8. Incident State Machines


## 8.1 Service health state


```text
HEALTHY
  -> DEGRADING   (sustained risk/anomaly + precursor evidence)
  -> INCIDENT    (objective incident/SLO condition)
DEGRADING
  -> HEALTHY     (hysteresis/cooldown passes)
  -> INCIDENT
INCIDENT
  -> RECOVERING  (approved action executed or natural recovery observed)
RECOVERING
  -> HEALTHY     (verification window passes)
  -> INCIDENT    (verification fails)
```

State thresholds and persistence durations are configuration values. State transition semantics are frozen. A single one-off spike must not open/close incidents without persistence/hysteresis logic.


## 8.2 Incident lifecycle


```text
OPEN -> RCA_READY -> EXPLAINED(optional)
  -> MITIGATION_PLANNED
  -> MITIGATING
  -> VERIFYING
       -> RECOVERED -> CLOSED
       -> ROLLED_BACK -> ESCALATED
       -> ESCALATED
```


# 9. Fault & Validation Scenario Catalog


## 9.1 Scenario execution protocol

1. Warm up workload and capture healthy baseline before fault start.
1. Start experiment manifest and telemetry capture; ground-truth metadata is stored out-of-band.
1. Inject fault using the declared method; do not manually alter model outputs or incident state.
1. Capture model signal stream, incident records, RCA and remediation/audit events.
1. Allow recovery/rollback and continue capture through the verification window.
1. Re-run the same scenario enough times to avoid presenting one synthetic curve as proof.

# 10. Security, Safety & Trust Boundaries

All AWS access uses dedicated roles/workload identities and short-lived credentials. No static AWS keys are committed. Sensitive values go through AWS-native secret handling or Kubernetes secrets backed by an approved secret mechanism. LLM input is field-whitelisted and must not include secrets or unnecessary user data.


## 10.1 Kill switches and safety defaults

- Global remediation_enabled=false must be able to disable all automatic actions without disabling detection/RCA.
- Per-runbook enable/disable and max frequency/cooldown.
- Per-target replica/namespace/version bounds.
- Idempotency key/correlation ID prevents duplicate action execution.
- Unknown incident type, missing evidence or policy uncertainty defaults to escalation rather than action.

# 11. Repository, Deployment & CI/CD Specification


## 11.1 Repository structure


```text
xbrain-aiops/
├── apps/
│   ├── checkout-service/
│   ├── payment-service/
│   ├── inventory-service/
│   └── operator-ui/
├── services/
│   ├── telemetry-processor/
│   ├── ai-engine/              # Isolation Forest + XGBoost serving
│   ├── incident-rca-service/
│   ├── explanation-service/
│   └── remediation-controller/
├── ml/
│   ├── features/
│   ├── training/
│   ├── evaluation/
│   └── artifacts/
├── contracts/
│   ├── jsonschema/
│   ├── openapi/
│   └── fixtures/
├── experiments/
│   ├── scenarios/
│   ├── manifests/
│   ├── replay/
│   └── results/
├── infra/
│   ├── terraform/
│   └── helm/
├── docs/
│   ├── adr/
│   ├── runbooks/
│   └── architecture/
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    └── e2e/
```


## 11.2 Technology baseline


## 11.3 CI quality gates

- Unit tests for feature transforms, model wrappers, RCA scoring, policy and validators.
- JSON schema/OpenAPI contract validation against committed fixtures.
- Replay smoke test on a tiny healthy + incident dataset.
- Container build and health check.
- Terraform fmt/validate and Helm render/lint.
- No merge of a breaking contract change without ADR reference.

# 12. Four-Week Internal Execution Plan


## 12.1 Daily build discipline

- Every feature merges with test + sample/replay evidence; avoid branches that accumulate a week of unintegrated work.
- Keep one stable demo scenario always runnable while adding new capabilities.
- Record every architecture-impacting choice as ADR immediately, not at the end.
- At the end of each week, freeze a tagged checkpoint and run the E2E replay suite.

# 13. Team Working Agreement & Ownership

The source materials confirm two team members but do not define a technical ownership split. Therefore this spec defines role lanes rather than assigning a person without team confirmation.

Before implementation, the team should assign one named owner and one reviewer for each lane. Ownership means accountable for integration and evidence, not working alone.


## 13.1 Definition of Ready (DoR)

- Requirement has an owner, input/output contract and acceptance test.
- Required telemetry/evidence exists or a mock fixture is defined.
- Security/remediation permissions are understood before implementation.
- Any breaking architectural impact has an ADR draft.

## 13.2 Definition of Done (DoD)

- Code merged to main with tests and reproducible configuration.
- Contract fixtures pass producer/consumer tests.
- Feature works in replay and, when applicable, on AWS testbed.
- Observability exists for the new component itself.
- Evidence/result is saved to the experiment output and can be shown in demo.
- Documentation/ADR updated if behavior changed.

# 14. Acceptance Gates


# 15. Risk Register & Fallback Plan


# 16. Demo Acceptance Scenario

The final demonstration should tell one continuous story rather than show unrelated screens. The preferred golden path is S2 (bad Payment deployment), because it exercises telemetry, ML, change context, RCA, evidence, explanation and a reversible remediation.

1. System starts healthy; dashboard shows Checkout/Payment/Inventory and baseline status.
1. Deploy a known bad Payment version and record the change event.
1. Telemetry begins to deviate; Isolation Forest raises anomaly and incident correlation opens one incident.
1. RCA combines Payment anomaly, deployment event, traces/logs and topology; Payment/bad deployment is ranked as root suspect with evidence IDs.
1. Incident Evidence Record appears in UI. Bedrock explanation summarizes the same result beside official evidence.
1. Policy matches the approved rollback runbook; pre-check/dry-run/blast-radius pass; controller rolls back to known-good version.
1. Verification window shows latency/error recovery; incident becomes RECOVERED and audit history shows full chain.
1. A second prepared run intentionally fails verification to demonstrate rollback/escalation behavior.

# 17. Traceability to AI4Life Evaluation


# 18. ADR Backlog (Must Be Written During Build)


# Appendix A. Configuration Keys

The example numbers above are defaults for code scaffolding only. They are not competition claims and must be tuned from healthy/fault experiments before final reporting.


# Appendix B. Reference Basis

The following external materials influenced engineering discipline. They are not automatically binding AI4Life requirements.

AI4Life 2026 competition rules — Local uploaded rule document. Core scoring emphasizes feasibility, originality, technology fit, functionality, effectiveness, solution quality, documentation and presentation.

TechX Phase 2 capstone announcement — https://github.com/TechX-Corp/xbrain-learners/blob/main/capstone-phase2/W11_W12_capstone_announcement.md

TechX TF3 Self-Heal reference — https://github.com/TechX-Corp/xbrain-learners/blob/main/capstone-phase2/reference/TF3_SELFHEAL_LEARNER.md

TechX TF4 Foresight reference — https://github.com/TechX-Corp/xbrain-learners/blob/main/capstone-phase2/reference/TF4_FORESIGHT_LEARNER.md

TechX Phase 3 AIOps detection standard — https://github.com/TechX-Corp/xbrain-learners/blob/main/phase3/mandates/MANDATE-15-aiops-detection-standard.md

TechX Phase 3 closed-loop mitigation — https://github.com/TechX-Corp/xbrain-learners/blob/main/phase3/mandates/MANDATE-22-closed-loop-mitigation.md

TechX Phase 3 RCA standard — https://github.com/TechX-Corp/xbrain-learners/blob/main/phase3/mandates/MANDATE-26-rca-root-cause.md

TechX Phase 3 sustained incident standard — https://github.com/TechX-Corp/xbrain-learners/blob/main/phase3/mandates/MANDATE-28-sustained-incident.md


# Appendix C. v1.0 Approval Checklist

- Team agrees the 3-service workload and RDS dependency are final for MVP.
- Team agrees CloudWatch Metrics/Logs + X-Ray are the canonical observability interfaces for v1.
- Team agrees Isolation Forest and XGBoost model roles will not change without ADR.
- Team agrees RCA, not LLM, owns root-cause ranking.
- Team agrees Incident Evidence Record is the authoritative source of truth.
- Team agrees Bedrock is explanation-only and has deterministic fallback.
- Team agrees one automatic closed-loop remediation is mandatory and safety controls cannot be removed.
- Team assigns named owners/reviewers for Platform lane and AI/Data lane.
- Contracts under Section 6 are converted to committed JSON Schema/OpenAPI files.
- After all items pass, rename this document to v1.0 and tag the repository spec-freeze-v1.

---

> Note: Detailed decision/contract/acceptance tables are maintained in the DOCX review artifact and should be converted into `contracts/` JSON Schema/OpenAPI files after v1.0 approval.
