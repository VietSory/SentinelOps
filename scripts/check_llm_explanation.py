"""Check a live OpenAI-compatible explanation using the SentinelOps settings."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

from sentinelops.api_settings import Settings
from sentinelops.explanations import explain_incident
from sentinelops.explanations.openai_compatible import openai_responses_provider


DEFAULT_ENVELOPE = Path(__file__).resolve().parents[1] / "samples/incident-envelope.sample.json"


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, "Invalid arguments. See --help.\n")


async def check_explanation(settings: Settings, envelope: dict) -> dict:
    if settings.llm_base_url is None:
        return await explain_incident(envelope)
    runtime, provider = openai_responses_provider(
        base_url=settings.llm_base_url, model=settings.llm_model, api_key=settings.llm_api_key,
    )
    try:
        return await explain_incident(envelope, provider=provider)
    finally:
        await runtime.close()


def print_result(result: dict, settings: Settings) -> bool:
    secrets = sorted({value for value in (settings.llm_api_key, settings.llm_actor_authorization)
                      if value}, key=len, reverse=True)
    if not secrets:
        print(json.dumps(result, ensure_ascii=False))
        return False

    pattern = re.compile("|".join(re.escape(secret) for secret in secrets))

    def redact(value):
        if isinstance(value, str):
            return pattern.sub("[REDACTED]", value)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    safe_result = redact(result)
    output = json.dumps(safe_result, ensure_ascii=False)
    if any(secret in output or json.dumps(secret, ensure_ascii=False)[1:-1] in output
           for secret in secrets):
        return True
    print(output)
    return safe_result != result


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--envelope", type=Path, default=DEFAULT_ENVELOPE)
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ValueError:
        print("Invalid SentinelOps settings.", file=sys.stderr)
        return 1

    try:
        envelope = json.loads(args.envelope.read_text(encoding="utf-8"))
        result = asyncio.run(check_explanation(settings, envelope))
    except Exception:
        print("Could not complete the explanation check.", file=sys.stderr)
        return 1

    root_cause = result["payload"]["root_cause"]
    narrative = root_cause["explanation"]
    output = {
        "provider": result["provider"],
        "fallback": result["fallback"],
        "root_cause": {"service": root_cause["service"], "score": root_cause["score"]},
        "evidence_refs": root_cause["evidence_refs"],
        "narrative_length": len(narrative),
        "narrative": narrative,
    }
    reason = result.get("fallback_reason")
    if result["fallback"] and isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
        output["fallback_reason"] = reason
    if print_result(output, settings):
        return 3
    if result["fallback"]:
        return 2
    return 0 if result["fallback"] is False and result["provider"].startswith("openai-responses/v1/") else 1


if __name__ == "__main__":
    sys.exit(main())
