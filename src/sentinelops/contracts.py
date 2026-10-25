from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9-]+\.schema\.json", name):
        raise ValueError("invalid schema name")
    bundled = files("sentinelops").joinpath("schemas", name)
    if bundled.is_file():
        return json.loads(bundled.read_text(encoding="utf-8"))
    # Editable source checkout: the root contracts remain the only authority.
    root = Path(__file__).resolve().parents[2]
    path = root / "contracts" / name
    if not path.is_file():
        raise ValueError(f"unknown schema: {name}")
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
