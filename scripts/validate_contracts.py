from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from sentinelops.contracts import validate_payload

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
SAMPLES = ROOT / "samples"
CONFIG_MAP = {"training_data_contract.json": "training-data-policy.schema.json"}

SAMPLE_MAP = {
    "telemetry.sample.json": "telemetry.schema.json",
    "feature-window.sample.json": "feature-window.schema.json",
    "incident-evidence-record.sample.json": "incident-evidence-record.schema.json",
    "observation-envelope.sample.json": "observation-envelope.schema.json",
    "feature-envelope.sample.json": "feature-envelope.schema.json",
    "infrastructure-observation.sample.json": "infrastructure-observation.schema.json",
    "experiment-run.sample.json": "experiment-run.schema.json",
    "dataset-manifest.sample.json": "dataset-manifest.schema.json",
    "incident-write.sample.json": "incident-write.schema.json",
    "incident-envelope.sample.json": "incident-envelope.schema.json",
    "incident-list.sample.json": "incident-list.schema.json",
    "api-error.sample.json": "api-error.schema.json",
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
        try:
            validate_payload(schema_name, payload)
        except ValueError as exc:
            errors.append(f"{sample_name} -> {schema_name}: {exc}")

    for config_name, schema_name in CONFIG_MAP.items():
        payload = json.loads((ROOT / "configs" / config_name).read_text(encoding="utf-8"))
        try:
            validate_payload(schema_name, payload)
        except ValueError as exc:
            errors.append(f"{config_name} -> {schema_name}: {exc}")

    if errors:
        raise SystemExit("\n".join(errors))

    print(f"OK: {len(list(CONTRACTS.glob('*.schema.json')))} schemas + {len(SAMPLE_MAP)} samples + {len(CONFIG_MAP)} configurations validated")


if __name__ == "__main__":
    main()
