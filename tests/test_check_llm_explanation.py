"""Offline checks of the live explanation command."""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts import check_llm_explanation as cli
from tests.test_explanations import selection


API_KEY = "test-key-000000"
ACTOR_AUTHORIZATION = "test-actor-authorization"
NARRATIVE = "RDS là giả thuyết nguyên nhân gốc rễ dẫn đầu."


@pytest.fixture
def mock_runtime(monkeypatch):
    monkeypatch.setenv("SENTINELOPS_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("SENTINELOPS_LLM_MODEL", "test-model")
    monkeypatch.setenv("SENTINELOPS_LLM_API_KEY", API_KEY)
    monkeypatch.setenv("SENTINELOPS_LLM_ACTOR_AUTHORIZATION", ACTOR_AUTHORIZATION)
    real_factory = cli.openai_responses_provider

    def install(handler):
        transport = httpx.MockTransport(handler)
        close = AsyncMock(wraps=transport.aclose)
        monkeypatch.setattr(transport, "aclose", close)
        monkeypatch.setattr(cli, "openai_responses_provider",
                            lambda **settings: real_factory(**settings, transport=transport))
        return close

    return install


def completed_response(request, narrative=NARRATIVE):
    answer = selection(json.loads(json.loads(request.content)["input"]))
    answer["root_cause"]["explanation"] = narrative
    return httpx.Response(200, json={
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}],
    })


def test_openai_explanation_success(mock_runtime, capsys, tmp_path):
    def respond(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert "x-openai-actor-authorization" not in request.headers
        assert json.loads(request.content)["model"] == "test-model"
        return completed_response(request)

    close = mock_runtime(respond)
    envelope = tmp_path / "custom-envelope.json"
    envelope.write_bytes(cli.DEFAULT_ENVELOPE.read_bytes())
    assert cli.main(["--envelope", str(envelope)]) == 0
    close.assert_awaited_once()
    stdout, stderr = capsys.readouterr()
    output = json.loads(stdout)
    assert output.pop("provider").startswith("openai-responses/v1/")
    assert output == {
        "fallback": False,
        "root_cause": {"service": "rds", "score": 0.91},
        "evidence_refs": ["E-001", "E-002", "E-003"],
        "narrative_length": len(NARRATIVE),
        "narrative": NARRATIVE,
    }
    assert all(secret not in stdout for secret in (API_KEY, ACTOR_AUTHORIZATION, "fixture-producer"))
    assert stderr == ""


def test_http_error_falls_back_without_printing_response(mock_runtime, capsys):
    close = mock_runtime(lambda request: httpx.Response(401, text=f"denied {API_KEY} {ACTOR_AUTHORIZATION}"))
    assert cli.main([]) == 2
    close.assert_awaited_once()
    stdout, stderr = capsys.readouterr()
    output = json.loads(stdout)
    assert output["provider"] == "deterministic-facts/v1"
    assert output["fallback"] is True
    assert "fallback_reason" not in output
    assert all(text not in stdout for text in (API_KEY, ACTOR_AUTHORIZATION, "denied"))
    assert stderr == ""


@pytest.mark.parametrize("key", [API_KEY, 'test-key-"\\-000000'])
def test_provider_echoing_credentials_falls_back_without_printing_them(mock_runtime, monkeypatch, capsys, key):
    close = mock_runtime(lambda request: completed_response(request, f"{NARRATIVE} {key} {ACTOR_AUTHORIZATION}"))
    monkeypatch.setenv("SENTINELOPS_LLM_API_KEY", key)
    assert cli.main([]) == 2
    close.assert_awaited_once()
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout)["fallback"] is True
    assert key not in stdout and json.dumps(key)[1:-1] not in stdout
    assert ACTOR_AUTHORIZATION not in stdout and stderr == ""


@pytest.mark.parametrize("key", [API_KEY, 'test-key-"\\-000000'])
def test_credentials_reaching_output_are_redacted(mock_runtime, monkeypatch, capsys, key):
    close = mock_runtime(lambda request: pytest.fail("unexpected provider request"))
    monkeypatch.setenv("SENTINELOPS_LLM_API_KEY", key)

    async def explain(envelope, *, provider=None):
        return {"provider": "openai-responses/v1/x", "fallback": False,
                "payload": {"root_cause": {"service": "rds", "score": 0.91, "evidence_refs": ["E-001"],
                                           "explanation": f"{NARRATIVE} {key} {ACTOR_AUTHORIZATION}"}}}

    monkeypatch.setattr(cli, "explain_incident", explain)
    assert cli.main([]) == 3
    close.assert_awaited_once()
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout)["narrative"] == f"{NARRATIVE} [REDACTED] [REDACTED]"
    assert key not in stdout and json.dumps(key)[1:-1] not in stdout
    assert ACTOR_AUTHORIZATION not in stdout and stderr == ""


@pytest.mark.parametrize(("provider_id", "fallback", "reason", "exit_code"), [
    ("deterministic-facts/v1", True, "provider_error", 2),
    ("deterministic-facts/v1", True, f"upstream: {API_KEY}", 2),
    ("other/v1", False, None, 1),
])
def test_exit_requires_openai_and_only_prints_fallback_codes(
    mock_runtime, monkeypatch, capsys, provider_id, fallback, reason, exit_code,
):
    close = mock_runtime(lambda request: pytest.fail("unexpected provider request"))

    async def explain(envelope, *, provider=None):
        return {"provider": provider_id, "fallback": fallback, "fallback_reason": reason,
                "payload": {"root_cause": {"service": None, "score": None,
                                           "evidence_refs": [], "explanation": NARRATIVE}}}

    monkeypatch.setattr(cli, "explain_incident", explain)
    assert cli.main([]) == exit_code
    close.assert_awaited_once()
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout).get("fallback_reason") == ("provider_error" if reason == "provider_error" else None)
    assert API_KEY not in stdout and stderr == ""


def test_runtime_closes_when_explain_raises(mock_runtime, monkeypatch, capsys):
    close = mock_runtime(lambda request: pytest.fail("unexpected provider request"))

    async def explain(envelope, *, provider=None):
        raise ValueError(f"bad input {API_KEY} {ACTOR_AUTHORIZATION}")

    monkeypatch.setattr(cli, "explain_incident", explain)
    assert cli.main([]) == 1
    close.assert_awaited_once()
    stdout, stderr = capsys.readouterr()
    assert stdout == ""
    assert stderr == "Could not complete the explanation check.\n"


def test_key_argument_is_rejected_without_echo(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["--api-key", API_KEY])
    assert error.value.code == 1
    assert API_KEY not in capsys.readouterr().err
