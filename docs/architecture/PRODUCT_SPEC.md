# SentinelOps — Product and architecture baseline v1.1

| Property | Value |
| --- | --- |
| Status | Accepted implementation baseline |
| Updated | 2026-10-26 |
| Authorization | Repository owner requested replacement of outdated documentation with the implementation architecture in this chat |
| Source baseline | v1.0 + ADR-001…005; source reviewed at `20edcae` |
| Historical record | [Archived v1.0](../archive/PRODUCT_SPEC_v1.0.md) |

## 1. Product outcome

SentinelOps provides evidence-driven incident detection, diagnosis and controlled recovery for an AWS microservices testbed. Each incident must answer: what is abnormal, which component is suspected, which evidence supports it, what customer impact exists, and whether a permitted recovery can be verified.

The closed loop is Observe → Detect/Predict → Correlate/Diagnose → Record → Explain → Remediate → Verify. An implemented capability and an evaluated capability must be reported separately.

## 2. Authoritative documentation

| Topic | Authority |
| --- | --- |
| Product scope and responsibility boundaries | This document and Accepted ADRs |
| Runtime data/API shape | Versioned JSON Schemas in `contracts/`; OpenAPI for newly implemented routes |
| Implementation design and default parameters | [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md) |
| Constraints, target metrics, gates, decision status | [CONSTRAINTS_AND_GATES.md](CONSTRAINTS_AND_GATES.md) |
| Delivery order, owners by role, estimates, backlog | [DELIVERY_PLAN.md](../delivery/DELIVERY_PLAN.md) |
| Runtime evidence and known failures | Current environment checks, version-matched CI and required data/model evaluation artifacts; routine PASS logs need no separate report |

The previous v1.0 export omitted several tables and contained examples inconsistent with committed schemas. It is retained as history, not as a second implementation specification. Accepted architecture changes require an ADR; tuning parameters and unmeasured quality targets remain explicitly provisional until their stated freeze gates.

## 3. Architecture invariants

- Isolation Forest is the primary anomaly detector, trained per business service on healthy training windows. A robust statistical control is retained for comparison.
- XGBoost scores gradual degradation from past/current telemetry. Risk is not a promised time-to-failure or a calibrated probability without evidence.
- Incident correlation groups supported related signals; deterministic RCA ranks suspected causes using anomaly, time, dependency/traces, logs and actual change events.
- The versioned Incident Evidence Record is authoritative. Generated language cannot become diagnostic evidence or authorization.
- Amazon Bedrock only explains bounded facts and has deterministic fallback. It has no infrastructure tools or remediation credentials.
- Remediation uses approved runbooks with policy, prechecks, idempotency, blast-radius bounds, execution, telemetry verification and audit. Missing/ambiguous evidence defaults to escalation.
- Inputs at time t cannot include future observations or experiment truth. Experiment labels are stored outside serving inputs.

See [ADR index](../adr/README.md). ADR-001…005 remain applicable; ADR-006…009 define the implementation decisions added in v1.1.

## 4. Supported MVP scope

The monitored workload is Checkout → Payment/Inventory → PostgreSQL. PostgreSQL is the local substitute for RDS. The application exists to produce real dependency behavior; it is not a production commerce implementation.

Local development uses OTel, Collector, Jaeger, Prometheus history and structured log capture. AWS uses CloudWatch Metrics/Logs and X-Ray through source adapters. The processor creates normalized observations and quality-controlled feature windows. An infrastructure record represents RDS signals without fabricated HTTP metrics.

The control plane starts as a modular Python application with API and worker processes. PostgreSQL stores operational records, tasks, action intents and audits under credentials separate from commerce. Local filesystem/S3 object adapters retain datasets, models and captured evidence. Model training is offline and versioned, with reproducible AWS training in SageMaker.

The first automatic runbook restores a known-good immutable Payment image on Kubernetes. A genuine deployment change and subsequent telemetry recovery are required. Resetting a sandbox fault flag or observing a native pod restart does not satisfy this requirement.

## 5. Explicit non-goals

v1 does not deliver multi-region/multi-cluster/multi-cloud support, general autonomous SRE agents, LLM tool execution, automatic model retraining, automatic commerce-data changes or automatic IAM changes. Unknown/high-risk incidents remain observable and are escalated. A production availability SLA is outside the prototype acceptance claims.

## 6. Contract compatibility

Existing v1 payloads use `schema_version: "1.0"`, `service` and the enum values in committed JSON Schema. Consumers validate these strict payloads. New provenance, quality, rich lifecycle, timeline and execution metadata belong in separately versioned envelopes/contracts, not unknown fields inserted into existing payloads.

JSON Schema shape validation is supplemented by semantic validation: time bounds, finite values, evidence reference integrity, revision ordering, state transitions and policy authorization. Both producer and consumer fixtures are required for integration changes.

## 7. Data and evaluation requirements

Collect healthy runs before training, then repeated fault runs with multiple intensities/seeds. Keep raw, normalized and features separate and versioned. Split by experiment run/time before fitting preprocessing or thresholds; overlapping windows and services from one run cannot straddle splits.

The scenario catalog covers healthy/benign load, abrupt Payment faults, a bad Payment deployment, gradual degradation, real database pressure and independent concurrent faults. Failure labels and ground-truth targets remain outside serving.

Evaluation reports detector precision/recall/F1, false incidents/service-hour, MTTD, risk PR-AUC/lead time, RCA top-k/MRR/abstention, recovery outcomes, unsafe actions and cost. Model-only outputs and operational SLO-rule triggers are measured separately. Every claim includes denominators, versions, exclusions and uncertainty. Targets in the gate document are proposed internal targets, not official competition requirements or measured results.

## 8. Incident and action state

Internal health and incident lifecycle are richer than the current IER enum. The exact mapping to `degrading/incident/recovering/recovered/escalated` is specified in the technical spec, with revisioned context and audit outside the strict payload.

A successful API mutation is not recovery. Post-action verification requires the intended image/rollout and consecutive completed telemetry buckets with adequate traffic. Failed/missing verification results in escalation. A failed rollback never automatically redeploys the known-bad image.

## 9. Operator experience

The operator sees service health/staleness, incidents, official evidence/ranking/timeline, generated explanation and action history. Evidence and generated explanation are visibly distinct. A Bedrock outage must not remove the incident or prevent reading the authoritative record.

## 10. Delivery and current implementation status

Foundation API, local workload and OTel baseline exist in source. Their execution status is tracked in dated reports; source presence alone is not a PASS. Telemetry processing, models, RCA, UI and remediation become complete only when their gates pass with real evidence.

Delivery sequence is G0 baseline → G1 data pipeline → G2 dataset → G3 models → G4 RCA/IER → G5 explanation/UI and G6 local remediation → G7 AWS → G8 release. Interfaces and infrastructure design may proceed in parallel where the plan permits; no dependency gate is bypassed to fabricate progress.

## 11. Security and operating boundaries

Workloads, model workers, explanation and controller use separate permissions. Controller cannot mutate commerce data or IAM and cannot operate outside approved Kubernetes targets. Fault/admin/exporter endpoints are local/private, secrets are outside Git and observation data is redacted before prompts/reports.

Before paid AWS provisioning, region/model access/quota, budget, retention and teardown ownership must be established. Existing local credentials and `.env.example` placeholders do not decide AWS production settings.

## 12. Governance and remaining unknowns

The repository owner's 2026-10-26 request authorizes this architecture reconciliation and implementation/testing/push work. Technical decisions accepted in ADR-006…009 are recorded in the decision register. Numerical quality targets, AWS budget/quota/model access, named future team owners and competition rules provenance remain separate open items where not yet evidenced.

The original DOCX and authoritative AI4Life rule file are not present in this checkout. TechX references in archived v1.0 are engineering references, not binding competition rules. Competition compliance cannot be signed off solely from those references.
