# Grounded explanation backend — H2

`sentinelops.explanations.service.explain_incident(envelope, *, provider=None)`
is the async lane A entry point. Pass the validated, revision-pinned
`IncidentEnvelope` returned by the incident repository. The function validates
again and snapshots its input. It returns exactly:

```python
{"payload": grounded_explanation, "provider": provider_id, "fallback": bool}
```

`payload` follows `explanation.schema.json` v2.0. No fields are added to IER.
The summary identifies the incident/revision, current state, severity,
availability and uncertainty. `root_cause` contains the selected service,
authoritative RCA score, a short explanation and supporting evidence IDs. When
Bedrock is active, this explanation is an AI synthesis of the ranked hypothesis
and recorded evidence; the score is not a probability. Claims quote recorded
evidence verbatim with the original evidence ID. The summary reports displayed
and total evidence counts. Claims describe records, not independently verified
causes.
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
request with `prompt_version=incident-root-cause-narrative/v2`, incident
ID/revision, state/severity/availability, affected services, structured service
signals, ranked root-cause hypotheses and at most 64 sanitized evidence cards.
Each card contains `card_id`, `evidence_id`, evidence type, service, observed
time and a bounded fact. Source URIs, actor/reason, timeline and trigger
references are excluded. The provider may explain a ranked hypothesis, but
cannot introduce a service, score, evidence reference or action.

The transport returns exactly:

```json
{
  "incident_id": "<same input ID>",
  "revision": 1,
  "prompt_version": "incident-root-cause-narrative/v2",
  "card_ids": ["evidence-00000"],
  "root_cause": {
    "service": "payments",
    "score": 0.91,
    "explanation": "Payments is the leading hypothesis because the recorded evidence points to it.",
    "evidence_refs": ["E001"]
  }
}
```

Return 1–12 unique card IDs from this request. `root_cause.service` must be a
ranked hypothesis or null; `score` must copy that hypothesis score; and
`evidence_refs` must be a non-empty subset of both the hypothesis refs and the
selected cards. A null service requires null score and no refs. The service
restores source order, validates the narrative and renders the final response.
Provider output with extra keys, stale identity, unsupported measurements,
unknown services or refs is rejected even if it sounds plausible. No general
hallucination guarantee is claimed for source evidence itself.

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
to a bounded root-cause narrative. Its factory requires a region, exact model or
inference-profile ID and an explicitly supplied async operation:

```python
from sentinelops.explanations.bedrock import bedrock_provider

async def converse(**kwargs):
    return await runtime_client.converse(**kwargs)

provider = bedrock_provider(region=approved_region, model_id=approved_model_id,
                            converse=converse)
result = await explain_incident(envelope, provider=provider)
```

The API can construct the runtime from `SENTINELOPS_BEDROCK_REGION` and
`SENTINELOPS_BEDROCK_MODEL_ID`. Both are required; omitting either keeps the
deterministic fallback. boto3 uses the process's AWS credential chain, with a
2-second connect timeout, 8-second read timeout and no SDK retries. The deployed
API role currently does not have Bedrock permission, and the separate explanation
role is not wired to that API process. Therefore the AWS path remains fallback
until a deployment explicitly connects a least-privilege explanation identity.
No AWS access or model invocation is enabled by this code change.

The adapter sends one bounded user message containing sanitized fact cards and
ranked RCA hypotheses. It asks the model for selected cards plus
`root_cause={service,score,explanation,evidence_refs}`. The service accepts only
a ranked service, the copied score, references from that hypothesis and selected
cards, and a short human-readable narrative. It rejects secrets, URLs,
instruction-like fact text, unknown services, unknown references, duplicate
keys, non-finite constants, extra blocks, tools, guardrail/truncated responses
and markdown. Provider identity
hashes region/model/prompt/instructions/inference parameters for version tracking.
Errors use the same-revision fallback, which labels the result as an unconfirmed
hypothesis. Tests of this protocol do not prove Bedrock availability, explanation
quality or G5 acceptance.

Adapter regression: `python -m pytest -q tests/test_explanation_bedrock.py`.

The runtime wiring is exercised without AWS credentials. A real Bedrock result
still requires an AWS account, model access and deployment verification; local
adapter tests do not prove those gates.

## Bật trên EKS dev

Dùng `kubectl` và `jq` với `AWS_PROFILE=console`, current context
`sentinelops-demo` và namespace `sentinelops-b-actions`. Owner tạo trước Secret
`sentinelops-llm` có khóa `SENTINELOPS_LLM_API_KEY` qua quy trình quản lý bí mật
của môi trường. Script chỉ kiểm tra tên khóa và gắn `secretKeyRef` bắt buộc vào
`operator-ui`.

Chuẩn bị `IMAGE` là image `operator-ui` đã push, dạng `repo@sha256:<digest>`,
`MODEL` là ID model được cấp quyền truy cập, và `STATE_DIR` là đường dẫn đến một
thư mục riêng cho lượt deploy. Chạy kiểm tra trước rồi mới bật chế độ ghi:

```bash
AWS_PROFILE=console LLM_DEPLOY_EXECUTE=0 bash scripts/aws_demo/deploy_llm_explanation.sh \
  --image "$IMAGE" --llm-model "$MODEL" --state-dir "$STATE_DIR"
AWS_PROFILE=console LLM_DEPLOY_EXECUTE=1 bash scripts/aws_demo/deploy_llm_explanation.sh \
  --image "$IMAGE" --llm-model "$MODEL" --state-dir "$STATE_DIR"
```

`--llm-base-url` mặc định là `https://api.openai.com/v1`. Chỉ dùng override
HTTPS với endpoint được phép nhận API key. Deploy dùng một JSON Patch trên
Deployment `remediation-controller` để đổi image `operator-ui`, đặt URL, model
và tham chiếu Secret, đồng thời xóa mục env
`SENTINELOPS_LLM_ACTOR_AUTHORIZATION` khỏi container nếu có. ConfigMap không
thuộc phạm vi của patch.

Chế độ ghi lưu `rollback.json` với quyền `0600`. File chứa image cũ và tối đa
bốn mục env LLM cũ của `operator-ui`, cùng `resourceVersion` và `generation`
trả về sau patch. Script từ chối deploy nếu tên env chứa `KEY`, `SECRET`,
`TOKEN` hoặc `AUTHORIZATION` mà mục đó dùng giá trị trực tiếp. Deployment dùng
`Recreate`, vì vậy rollout có thể làm gián đoạn cả Pod. Script chờ tối đa 300
giây rồi xác minh các mục env được quản lý và image của cả ba container trong
Pod sẵn sàng thuộc ReplicaSet vừa rollout.
Để xác nhận model thực sự được dùng, gọi endpoint explanation bằng yêu cầu đã
xác thực cho một incident/revision đã biết và kiểm tra `provider` cùng
`fallback: false`.

Nếu rollout lỗi sau khi patch được xác nhận hoặc cần quay về cấu hình cũ, dùng
đúng `STATE_DIR` của lượt deploy đó. Rollback khôi phục image và các mục env đã
lưu bằng một JSON Patch rồi xác minh rollout và Pod. Nếu `generation` đã thay
đổi, xem lại thay đổi trên Deployment trước khi dùng `rollback --force`. Nếu
patch chưa được xác nhận, kiểm tra Deployment trước khi quyết định cách khôi
phục. Script chỉ nhận snapshot có `generation` và `resourceVersion` đã xác nhận.

```bash
AWS_PROFILE=console LLM_DEPLOY_EXECUTE=0 bash scripts/aws_demo/deploy_llm_explanation.sh \
  rollback --state-dir "$STATE_DIR"
AWS_PROFILE=console LLM_DEPLOY_EXECUTE=1 bash scripts/aws_demo/deploy_llm_explanation.sh \
  rollback --state-dir "$STATE_DIR"
```
