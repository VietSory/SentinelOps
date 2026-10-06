from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    path = root / "contracts" / name
    return json.loads(path.read_text(encoding="utf-8"))


def validate_payload(schema_name: str, payload: dict[str, Any]) -> None:
    validator = Draft202012Validator(
        load_schema(schema_name),
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        messages = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
        raise ValueError("; ".join(messages))
