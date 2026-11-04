import json

import pytest

from sentinelops.explanations.bedrock import bedrock_provider
from tests.test_explanations import envelope, run, selection


def response(value):
    return {"stopReason": "end_turn", "output": {"message": {
        "role": "assistant", "content": [{"text": json.dumps(value)}]}}}


def test_converse_uses_pinned_model_bounded_tokens_and_no_tools_or_raw_facts():
    calls = []
    async def converse(**kwargs):
        calls.append(kwargs)
        request = json.loads(kwargs["messages"][0]["content"][0]["text"])
        return response(selection(request))
    provider = bedrock_provider(region="us-east-1", model_id="configured.model-v1", converse=converse)
    result = run(provider)
    assert not result["fallback"]
    assert calls[0]["modelId"] == "configured.model-v1"
    assert calls[0]["inferenceConfig"] == {"maxTokens": 512, "temperature": 0}
    assert set(calls[0]) == {"modelId", "system", "messages", "inferenceConfig"}
    assert envelope()["payload"]["evidence"][0]["fact"] not in json.dumps(calls[0])
    assert run(provider)["payload"] == run()["payload"]


@pytest.mark.parametrize("change", [
    {"stopReason": "max_tokens"}, {"stopReason": "guardrail_intervened"}, {"stopReason": "tool_use"},
    {"output": None}, {"output": {"message": {"role": "user"}}},
    {"output": {"message": {"role": "assistant", "content": [{"text": "```json\n{}\n```"}]}}},
    {"output": {"message": {"role": "assistant", "content": [{"toolUse": {}}]}}},
    {"output": {"message": {"role": "assistant", "content": [{"text": "{}"}, {"text": "{}"}]}}},
    {"output": {"message": {"role": "assistant", "content": [{"text": '{"revision":1,"revision":2}'}]}}},
    {"output": {"message": {"role": "assistant", "content": [{"text": '{"revision":NaN}'}]}}},
    {"output": {"message": {"role": "assistant", "content": [{"text": "x" * 64_001}]}}},
])
def test_untrusted_converse_output_falls_back_without_using_any_model_text(change):
    async def converse(**kwargs):
        request = json.loads(kwargs["messages"][0]["content"][0]["text"])
        return {**response(selection(request)), **change}
    assert run(bedrock_provider(region="us-east-1", model_id="configured.model-v1", converse=converse)) == run()


@pytest.mark.parametrize("formatting", ["duplicate", "oversized", "markdown", "extra_block"])
def test_protocol_guards_reject_an_otherwise_valid_selection(formatting):
    async def converse(**kwargs):
        request = json.loads(kwargs["messages"][0]["content"][0]["text"])
        result = response(selection(request))
        content = result["output"]["message"]["content"]
        text = content[0]["text"]
        if formatting == "duplicate":
            # A permissive decoder would keep the final, valid revision and accept.
            content[0]["text"] = text.replace('"revision": 1', '"revision": 99, "revision": 1')
        elif formatting == "oversized":
            content[0]["text"] = " " * 64_001 + text
        elif formatting == "markdown":
            content[0]["text"] = "```json\n" + text + "\n```"
        else:
            content.append({"text": "unsupported additional reasoning"})
        return result
    assert run(bedrock_provider(region="us-east-1", model_id="configured.model-v1", converse=converse)) == run()


def test_region_model_and_async_transport_are_explicit_and_in_provider_identity():
    async def converse(**kwargs):
        raise RuntimeError("not invoked during configuration")
    a = bedrock_provider(region="us-east-1", model_id="configured.model-v1", converse=converse)
    b = bedrock_provider(region="us-east-1", model_id="configured.model-v2", converse=converse)
    assert a.provider_id != b.provider_id
    with pytest.raises(ValueError, match="async function"):
        bedrock_provider(region="us-east-1", model_id="configured.model-v1", converse=lambda **kwargs: {})
    with pytest.raises(ValueError, match="regional"):
        bedrock_provider(region="us-east-1", model_id="arn:aws:bedrock:eu-west-1:123456789012:inference-profile/one", converse=converse)
    with pytest.raises(ValueError):
        bedrock_provider(region="us-east-1", model_id="arn:aws:bedrock:us-east-1:123456789012:prompt/example", converse=converse)
