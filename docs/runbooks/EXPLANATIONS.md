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
original evidence ID. The summary reports the displayed and total evidence counts,
so bounded selection is visible. Claims describe records, not independently verified causes.
Empty/oversized evidence produces an explicit limited summary, never invented
claims, a healthy score or action permission.

## Consumer wiring

```python
from sentinelops.explanations import explain_incident

result = await explain_incident(envelope)
```

This works without any provider or cloud access. B implements the authenticated
`GET /v1/incidents/{id}/explanation?revision=N`, loading the requested revision and
the [versioned response wrapper](../../contracts/explanation-response.schema.json) carrying
`incident_id`, `revision`, `generated_at`, `provider`, `fallback`, `payload`, as
specified in [H2](../delivery/WORKSTREAM_CONTRACT.md).
The [API/UI runbook](INCIDENT_API.md) documents auth, revision headers, failure
states and the read-only Explanation tab. The default mounted API uses this real
deterministic fallback, including while models are unavailable. This backend does
not implement HTTP, write EXPLAINED lifecycle transitions,
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

## Bedrock Converse adapter

`sentinelops.explanations.bedrock.bedrock_provider` adapts the
[AWS Converse protocol](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
to the same bounded selector. Its factory requires a region, exact model or
inference-profile ID and an explicitly supplied async operation:

```python
from sentinelops.explanations.bedrock import bedrock_provider

async def converse(**kwargs):
    return await runtime_client.converse(**kwargs)

provider = bedrock_provider(region=approved_region, model_id=approved_model_id,
                            converse=converse)
result = await explain_incident(envelope, provider=provider)
```

The application/deployment owns the asynchronous runtime client, its regional
endpoint, scoped IAM role, lifecycle, response byte limits, network timeouts and
disabled SDK retries. Do not pass a synchronous SDK method or disguise a blocking
call with `async def`. No AWS SDK is loaded and no request is sent by the factory.
The deployment must supply that client; the adapter alone does not demonstrate
cloud access. Model support/access, region, budget and authorization must be
checked before enabling it. Its role needs only approved model invocation,
without infrastructure/action mutation permissions.

The adapter sends system instructions and one metadata-only user message, with a
512-token output bound and no tool configuration. It accepts one assistant text
block ending with `end_turn`, parses strict JSON and rejects duplicate keys,
non-finite constants, extra blocks, tool requests, guardrail/truncated responses
and markdown. The core selector then checks exact incident/revision/cards. Provider
identity hashes region/model/prompt/instructions/inference parameters for version
tracking. Errors use the existing same-revision fallback. Tests of this protocol
do not prove Bedrock availability, explanation quality or G5 acceptance.

Adapter regression: `python -m pytest -q tests/test_explanation_bedrock.py`.
