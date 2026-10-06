# ADR-004 — Grounded LLM boundary

**Status:** Accepted

Amazon Bedrock is used only for human-readable incident explanation.

The LLM:

- accepts only precomputed Incident Evidence Records / bounded fact cards;
- may not change metric values or timestamps;
- may not decide the root cause;
- may not create operational evidence;
- may not call Kubernetes/AWS remediation actions;
- must return structured claims with evidence references.

Invalid output, unknown evidence references, timeout, or model failure falls back to a deterministic summary.

**Rule:** Evidence is computed; LLM explanation is generated. Never the reverse.
