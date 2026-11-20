import asyncio
from unittest.mock import AsyncMock

import pytest

import sentinelops.api as api_module


@pytest.mark.parametrize("backend", ["bedrock", "openai"])
@pytest.mark.parametrize("fail_startup", [True, False], ids=["startup-error", "shutdown"])
def test_lifespan_closes_explanation_runtime(monkeypatch, backend, fail_startup):
    closed = []

    class Resource:
        def __init__(self, name):
            self.name = name

        async def close(self):
            closed.append(self.name)

    runtime = Resource("runtime")
    runtime.close = AsyncMock(wraps=runtime.close)
    if backend == "bedrock":
        monkeypatch.setattr(api_module, "bedrock_runtime_provider", lambda **kwargs: (runtime, None))
        provider_settings = {"bedrock_region": "us-east-1", "bedrock_model_id": "test-model"}
    else:
        monkeypatch.setattr(api_module, "openai_responses_provider", lambda **kwargs: (runtime, None))
        provider_settings = {"llm_base_url": "https://llm.example.invalid", "llm_model": "test-model",
                             "llm_api_key": "test-placeholder"}

    control_url = "postgresql://localhost/control"
    action_url = "postgresql://localhost/action"

    async def create_pool(dsn, **kwargs):
        if dsn == control_url:
            if fail_startup:
                raise RuntimeError("control pool failed")
            return Resource("control")
        assert dsn == action_url
        return Resource("action")

    monkeypatch.setattr(api_module.asyncpg, "create_pool", create_pool)
    app = api_module.create_app(api_module.Settings(control_database_url=control_url,
                                                     action_database_url=action_url, **provider_settings))

    async def scenario():
        if fail_startup:
            with pytest.raises(RuntimeError, match="control pool failed"):
                async with app.router.lifespan_context(app):
                    pytest.fail("startup unexpectedly succeeded")
        else:
            async with app.router.lifespan_context(app):
                assert not closed

    asyncio.run(scenario())
    runtime.close.assert_awaited_once_with()
    assert closed == (["runtime"] if fail_startup else ["action", "control", "runtime"])
