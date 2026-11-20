"""Bounded OpenAI Responses-compatible transport for grounded explanations."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

import httpx

from sentinelops.explanations.bedrock import INSTRUCTION, _invalid_constant, _unique_object
from sentinelops.explanations.service import ExplanationProvider, MAX_BYTES, PROMPT_VERSION

# OpenAI rejects json_object unless the input text contains the word "json"; a schema format has no such rule.
TEXT_FORMAT = {"type": "json_schema", "name": "incident_explanation", "schema": {"type": "object"}, "strict": False}


class OpenAIResponsesRuntime:
    """Async Responses API client with explicit endpoint, key and no body storage."""

    def __init__(self, *, base_url: str, model: str, api_key: str,
                 actor_authorization: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        parts = urlsplit(base_url) if isinstance(base_url, str) else None
        if (parts is None or parts.scheme != "https" or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or any(char.isspace() for char in base_url)):
            raise ValueError("LLM base URL must be explicit credential-free HTTPS")
        if (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model)
            or not isinstance(api_key, str) or not re.fullmatch(r"[!-~]{8,4096}", api_key)):
            raise ValueError("explicit model and bounded API key are required")
        if actor_authorization is not None and (
            not isinstance(actor_authorization, str)
            or not re.fullmatch(r"[ -~]{1,256}", actor_authorization)
        ):
            raise ValueError("actor authorization header is invalid")
        self.model = model
        self._responses_url = base_url.rstrip("/") + "/responses"
        self._client = httpx.AsyncClient(
            headers={"Authorization": "Bearer " + api_key, "Accept-Encoding": "identity",
                     **({"x-openai-actor-authorization": actor_authorization}
                        if actor_authorization else {})},
            timeout=httpx.Timeout(8.0, connect=2.0),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def converse(self, *, model: str, instructions: str, input: str,
                       max_output_tokens: int = 512) -> dict:
        if model != self.model or len(input.encode()) > MAX_BYTES:
            raise ValueError("request model or input budget differs from configuration")
        body = {"model": self.model, "instructions": instructions, "input": input,
                "max_output_tokens": max_output_tokens, "store": False,
                "text": {"format": TEXT_FORMAT}}
        async with self._client.stream("POST", self._responses_url, json=body) as response:
            response.raise_for_status()
            if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
                raise ValueError("Responses API body has unsupported content encoding")
            if response.is_stream_consumed:
                raw = response.content
                if len(raw) > MAX_BYTES:
                    raise ValueError("Responses API body exceeds byte limit")
            else:
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    if len(raw) + len(chunk) > MAX_BYTES:
                        raise ValueError("Responses API body exceeds byte limit")
                    raw.extend(chunk)
        return json.loads(bytes(raw), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)

    async def close(self):
        await self._client.aclose()


def openai_responses_provider(*, base_url: str, model: str, api_key: str,
                              actor_authorization: str | None = None,
                              transport: httpx.AsyncBaseTransport | None = None):
    runtime = OpenAIResponsesRuntime(base_url=base_url, model=model, api_key=api_key,
                                     actor_authorization=actor_authorization, transport=transport)
    credentials = set()
    for credential in (api_key, actor_authorization):
        if credential:
            credentials.add(credential)
            stripped = credential.strip()
            if stripped:
                credentials.add(stripped)
    identity = {"protocol": "openai-responses/v1", "base_url": base_url.rstrip("/"),
                "model": model, "credential_fingerprint": hashlib.sha256(api_key.encode()).hexdigest(),
                "prompt_version": PROMPT_VERSION,
                "instructions": INSTRUCTION, "store": False, "max_output_tokens": 512}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    async def invoke(request: dict) -> dict:
        response = await runtime.converse(model=model, instructions=INSTRUCTION,
                                          input=json.dumps(request, ensure_ascii=False, allow_nan=False))
        if not isinstance(response, dict) or response.get("status") != "completed":
            raise ValueError("Responses API did not complete")
        text = response.get("output_text")
        blocks = []
        output = response.get("output", [])
        if not isinstance(output, list):
            raise ValueError("Responses API returned invalid output")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            if item.get("status", "completed") != "completed":
                raise ValueError("Responses API message did not complete")
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal":
                    raise ValueError("Responses API refused the request")
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    blocks.append(part["text"])
        if text is None:
            text = "\n".join(blocks)
        if not isinstance(text, str) or not text or len(text.encode()) > MAX_BYTES:
            raise ValueError("Responses API returned empty or oversized output")
        if any(credential in candidate for candidate in (text, *blocks) for credential in credentials):
            raise ValueError("Responses API returned invalid output")
        parsed = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        pending = [parsed]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                if any(credential in value for credential in credentials):
                    raise ValueError("Responses API returned invalid output")
            elif isinstance(value, dict):
                pending.extend(value)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return parsed

    return runtime, ExplanationProvider("openai-responses/v1/" + digest, invoke)
