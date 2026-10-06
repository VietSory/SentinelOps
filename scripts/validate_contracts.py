from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
SAMPLES = ROOT / "samples"

SAMPLE_MAP = {
    "telemetry.sample.json": "telemetry.schema.json",
    "feature-window.sample.json": "feature-window.schema.json",
    "incident-evidence-record.sample.json": "incident-evidence-record.schema.json",
}


def main() -> None:
    errors: list[str] = []

    for schema_path in sorted(CONTRACTS.glob("*.schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            errors.append(f"invalid schema {schema_path.name}: {exc}")

    for sample_name, schema_name in SAMPLE_MAP.items():
        payload = json.loads((SAMPLES / sample_name).read_text(encoding="utf-8"))
        schema = json.loads((CONTRACTS / schema_name).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for err in validator.iter_errors(payload):
            errors.append(f"{sample_name} -> {schema_name}: {err.message}")

    if errors:
        raise SystemExit("\n".join(errors))

    print(f"OK: {len(list(CONTRACTS.glob('*.schema.json')))} schemas + {len(SAMPLE_MAP)} samples validated")


if __name__ == "__main__":
    main()
