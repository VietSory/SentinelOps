"""Bounded fact selection with local rendering, never free-form causal generation."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import math
import re
from time import monotonic
from typing import Awaitable, Callable

from sentinelops.contracts import validate_payload

PROMPT_VERSION = "incident-fact-selection/v1"
FALLBACK_PROVIDER = "deterministic-facts/v1"
MAX_CARDS = 64
MAX_CLAIMS = 12
MAX_BYTES = 64_000
MAX_FACT_BYTES = 4_000
LOG = logging.getLogger(__name__)


class ProviderFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ExplanationProvider:
    """Reuse one instance per configured provider for bounded concurrency/circuit state.

    ``invoke`` is an async transport accepting the selection request documented in
    EXPLANATIONS.md. It must cooperate with task cancellation and bound its network
    response before decoding. No provider, SDK or credentials are loaded implicitly.
    """

    def __init__(self, provider_id: str, invoke: Callable[[dict], Awaitable[dict]], *,
                 timeout_seconds: float = 10, failure_threshold: int = 3,
                 recovery_seconds: float = 30, max_concurrency: int = 4):
        if (not isinstance(provider_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", provider_id)
            or provider_id == FALLBACK_PROVIDER):
            raise ValueError("provider requires a bounded versioned identifier")
        if (not callable(invoke) or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 10
            or type(recovery_seconds) not in (int, float) or not math.isfinite(recovery_seconds)
            or not 0 < recovery_seconds <= 300
            or type(failure_threshold) is not int or not 1 <= failure_threshold <= 10
            or type(max_concurrency) is not int or not 1 <= max_concurrency <= 32):
            raise ValueError("invalid provider bounds")
        self.provider_id, self.invoke = provider_id, invoke
        self.timeout_seconds, self.failure_threshold = timeout_seconds, failure_threshold
        self.recovery_seconds = recovery_seconds
        self._failures, self._open_until, self._generation = 0, 0.0, 0
        self._slots = asyncio.Semaphore(max_concurrency)

    async def select(self, request: dict) -> list[str]:
        if monotonic() < self._open_until:
            raise ProviderFailure("circuit_open")
        generation = self._generation
        try:
            # Queueing consumes the same total deadline; no hidden unbounded wait.
            async with asyncio.timeout(self.timeout_seconds):
                async with self._slots:
                    if monotonic() < self._open_until:
                        raise ProviderFailure("circuit_open")
                    response = await self.invoke(deepcopy(request))
                    result = _validate_selection(response, request)
        except ProviderFailure:
            raise
        except Exception as exc:
            self._generation += 1
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open_until = monotonic() + self.recovery_seconds
            reason = ("timeout" if isinstance(exc, TimeoutError)
                      else "invalid_output" if isinstance(exc, (ValueError, TypeError))
                      else "provider_error")
            # Deliberately exclude exception text: upstream errors may contain secrets.
            raise ProviderFailure(reason) from None
        # A late concurrent success cannot close a circuit opened by a newer failure.
        if generation == self._generation:
            self._failures = 0
            self._open_until = 0.0
        return result


def _validate_selection(response: dict, request: dict) -> list[str]:
    if (not isinstance(response, dict)
        or set(response) != {"incident_id", "revision", "prompt_version", "card_ids"}
        or response["incident_id"] != request["incident_id"]
        or type(response["revision"]) is not int or response["revision"] != request["revision"]
        or response["prompt_version"] != PROMPT_VERSION):
        raise ValueError("provider selection differs from pinned revision/protocol")
    if len(json.dumps(response, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("provider selection exceeds byte budget")
    selected = response["card_ids"]
    known = {card["card_id"] for card in request["cards"]}
    if (not isinstance(selected, list) or not 1 <= len(selected) <= MAX_CLAIMS
        or any(not isinstance(item, str) or item not in known for item in selected)
        or len(set(selected)) != len(selected)):
        raise ValueError("provider selected unknown, duplicate or unbounded cards")
    # Final output retains authoritative source ordering, not a model ranking.
    return [card["card_id"] for card in request["cards"] if card["card_id"] in selected]


def _cards(envelope: dict) -> tuple[dict, dict[str, dict]]:
    ier = envelope["payload"]
    cards, rendered = [], {}
    for index, evidence in enumerate(ier["evidence"]):
        # Never truncate a numeric/time token to make an over-budget card fit.
        if len(evidence["fact"].encode()) > MAX_FACT_BYTES:
            continue
        if len(cards) >= MAX_CARDS:
            break
        card_id = f"evidence-{index:05d}"
        text = "Recorded " + evidence["type"] + " evidence"
        if evidence.get("service") is not None:
            text += " for " + json.dumps(evidence["service"], ensure_ascii=False)
        if evidence.get("observed_at") is not None:
            text += " at " + evidence["observed_at"]
        text += ": " + json.dumps(evidence["fact"], ensure_ascii=False)
        claim = {"text": text, "evidence_refs": [evidence["id"]]}
        if len(json.dumps(claim, ensure_ascii=False).encode()) > MAX_FACT_BYTES:
            continue
        cards.append({"card_id": card_id, "type": evidence["type"],
                      "service": evidence.get("service"), "observed_at": evidence.get("observed_at")})
        rendered[card_id] = claim
    # Raw fact/log text, source URIs, actor/reason, timeline and trigger refs are
    # intentionally absent from the remote request. Only local rendering uses them.
    request = {"prompt_version": PROMPT_VERSION, "incident_id": envelope["incident_id"],
               "revision": envelope["revision"], "state": ier["state"], "severity": ier["severity"],
               "availability": envelope["context"]["availability"],
               "affected_services": ier["affected_services"], "service_signals": ier["service_signals"],
               "root_cause_hypotheses": [{"service": row["service"], "score": row["score"]}
                                         for row in ier["root_cause_ranking"]], "cards": cards}
    return request, rendered


def _render(envelope: dict, selected: list[str], cards: dict[str, dict]) -> dict:
    ier = envelope["payload"]
    summary = (f"Incident {envelope['incident_id']}, revision {envelope['revision']}: "
               f"state={ier['state']}; severity={ier['severity']}; "
               f"availability={envelope['context']['availability']}. ")
    summary += ("Ranked RCA hypotheses are not confirmed causes. " if ier["root_cause_ranking"]
                else "No ranked root-cause hypothesis is available. ")
    summary += "Recorded evidence is quoted, not independently verified. No action is authorized by this explanation."
    if not cards:
        summary += " No bounded evidence cards are available."
    payload = {"schema_version": "1.0", "incident_id": envelope["incident_id"], "summary": summary,
               "claims": [deepcopy(cards[key]) for key in selected]}
    validate_payload("explanation.schema.json", payload)
    return payload


async def explain_incident(envelope: dict, *, provider: ExplanationProvider | None = None) -> dict:
    """Implement H2. Invalid input fails; provider failure never changes incident facts.

    B owns authentication/loading and the HTTP revision wrapper. This module has
    no persistence, cache, API settings, filesystem or cloud credential access.
    """
    snapshot = deepcopy(envelope)
    validate_payload("incident-envelope.schema.json", snapshot)
    request, cards = _cards(snapshot)
    selected = list(cards)[:MAX_CLAIMS]
    reason, used_provider = "provider_absent", FALLBACK_PROVIDER
    if provider is not None:
        if not isinstance(provider, ExplanationProvider):
            raise ValueError("provider must use the bounded ExplanationProvider adapter")
        services = [*request["affected_services"], *(r["service"] for r in request["service_signals"]),
                    *(r["service"] for r in request["root_cause_hypotheses"]),
                    *(r["service"] for r in request["cards"] if r["service"] is not None)]
        if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) for name in services):
            reason = "unsupported_service_identifier"
        elif not cards:
            reason = "no_bounded_cards"
        elif len(json.dumps(request, allow_nan=False).encode()) > MAX_BYTES:
            reason = "request_budget"
        else:
            try:
                selected = await provider.select(request)
                used_provider, reason = provider.provider_id, None
            except ProviderFailure as exc:
                reason = exc.reason
    payload = _render(snapshot, selected, cards)
    LOG.info("incident_explanation", extra={"incident_id": snapshot["incident_id"],
             "revision": snapshot["revision"], "prompt_version": PROMPT_VERSION,
             "provider": used_provider, "requested_provider": provider.provider_id if provider else None,
             "fallback_reason": reason})
    return {"payload": payload, "provider": used_provider, "fallback": reason is not None}
