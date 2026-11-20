# ADR-004 — Grounded LLM boundary

**Status:** Accepted

Amazon Bedrock is used only for human-readable incident explanation.

The LLM:

- accepts only precomputed Incident Evidence Records / bounded fact cards;
- may not change metric values or timestamps;
- may synthesize a human-readable root-cause narrative only for a ranked RCA hypothesis;
- may not introduce a service, evidence reference or certainty outside that hypothesis;
- may not create operational evidence;
- may not call Kubernetes/AWS remediation actions;
- must return structured claims with evidence references.

Invalid output, unknown evidence references, timeout, or model failure falls back to a deterministic summary.

**Rule:** Evidence and RCA ranking are computed; the LLM explains the selected
hypothesis in human language. The narrative never replaces RCA ranking or
authorizes an action.
