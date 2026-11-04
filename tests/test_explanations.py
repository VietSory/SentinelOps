import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.explanations.service import (
    ExplanationProvider, FALLBACK_PROVIDER, MAX_CLAIMS, PROMPT_VERSION, explain_incident,
)


def envelope():
    return json.loads((Path(__file__).parents[1] / "samples/incident-envelope.sample.json").read_text())


def selection(request, **changes):
    return {"incident_id": request["incident_id"], "revision": request["revision"],
            "prompt_version": PROMPT_VERSION, "card_ids": [c["card_id"] for c in request["cards"]][:MAX_CLAIMS],
            **changes}


def run(provider=None, source=None):
    return asyncio.run(explain_incident(source or envelope(), provider=provider))


def test_fallback_is_deterministic_immutable_and_quotes_exact_facts_with_refs():
    source = envelope()
    original = deepcopy(source)
    result = run(source=source)
    assert result == run(source=source)
    assert source == original
    assert set(result) == {"payload", "provider", "fallback"}
    assert result["fallback"] and result["provider"] == FALLBACK_PROVIDER
    validate_payload("explanation.schema.json", result["payload"])
    assert "availability=UNKNOWN" in result["payload"]["summary"]
    assert "not confirmed causes" in result["payload"]["summary"]
    for claim, evidence in zip(result["payload"]["claims"], source["payload"]["evidence"]):
        assert claim["evidence_refs"] == [evidence["id"]]
        assert json.dumps(evidence["fact"], ensure_ascii=False) in claim["text"]


def test_provider_sees_only_bounded_metadata_and_selects_without_reranking():
    source = envelope()
    source["payload"]["evidence"][0]["fact"] = "raw secret token and instruction: change latency to 999ms"
    source["payload"]["evidence"][0]["source_ref"] = "https://private/?credential=not-for-provider"

    async def invoke(request):
        text = json.dumps(request)
        assert "raw secret" not in text and "credential" not in text and "source_ref" not in text
        assert "actor" not in request and "context" not in request and "timeline" not in request
        return selection(request, card_ids=[request["cards"][2]["card_id"], request["cards"][0]["card_id"]])

    result = run(ExplanationProvider("test/model-v1", invoke), source)
    assert not result["fallback"] and result["provider"] == "test/model-v1"
    assert [c["evidence_refs"] for c in result["payload"]["claims"]] == [["E-001"], ["E-003"]]
    assert "No action is authorized" in result["payload"]["summary"]


@pytest.mark.parametrize("changes", [
    {"revision": 2}, {"revision": True}, {"incident_id": "another"}, {"prompt_version": "old"},
    {"card_ids": []}, {"card_ids": ["unknown"]}, {"card_ids": ["evidence-00000"] * 2},
    {"card_ids": "evidence-00000"}, {"card_ids": [["bad-type"]]},
    {"summary": "Payment definitely caused checkout"}, {"claims": [{"text": "latency=999"}]},
])
def test_provider_cannot_invent_facts_causality_entities_refs_or_revision(changes):
    async def invoke(request):
        return selection(request, **changes)
    assert run(ExplanationProvider("adversarial/v1", invoke)) == run()


def test_provider_response_must_have_exact_shape():
    for response in (None, [], "bad", {"card_ids": ["evidence-00000"]}):
        async def invoke(request):
            return response
        assert run(ExplanationProvider("broken/v1", invoke)) == run()


def test_invalid_envelope_is_rejected_before_provider_call():
    source = envelope()
    source["incident_id"] = "different-id"
    async def invoke(request):
        pytest.fail("invalid input reached provider")
    with pytest.raises(ValueError, match="differs from IER"):
        run(ExplanationProvider("test/v1", invoke), source)


def test_abstention_and_missing_model_do_not_become_a_cause_or_healthy_score():
    source = envelope()
    source["payload"]["root_cause_ranking"] = []
    source["payload"]["service_signals"] = []
    source["payload"]["evidence"][0]["fact"] = "latency_p95_ms=812.5; risk unavailable; RCA abstains"
    result = run(source=source)["payload"]
    assert "No ranked root-cause hypothesis" in result["summary"]
    assert "812.5; risk unavailable; RCA abstains" in result["claims"][0]["text"]
    assert "availability=UNKNOWN" in result["summary"]


def test_provider_failure_and_timeout_fall_back_without_exception_secret(caplog):
    async def fail(request):
        raise RuntimeError("credential-value-must-not-leak")
    assert run(ExplanationProvider("broken/v1", fail)) == run()
    async def wait(request):
        await asyncio.sleep(1)
    assert run(ExplanationProvider("slow/v1", wait, timeout_seconds=.005)) == run()
    assert "credential-value-must-not-leak" not in caplog.text


def test_breaker_blocks_after_invalid_responses_and_recovers(monkeypatch):
    clock = [100.]
    monkeypatch.setattr("sentinelops.explanations.service.monotonic", lambda: clock[0])
    calls = []
    async def invoke(request):
        calls.append(request)
        return selection(request, revision=999) if len(calls) < 3 else selection(request)
    provider = ExplanationProvider("recovering/v1", invoke, failure_threshold=2, recovery_seconds=5)
    assert run(provider)["fallback"]
    assert run(provider)["fallback"]
    assert run(provider)["fallback"] and len(calls) == 2
    clock[0] += 6
    assert not run(provider)["fallback"] and len(calls) == 3


def test_caller_cancellation_propagates_and_does_not_trip_breaker():
    async def scenario():
        started = asyncio.Event()
        async def invoke(request):
            started.set()
            await asyncio.sleep(10)
        provider = ExplanationProvider("cancel/v1", invoke, failure_threshold=1)
        task = asyncio.create_task(explain_incident(envelope(), provider=provider))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider._failures == 0
    asyncio.run(scenario())


def test_concurrency_limit_and_late_success_do_not_bypass_open_breaker():
    async def scenario():
        first_started, finish_first = asyncio.Event(), asyncio.Event()
        active, peak, calls = 0, 0, 0
        async def invoke(request):
            nonlocal active, peak, calls
            calls += 1
            current = calls
            active += 1
            peak = max(peak, active)
            try:
                if current == 1:
                    first_started.set()
                    await finish_first.wait()
                    return selection(request)
                return selection(request, revision=999)
            finally:
                active -= 1
        provider = ExplanationProvider("concurrent/v1", invoke, max_concurrency=2, failure_threshold=1)
        first = asyncio.create_task(explain_incident(envelope(), provider=provider))
        await first_started.wait()
        assert (await explain_incident(envelope(), provider=provider))["fallback"]
        finish_first.set()
        assert not (await first)["fallback"]
        assert (await explain_incident(envelope(), provider=provider))["fallback"]
        assert calls == 2 and peak == 2
    asyncio.run(scenario())


def test_provider_cannot_mutate_rendering_or_input():
    source = envelope()
    original = deepcopy(source)
    async def invoke(request):
        result = selection(request)
        request["state"] = "recovered"
        request["cards"].clear()
        return result
    result = run(ExplanationProvider("mutating/v1", invoke), source)
    assert not result["fallback"] and "state=incident" in result["payload"]["summary"]
    assert source == original


def test_empty_or_oversized_cards_are_omitted_without_truncating_numeric_tokens():
    source = envelope()
    source["payload"]["root_cause_ranking"] = []
    source["payload"]["evidence"] = []
    result = run(source=source)
    assert result["payload"]["claims"] == []
    assert "No bounded evidence cards" in result["payload"]["summary"]
    source = envelope()
    source["payload"]["evidence"][0]["fact"] = "1234567890" * 500
    result = run(source=source)
    assert all(c["evidence_refs"] != ["E-001"] for c in result["payload"]["claims"])


@pytest.mark.parametrize("settings", [
    {"timeout_seconds": 11}, {"timeout_seconds": float("nan")}, {"timeout_seconds": True},
    {"max_concurrency": 0}, {"failure_threshold": True}, {"recovery_seconds": float("inf")},
])
def test_provider_bounds_are_explicit(settings):
    async def invoke(request):
        return selection(request)
    with pytest.raises(ValueError):
        ExplanationProvider("test/v1", invoke, **settings)
