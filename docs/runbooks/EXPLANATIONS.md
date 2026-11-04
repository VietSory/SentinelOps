# Grounded explanation backend — H2

`sentinelops.explanations.service.explain_incident(envelope, *, provider=None)`
is the async lane A entry point. Pass the validated, revision-pinned
`IncidentEnvelope` returned by the incident repository. The function validates
again and snapshots its input. It returns exactly:

```python
{"payload": grounded_explanation, "provider": provider_id, "fallback": bool}
```

`payload` follows the existing `explanation.schema.json`. No fields are added to
IER. The summary identifies the incident/revision, current state, severity,
availability and uncertainty. Claims quote recorded evidence verbatim with the
original evidence ID. They describe records, not independently verified causes.
Empty/oversized evidence produces an explicit limited summary, never invented
claims, a healthy score or action permission.

## Consumer wiring

```python
from sentinelops.explanations import explain_incident

result = await explain_incident(envelope)
```

This works without any provider or cloud access. B owns the authenticated HTTP
route, loading the requested revision and the versioned response wrapper carrying
`incident_id`, `revision`, `generated_at`, `provider`, `fallback`, `payload`, as
specified in [H2](../delivery/WORKSTREAM_CONTRACT.md).
This module does not implement that route, write EXPLAINED lifecycle transitions,
read credentials, cache responses or grant remediation authority. B must not
label a response from an older revision as current. Render text as text, including
quoted evidence; source evidence remains untrusted content.

## Optional provider transport

Create one reusable `ExplanationProvider(provider_id, invoke)` per configured
provider/model version. `invoke` is an async callable receiving a JSON-compatible
request with `prompt_version=incident-fact-selection/v1`, incident ID/revision,
state/severity/availability, affected services, structured service signals,
ranked hypothesis scores and at most 64 metadata cards. Each card contains
`card_id`, evidence type, service and observed time. Raw fact/log text, source
URIs, actor/reason, timeline and trigger references are excluded.

The transport returns exactly:

```json
{
  "incident_id": "<same input ID>",
  "revision": 1,
  "prompt_version": "incident-fact-selection/v1",
  "card_ids": ["evidence-00000"]
}
```

Return 1–12 unique card IDs from this request. The service restores source order
and renders the final GroundedExplanation locally. A provider selects evidence;
it cannot supply prose, change numbers, substitute refs, rerank RCA or authorize
actions. This is the spec's bounded fact/template rendering mode, with deliberately
limited narrative flexibility. Provider output with extra prose or stale identity
is rejected even if it sounds plausible. No general hallucination guarantee is
claimed for source evidence itself.

Default total deadline is 10 seconds, including queueing; concurrency is 4,
with no retries. Three failed/invalid responses open the per-instance circuit
for 30 seconds. These bounds are configurable within the adapter's explicit
limits. External cancellation propagates. The transport must cooperate with
cancellation and limit response bytes before parsing; this service additionally
checks the decoded selection against a 64 KB budget. Input metadata is capped
at 64 KB. Each rendered claim is at most 4 KB; larger facts are omitted whole,
never truncated through numeric/time tokens.

Missing provider, timeout, transport failure, invalid selection, oversized request,
unsupported service identifiers or open circuit uses the deterministic renderer
on the same revision. Structured `incident_explanation` logs record revision,
prompt version, requested/used provider and fallback reason outside the payload;
they exclude source facts and upstream exception text. Choose a versioned provider
ID containing the model/config version. No Bedrock transport or invocation is
enabled merely by importing this backend; region/model access, credentials and
cloud authorization remain separate integration requirements.

Focused regression: `python -m pytest -q tests/test_explanations.py`.
