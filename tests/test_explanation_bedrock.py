import asyncio
import json

import pytest

from sentinelops.explanations.bedrock import Boto3BedrockRuntime, bedrock_provider, bedrock_runtime_provider
from tests.test_explanations import envelope, run, selection


def response(value):
    return {"stopReason": "end_turn", "output": {"message": {
        "role": "assistant", "content": [{"text": json.dumps(value)}]}}}


def test_converse_uses_pinned_model_bounded_tokens_and_sanitized_facts():
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
    assert envelope()["payload"]["evidence"][0]["fact"] in json.dumps(calls[0])
    assert "source_ref" not in json.dumps(calls[0])
    assert run(provider)["payload"]["claims"] == run()["payload"]["claims"]
    assert run(provider)["payload"]["root_cause"]["service"] == "rds"


def test_converse_returns_a_human_root_cause_narrative_bound_to_ranked_evidence():
    async def converse(**kwargs):
        request = json.loads(kwargs["messages"][0]["content"][0]["text"])
        return response(selection(request))
    result = run(bedrock_provider(region="us-east-1", model_id="configured.model-v1", converse=converse))
    assert result["payload"]["root_cause"]["service"] == "rds"
    assert result["payload"]["root_cause"]["evidence_refs"]
    assert not result["fallback"]


def test_converse_ignores_reasoning_content_before_final_text():
    async def converse(**kwargs):
        request = json.loads(kwargs["messages"][0]["content"][0]["text"])
        result = response(selection(request))
        result["output"]["message"]["content"].insert(0, {"reasoningContent": {"text": "internal"}})
        return result
    result = run(bedrock_provider(region="us-east-1", model_id="configured.model-v1", converse=converse))
    assert not result["fallback"]


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


def test_runtime_uses_a_bounded_async_boundary_and_closes_the_client(monkeypatch):
    calls = []

    class Client:
        def converse(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

        def close(self):
            calls.append("closed")

    runtime = Boto3BedrockRuntime("us-east-1")
    monkeypatch.setattr(runtime, "_create_client", lambda: Client())
    async def run_inline(operation, *args, **kwargs):
        return operation(*args, **kwargs)
    monkeypatch.setattr("sentinelops.explanations.bedrock.asyncio.to_thread", run_inline)
    async def scenario():
        assert await runtime.converse(modelId="model") == {"ok": True}
        await runtime.close()
    asyncio.run(scenario())
    assert calls == [{"modelId": "model"}, "closed"]


@pytest.mark.parametrize("reuse_client", [False, True], ids=["close-pending-client", "reuse-pending-client"])
def test_runtime_retains_client_created_after_request_cancellation(monkeypatch, reuse_client):
    calls = []

    class Client:
        def converse(self, **kwargs):
            calls.append("converse")
            return {"ok": True}

        def close(self):
            calls.append("closed")

    async def scenario():
        runtime = Boto3BedrockRuntime("us-east-1")
        worker_started = asyncio.Event()
        release_worker = asyncio.Event()
        workers = []

        def create_client():
            calls.append("create")
            return Client()

        async def run_in_worker(operation, *args, **kwargs):
            if operation is not create_client:
                return operation(*args, **kwargs)

            async def worker():
                worker_started.set()
                await release_worker.wait()
                return create_client()

            task = asyncio.create_task(worker())
            workers.append(task)
            return await asyncio.shield(task)

        monkeypatch.setattr(runtime, "_create_client", create_client)
        monkeypatch.setattr("sentinelops.explanations.bedrock.asyncio.to_thread", run_in_worker)
        first_request = asyncio.create_task(runtime.converse(modelId="model"))
        try:
            await asyncio.wait_for(worker_started.wait(), timeout=5)
            first_request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first_request

            operation = asyncio.create_task(runtime.converse(modelId="model") if reuse_client else runtime.close())
            await asyncio.sleep(0)
            assert not operation.done()
        finally:
            release_worker.set()
            await asyncio.gather(*workers)

        result = await operation
        if reuse_client:
            assert result == {"ok": True}
            await runtime.close()

    asyncio.run(scenario())
    expected = ["create", "converse", "closed"] if reuse_client else ["create", "closed"]
    assert calls == expected


def test_runtime_provider_returns_the_provider_and_its_lifecycle():
    runtime, provider = bedrock_runtime_provider(region="us-east-1", model_id="configured.model-v1")
    assert isinstance(runtime, Boto3BedrockRuntime)
    assert provider.provider_id.startswith("bedrock-converse/v1/")
