# ADR-006 — Control plane and persistence boundaries

**Status:** Accepted  
**Date:** 2026-10-26  
**Authorization:** Repository owner requested adoption of the implementation architecture and replacement of obsolete documentation in this chat.  
**Related:** ADR-001, PRODUCT_SPEC v1.1, TECHNICAL_SPEC §3/11.

## Context

The baseline specifies pipeline responsibilities but leaves storage and deployment details empty. A service-per-stage design would introduce integration overhead before workload/data requirements are proven. Collector metric exposition does not retain queryable history.

## Decision

- Keep three independently runnable monitored services. Implement the control plane as domain modules in `sentinelops`, with API and background workers in separate processes sharing domain code.
- Use Prometheus for local metric history, Jaeger for local traces, and captured structured logs. Preserve CloudWatch Metrics/Logs and X-Ray as AWS canonical sources through adapters.
- Store incident revisions, lifecycle/events, durable tasks, model activations, action intents and audits in a control PostgreSQL database/schema with credentials separate from commerce.
- Store immutable raw/normalized/features/models/evidence in a filesystem object-store adapter locally and S3 on AWS, with checksums and versioned manifests.
- Keep training offline, reproducible, and separate from serving. SageMaker produces the AWS model artefacts; a separately billed hosted model endpoint is not mandatory.

## Consequences

The DynamoDB placeholder is not a selected persistence technology. Changing storage later requires an explicit migration/CAS/outbox review. No implementation may assume exactly-once execution across PostgreSQL and Kubernetes. Separate microservices remain possible when demonstrated load or permissions justify them; the responsibility split in ADR-001 is unchanged.
