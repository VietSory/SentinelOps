"""Bounded AI root-cause narrative with local evidence rendering and fallback."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from ipaddress import IPv6Address
import json
import logging
import math
import re
from time import monotonic
from typing import Awaitable, Callable
from unicodedata import category, normalize
from uuid import uuid4

from sentinelops.contracts import validate_payload

PROMPT_VERSION = "incident-root-cause-narrative/v2"
FALLBACK_PROVIDER = "deterministic-facts/v1"
MAX_CARDS = 64
MAX_CLAIMS = 12
MAX_BYTES = 64_000
MAX_FACT_BYTES = 4_000
MAX_ROOT_CAUSE_BYTES = 2_000
UNSAFE_FACT_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(?:password|passwd|secret|token|authorization|api[_ -]?key|access[_ -]?key)\b",
    r"https?://",
    r"\b(?:ignore|disregard)\b.{0,50}\b(?:instructions?|system prompt|rules)\b",
    r"\b(?:reveal|exfiltrate|execute|run)\b.{0,40}\b(?:secret|credential|command|tool)\b",
    r"\b(?:system|developer|assistant|user)\s+(?:message|prompt|instruction|rule)s?\b",
    r"(?:bỏ qua|phớt lờ|tiết lộ).{0,80}(?:chỉ dẫn|quy tắc|prompt|bí mật)",
))
DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
PII_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"(?<![\w])(?:\+\d{8,15}|(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]){2,3}\d{3,4})(?![\w])",
    r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])",
    r"\b(?:phone|mobile|telephone)\s*[:=]\s*\S+",
    rf"(?<![\w.-])(?:(?:{DNS_LABEL}\.){{2,}}[a-z][a-z0-9-]*"
    rf"|(?:{DNS_LABEL}\.)+(?:internal|local|svc|test|example))(?![\w-]|\.[\w-])",
))
SECRET_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"sk-[A-Za-z0-9_-]{16,}", r"AKIA[0-9A-Z]{16}",
    r"\bbearer\s+[A-Za-z0-9._~+/-]+=*",
    r"[A-Za-z0-9+/]{32,}",
))
IPV6_CANDIDATE = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f:.]*(?![\w:])", re.I)
UNSUPPORTED_CAUSALITY_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(?:was caused by|were caused by|definitively caused|confirmed root cause|proves? the cause)\b",
    r"\broot\s+cause\s+is\b",
    r"(?:chắc chắn gây ra|đã xác nhận là nguyên nhân|chứng minh nguyên nhân)",
    r"chắc\s+chắn\s+\d+(?:[.,]\d+)?\s*%",
))
MEASURED_TOKEN = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|(?:ms|sec(?:ond)?s?|min(?:ute)?s?|hours?|rps|req/s|bytes?|kb|mb|gb)\b)", re.I)
BARE_NUMBER = re.compile(r"(?<![\w.-])[-+]?\d+(?:[.,]\d+)*(?![\w-]|[.,]\d)")
THOUSANDS_GROUPED = re.compile(r"[1-9]\d{0,2}(?:([.,])\d{3})(?:\1\d{3})*")
NUMERIC_CONTEXT = re.compile(
    r"\b(?:in|since|during|from|before|after|year|năm)\s+(?:19|20)[0-9]{2}\b", re.I)
SERVICE_MENTION = re.compile(r"(?<![\w-])[a-z][a-z0-9-]*(?![\w-])", re.I)
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

    async def select(self, request: dict) -> dict:
        if monotonic() < self._open_until:
            raise ProviderFailure("circuit_open")
        generation = self._generation
        started_invocation = False
        try:
            # Queueing consumes the same total deadline; no hidden unbounded wait.
            async with asyncio.timeout(self.timeout_seconds):
                async with self._slots:
                    if monotonic() < self._open_until:
                        raise ProviderFailure("circuit_open")
                    started_invocation = True
                    response = await self.invoke(deepcopy(request))
                    result = _validate_selection(response, request)
        except ProviderFailure:
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) and not started_invocation:
                raise ProviderFailure("queue_timeout") from None
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


def _contains_sensitive_content(text: str) -> bool:
    if any(pattern.search(text) for pattern in (*PII_PATTERNS, *SECRET_PATTERNS)):
        return True
    for match in IPV6_CANDIDATE.finditer(text):
        try:
            IPv6Address(match.group().rstrip("."))
        except ValueError:
            continue
        return True
    return False


def _numeric_tokens(text: str) -> set[str]:
    measurements = {token.casefold() for token in MEASURED_TOKEN.findall(text)}
    remaining = NUMERIC_CONTEXT.sub(" ", MEASURED_TOKEN.sub(" ", text))
    return measurements | set(BARE_NUMBER.findall(remaining))


def _token_forms(token: str) -> set[str]:
    # Narrative-side only: Vietnamese prose writes 1092 as "1.092". Evidence tokens stay verbatim,
    # so a decimal fact such as "1.016" never matches an integer narrative "1016".
    if THOUSANDS_GROUPED.fullmatch(token):
        return {token, re.sub(r"[.,]", "", token)}
    return {token}


def _request_services(request: dict) -> set[str]:
    return {*request["affected_services"],
            *(row["service"] for row in request["service_signals"]),
            *(row["service"] for row in request["root_cause_hypotheses"]),
            *(card["service"] for card in request["cards"] if card["service"] is not None)}


def _validate_root_cause(value: dict, request: dict, selected_cards: list[dict]) -> dict:
    if (not isinstance(value, dict)
        or set(value) != {"service", "score", "explanation", "evidence_refs"}
        or not isinstance(value["explanation"], str)
        or not value["explanation"].strip()
        or len(value["explanation"].encode()) > MAX_ROOT_CAUSE_BYTES
        or not isinstance(value["evidence_refs"], list)
        or any(not isinstance(item, str) for item in value["evidence_refs"])
        or len(set(value["evidence_refs"])) != len(value["evidence_refs"])):
        raise ValueError("provider root-cause narrative has an invalid shape")
    narrative = normalize("NFKC", value["explanation"])
    tokens = _numeric_tokens(narrative)
    if (_contains_sensitive_content(narrative)
        or any(pattern.search(narrative) for pattern in UNSUPPORTED_CAUSALITY_PATTERNS)
        or value["service"] is None and tokens):
        raise ValueError("provider root-cause narrative contains unsupported or sensitive content")
    services = _request_services(request)
    mentions = {match.casefold() for match in SERVICE_MENTION.findall(narrative)}
    def reject_unreferenced_services(allowed: set[str]) -> None:
        if any(name not in allowed and (name in services or name.endswith("-service")) for name in mentions):
            raise ValueError("provider root-cause narrative mentions a service outside its evidence")
    hypotheses = {item["service"]: item for item in request["root_cause_hypotheses"]}
    if value["service"] is None:
        if value["score"] is not None or value["evidence_refs"]:
            raise ValueError("provider abstention must omit score and references")
        reject_unreferenced_services(set())
        return value
    if not isinstance(value["service"], str) or value["service"] not in hypotheses:
        raise ValueError("provider root-cause service is not a ranked hypothesis")
    hypothesis = hypotheses[value["service"]]
    if (type(value["score"]) not in (int, float) or not math.isfinite(value["score"])
        or value["score"] != hypothesis["score"]):
        raise ValueError("provider root-cause score must copy the ranked hypothesis score")
    hypothesis_refs = set(hypothesis["evidence_refs"])
    card_refs = {card["card_id"] for card in selected_cards}
    refs = set(value["evidence_refs"])
    if not refs or not refs <= hypothesis_refs or not refs <= card_refs:
        raise ValueError("provider root-cause references are outside the ranked evidence cards")
    # Services may come from any card shown with the explanation; numbers only from the cited cards,
    # so a model cannot select an extra card to borrow another service's measurements.
    reject_unreferenced_services({value["service"].casefold(), *(
        card["service"].casefold() for card in selected_cards if card["service"])})
    allowed_tokens = {token for card in selected_cards if card["card_id"] in refs
                      for token in _numeric_tokens(card["fact"])}
    if any(not _token_forms(token) & allowed_tokens for token in tokens):
        raise ValueError("provider root-cause narrative adds unsupported or sensitive content")
    return value


def _validate_selection(response: dict, request: dict) -> dict:
    if (not isinstance(response, dict)
        or set(response) != {"incident_id", "revision", "prompt_version", "card_ids", "root_cause"}
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
    selected_cards = [card for card in request["cards"] if card["card_id"] in selected]
    return {"card_ids": [card["card_id"] for card in selected_cards],
            "root_cause": _validate_root_cause(response["root_cause"], request, selected_cards)}


def _safe_provider_fact(value: str) -> str | None:
    if not isinstance(value, str) or len(value.encode()) > MAX_FACT_BYTES:
        return None
    text = " ".join(normalize("NFKC", value).split())
    if (any(category(char) in {"Cc", "Cf", "Cs"} for char in text)
        or _contains_sensitive_content(text)
        or any(pattern.search(text) for pattern in UNSAFE_FACT_PATTERNS)):
        return None
    return text


def _cards(envelope: dict) -> tuple[dict, dict[str, dict], dict[str, str]]:
    ier = envelope["payload"]
    cards, rendered, evidence_to_card_id = [], {}, {}
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
        rendered[card_id] = claim
        provider_fact = _safe_provider_fact(evidence["fact"])
        if provider_fact is not None:
            cards.append({"card_id": card_id, "type": evidence["type"],
                          "service": evidence.get("service"), "observed_at": evidence.get("observed_at"),
                          "fact": provider_fact})
            evidence_to_card_id[evidence["id"]] = card_id
    # Raw fact/log text, source URIs, actor/reason, timeline and trigger refs are
    # intentionally absent from the remote request. Only local rendering uses them.
    request = {"prompt_version": PROMPT_VERSION, "incident_id": "request-" + uuid4().hex,
               "revision": envelope["revision"], "state": ier["state"], "severity": ier["severity"],
               "availability": envelope["context"]["availability"],
               "affected_services": ier["affected_services"], "service_signals": ier["service_signals"],
               "root_cause_hypotheses": [{"service": row["service"], "score": row["score"],
                                           "evidence_refs": [evidence_to_card_id[ref] for ref in row["evidence_refs"]
                                                             if ref in evidence_to_card_id]}
                                         for row in ier["root_cause_ranking"]
                                         if any(ref in evidence_to_card_id
                                                for ref in row["evidence_refs"])], "cards": cards}
    return request, rendered, evidence_to_card_id


def _fallback_root_cause(envelope: dict) -> dict:
    ranking = envelope["payload"]["root_cause_ranking"]
    if not ranking:
        return {"service": None, "score": None,
                "explanation": "Chưa có giả thuyết nguyên nhân gốc rễ được xếp hạng cho phiên bản incident này.",
                "evidence_refs": []}
    top = ranking[0]
    return {"service": top["service"], "score": top["score"],
            "explanation": (f"{top['service']} đang là giả thuyết RCA được xếp hạng cao nhất trong dữ liệu đã ghi. "
                            "Đây chưa phải nguyên nhân được xác nhận; cần người vận hành xem lại bằng chứng."),
            "evidence_refs": list(top["evidence_refs"])}


def _render(envelope: dict, selected: list[str], cards: dict[str, dict], root_cause: dict,
            ai_generated: bool) -> dict:
    ier = envelope["payload"]
    summary = (f"Incident {envelope['incident_id']}, revision {envelope['revision']}: "
               f"state={ier['state']}; severity={ier['severity']}; "
               f"availability={envelope['context']['availability']}. ")
    if ai_generated and root_cause["service"] is not None:
        summary += "AI đã tổng hợp lời giải thích từ giả thuyết RCA được xếp hạng và bằng chứng đã ghi; điểm RCA không phải xác suất hay nguyên nhân đã xác nhận. "
    elif ai_generated:
        summary += "AI không chọn được giả thuyết nguyên nhân gốc rễ từ bằng chứng hiện có. "
    elif ier["root_cause_ranking"]:
        summary += "Các giả thuyết RCA đã xếp hạng chưa phải nguyên nhân được xác nhận. "
    else:
        summary += "Chưa có giả thuyết nguyên nhân gốc rễ được xếp hạng. "
    summary += "Bằng chứng là nội dung đã ghi, chưa được xác minh độc lập. Phần giải thích này không cho phép hành động."
    summary += f" Hiển thị {len(selected)} trên {len(ier['evidence'])} mục bằng chứng đã ghi."
    if not cards:
        summary += " Không có thẻ bằng chứng phù hợp giới hạn gửi cho provider."
    payload = {"schema_version": "2.0", "incident_id": envelope["incident_id"], "summary": summary,
               "root_cause": deepcopy(root_cause), "claims": [deepcopy(cards[key]) for key in selected]}
    validate_payload("explanation.schema.json", payload)
    return payload


async def explain_incident(envelope: dict, *, provider: ExplanationProvider | None = None) -> dict:
    """Implement H2. Invalid input fails; provider failure never changes incident facts.

    B owns authentication/loading and the HTTP revision wrapper. This module has
    no persistence, cache, API settings, filesystem or cloud credential access.
    """
    snapshot = deepcopy(envelope)
    validate_payload("incident-envelope.schema.json", snapshot)
    request, cards, evidence_to_card_id = _cards(snapshot)
    selected = list(cards)[:MAX_CLAIMS]
    root_cause = _fallback_root_cause(snapshot)
    ai_generated = False
    reason, used_provider = "provider_absent", FALLBACK_PROVIDER
    if provider is not None:
        if not isinstance(provider, ExplanationProvider):
            raise ValueError("provider must use the bounded ExplanationProvider adapter")
        if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) for name in _request_services(request)):
            reason = "unsupported_service_identifier"
        elif not request["cards"]:
            reason = "no_bounded_cards"
        elif len(json.dumps(request, allow_nan=False).encode()) > MAX_BYTES:
            reason = "request_budget"
        else:
            try:
                selection = await provider.select(request)
                selected, root_cause = selection["card_ids"], selection["root_cause"]
                card_id_to_evidence = {card_id: evidence_id
                                       for evidence_id, card_id in evidence_to_card_id.items()}
                root_cause = {**root_cause, "evidence_refs": [
                    card_id_to_evidence[card_id] for card_id in root_cause["evidence_refs"]]}
                ai_generated = True
                used_provider, reason = provider.provider_id, None
            except ProviderFailure as exc:
                reason = exc.reason
    payload = _render(snapshot, selected, cards, root_cause, ai_generated)
    LOG.info("incident_explanation", extra={"incident_id": snapshot["incident_id"],
             "revision": snapshot["revision"], "prompt_version": PROMPT_VERSION,
             "provider": used_provider, "requested_provider": provider.provider_id if provider else None,
             "fallback_reason": reason})
    return {"payload": payload, "provider": used_provider, "fallback": reason is not None}
