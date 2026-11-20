# ADR-004 — Grounded LLM boundary

**Status:** Accepted

OpenAI Responses and Amazon Bedrock are optional providers for human-readable
incident explanation. Configure at most one provider. Deployment tooling defaults
the Responses base URL to `https://api.openai.com/v1` when enabled. Local API
configuration requires the explicit URL, model and key. Leave the optional gateway
actor header (`SENTINELOPS_LLM_ACTOR_AUTHORIZATION`) unset for direct OpenAI calls.

The Responses adapter requests `text.format.type=json_schema`. OpenAI returned
HTTP 400 for `json_object` when the input lacked the word `json`. The adapter
requests an object with `strict: false` and the application validates the output
and evidence references locally. See `scripts/render_api_manifests.py:46`,
`src/sentinelops/api_settings.py:54` and `src/sentinelops/explanations/openai_compatible.py:14`.

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
