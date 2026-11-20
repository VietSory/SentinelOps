"""Offline coverage for the OpenAI Responses-compatible explanation provider."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

import sentinelops.api as api_module
from sentinelops.api_settings import Settings
from sentinelops.explanations.openai_compatible import (
    OpenAIResponsesRuntime,
    openai_responses_provider,
)
from sentinelops.explanations.service import MAX_BYTES
from tests.test_explanations import envelope, run, selection


BASE_URL = "https://llm.example.test/v1"
MODEL = "fixture-model-v1"
API_KEY = "fixture-api-key-never-a-real-credential"
ACTOR_AUTHORIZATION = "fixture-actor-authorization"


def response_handler(payload, *, status_code=200):
    """Return a transport handler for a fixed JSON response or raw body."""
    def handle(request):
        if isinstance(payload, bytes):
            return httpx.Response(status_code, content=payload)
        return httpx.Response(status_code, json=payload)
    return handle


def build_provider(handler, *, actor_authorization=None, base_url=BASE_URL, model=MODEL,
                   api_key=API_KEY):
    return openai_responses_provider(
        base_url=base_url,
        model=model,
        api_key=api_key,
        actor_authorization=actor_authorization,
        transport=httpx.MockTransport(handler),
    )


def close_runtime(runtime):
    asyncio.run(runtime.close())


@pytest.mark.parametrize("trailing_slash", ["", "/"], ids=["no-slash", "trailing-slash"])
def test_responses_request_preserves_url_prefix_and_uses_expected_schema(trailing_slash):
    base_url = "https://llm.example.test/gateway/team/v1"
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append((request, body))
        requested_selection = json.loads(body["input"])
        return httpx.Response(200, json={"status": "completed", "output_text": json.dumps(
            selection(requested_selection))})

    runtime, provider = build_provider(
        handle, base_url=base_url + trailing_slash, actor_authorization=ACTOR_AUTHORIZATION
    )
    try:
        result = run(provider)
    finally:
        close_runtime(runtime)

    assert not result["fallback"]
    assert len(requests) == 1
    request, body = requests[0]
    assert request.method == "POST"
    assert request.url == "https://llm.example.test/gateway/team/v1/responses"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["x-openai-actor-authorization"] == ACTOR_AUTHORIZATION
    assert set(body) == {"model", "instructions", "input", "max_output_tokens", "store", "text"}
    assert body["model"] == MODEL
    assert body["store"] is False
    assert body["max_output_tokens"] == 512
    assert body["text"] == {"format": {"type": "json_object"}}
    assert isinstance(body["instructions"], str) and body["instructions"]
    assert json.loads(body["input"])["revision"] == 1


def test_actor_header_is_omitted_when_not_configured():
    seen = []

    def handle(request):
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "status": "completed", "output_text": json.dumps(selection(json.loads(body["input"])))
        })

    runtime, provider = build_provider(handle)
    try:
        assert not run(provider)["fallback"]
    finally:
        close_runtime(runtime)

    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert "x-openai-actor-authorization" not in seen[0].headers


@pytest.mark.parametrize("response_shape", ["output_text", "output_blocks"])
def test_provider_parses_completed_responses_text_shapes(response_shape):
    def handle(request):
        body = json.loads(request.content)
        answer = json.dumps(selection(json.loads(body["input"])))
        if response_shape == "output_text":
            payload = {"status": "completed", "output_text": answer}
        else:
            # Newline between JSON members is legal whitespace and exercises block joining.
            split_at = answer.index(",") + 1
            payload = {"status": "completed", "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "ignored"}]},
                {"type": "message", "content": [
                    {"type": "output_text", "text": answer[:split_at]},
                    {"type": "output_text", "text": answer[split_at:]},
                ]},
            ]}
        return httpx.Response(200, json=payload)

    runtime, provider = build_provider(handle)
    try:
        result = run(provider)
    finally:
        close_runtime(runtime)
    assert not result["fallback"]
    assert result["provider"].startswith("openai-responses/v1/")
    assert result["payload"]["claims"]


@pytest.mark.parametrize("status_fields", [{}, {"status": "incomplete"}], ids=["missing", "incomplete"])
def test_provider_requires_completed_status_even_with_valid_output(status_fields):
    def handle(request):
        answer = selection(json.loads(json.loads(request.content)["input"]))
        return httpx.Response(200, json={"output_text": json.dumps(answer), **status_fields})

    runtime, provider = build_provider(handle)
    try:
        assert run(provider) == run()
    finally:
        close_runtime(runtime)


def test_provider_does_not_parse_refusal_as_output_text():
    def handle(request):
        answer = selection(json.loads(json.loads(request.content)["input"]))
        # Even a valid selection in a refusal block must never be accepted.
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [
                {"type": "refusal", "refusal": json.dumps(answer)},
            ]},
        ]})

    runtime, provider = build_provider(handle)
    try:
        assert run(provider) == run()
    finally:
        close_runtime(runtime)


@pytest.mark.parametrize("body", [
    b"{not-json",
    b'{"status":"completed","status":"incomplete"}',
    b'{"value":NaN}',
])
def test_runtime_rejects_malformed_json_duplicate_keys_and_nonfinite_values(body):
    runtime = OpenAIResponsesRuntime(
        base_url=BASE_URL, model=MODEL, api_key=API_KEY,
        transport=httpx.MockTransport(response_handler(body)),
    )
    try:
        with pytest.raises(ValueError):
            asyncio.run(runtime.converse(model=MODEL, instructions="test", input="{}"))
    finally:
        close_runtime(runtime)


def test_runtime_stops_reading_an_oversized_response_body():
    runtime = OpenAIResponsesRuntime(
        base_url=BASE_URL, model=MODEL, api_key=API_KEY,
        transport=httpx.MockTransport(response_handler(b"x" * (MAX_BYTES + 1))),
    )
    try:
        with pytest.raises(ValueError, match="body exceeds byte limit"):
            asyncio.run(runtime.converse(model=MODEL, instructions="test", input="{}"))
    finally:
        close_runtime(runtime)


@pytest.mark.parametrize("payload", [
    {"status": "completed", "output": []},
    {"status": "completed", "output_text": ""},
    {"status": "completed", "output_text": "not-json"},
    {"status": "completed", "output_text": '{"revision":1,"revision":1}'},
    {"status": "completed", "output_text": '{"revision":NaN}'},
    {"status": "completed", "output_text": "x" * (MAX_BYTES + 1)},
    {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": "not-json"},
    ]}]},
])
def test_invalid_responses_output_is_rejected_and_provider_falls_back(payload):
    runtime, provider = build_provider(response_handler(payload))
    try:
        result = run(provider)
    finally:
        close_runtime(runtime)
    assert result == run()


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_http_errors_fall_back_without_leaking_credentials_or_provider_body(status_code, caplog):
    private_error = "fixture-private-provider-detail fixture-upstream-secret"
    runtime, provider = build_provider(
        response_handler(private_error.encode(), status_code=status_code),
        actor_authorization=ACTOR_AUTHORIZATION,
    )
    try:
        result = run(provider)
    finally:
        close_runtime(runtime)

    assert result == run()
    assert API_KEY not in caplog.text
    assert ACTOR_AUTHORIZATION not in caplog.text
    assert "fixture-upstream-secret" not in caplog.text
    assert private_error not in json.dumps(result)


@pytest.mark.parametrize("settings_kwargs", [
    {"llm_base_url": BASE_URL},
    {"llm_model": MODEL},
    {"llm_api_key": API_KEY},
    {"llm_base_url": BASE_URL, "llm_model": MODEL},
    {"llm_base_url": BASE_URL, "llm_api_key": API_KEY},
    {"llm_model": MODEL, "llm_api_key": API_KEY},
])
def test_settings_require_all_openai_compatible_provider_fields(settings_kwargs):
    with pytest.raises(ValueError, match="required together"):
        Settings(**settings_kwargs)


def test_settings_accept_complete_provider_configuration_and_optional_actor_header():
    settings = Settings(
        llm_base_url=BASE_URL,
        llm_model=MODEL,
        llm_api_key=API_KEY,
        llm_actor_authorization=ACTOR_AUTHORIZATION,
    )
    assert settings.llm_base_url == BASE_URL
    assert settings.llm_model == MODEL
    assert settings.llm_api_key == API_KEY
    assert settings.llm_actor_authorization == ACTOR_AUTHORIZATION
    assert API_KEY not in repr(settings)
    assert ACTOR_AUTHORIZATION not in repr(settings)


@pytest.mark.parametrize("settings_kwargs", [
    {"llm_base_url": "http://llm.example.test/v1", "llm_model": MODEL, "llm_api_key": API_KEY},
    {"llm_base_url": "https://user:pass@llm.example.test/v1", "llm_model": MODEL, "llm_api_key": API_KEY},
    {"llm_base_url": BASE_URL + "?key=fixture", "llm_model": MODEL, "llm_api_key": API_KEY},
    {"llm_base_url": BASE_URL, "llm_model": "bad model", "llm_api_key": API_KEY},
    {"llm_base_url": BASE_URL, "llm_model": MODEL, "llm_api_key": "short"},
    {"llm_actor_authorization": "bad\nheader"},
    {"llm_base_url": BASE_URL, "llm_model": MODEL, "llm_api_key": API_KEY,
     "bedrock_region": "us-east-1", "bedrock_model_id": "fixture-model"},
])
def test_settings_reject_invalid_or_conflicting_provider_configuration(settings_kwargs):
    with pytest.raises(ValueError):
        Settings(**settings_kwargs)


class ReadOnlyRepository:
    """Small in-memory API repository fixture; no persistence or network access."""

    def __init__(self):
        self.source = envelope()

    async def check_ready(self):
        return None

    async def get(self, incident_id, revision):
        if incident_id != self.source["incident_id"] or revision not in (None, self.source["revision"]):
            from sentinelops.incidents.domain import IncidentNotFound
            raise IncidentNotFound()
        return deepcopy({key: value for key, value in self.source.items() if key != "timeline"})

    async def history(self, incident_id, *, after_revision, limit, max_revision):
        assert incident_id == self.source["incident_id"]
        assert after_revision == 0 and limit == 50 and max_revision == self.source["revision"]
        return deepcopy(self.source["timeline"]["events"])


def test_api_settings_wire_openai_provider_through_mock_transport(monkeypatch):
    repo = ReadOnlyRepository()
    request_observations = []

    def handle(request):
        body = json.loads(request.content)
        request_observations.append((request, body))
        candidate = selection(json.loads(body["input"]))
        return httpx.Response(200, json={"status": "completed", "output_text": json.dumps(candidate)})

    real_factory = openai_responses_provider
    factory_calls = []
    runtimes = []

    def factory_spy(**kwargs):
        factory_calls.append({key: value for key, value in kwargs.items() if key != "transport"})
        runtime, provider = real_factory(**kwargs, transport=httpx.MockTransport(handle))
        runtimes.append(runtime)
        return runtime, provider

    monkeypatch.setattr(api_module, "openai_responses_provider", factory_spy)
    settings = Settings(
        api_token="fixture-api-token-" + "x" * 32,
        llm_base_url=BASE_URL,
        llm_model=MODEL,
        llm_api_key=API_KEY,
        llm_actor_authorization=ACTOR_AUTHORIZATION,
    )
    app = api_module.create_app(settings, repository=repo)

    async def request_api():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get(
                f"/v1/incidents/{repo.source['incident_id']}/explanation?revision=1",
                headers={"Authorization": "Bearer " + settings.api_token},
            )

    try:
        response = asyncio.run(request_api())
    finally:
        for runtime in runtimes:
            close_runtime(runtime)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["fallback"] is False
    assert result["provider"].startswith("openai-responses/v1/")
    assert factory_calls == [{
        "base_url": BASE_URL,
        "model": MODEL,
        "api_key": API_KEY,
        "actor_authorization": ACTOR_AUTHORIZATION,
    }]
    assert len(request_observations) == 1
    request, body = request_observations[0]
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["x-openai-actor-authorization"] == ACTOR_AUTHORIZATION
    assert body["store"] is False and body["model"] == MODEL
    assert result["revision"] == 1
