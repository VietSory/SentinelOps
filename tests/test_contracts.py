import json
from pathlib import Path

import pytest

from sentinelops.contracts import load_schema, validate_payload

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("sample", "schema"),
    [
        ("telemetry.sample.json", "telemetry.schema.json"),
        ("feature-window.sample.json", "feature-window.schema.json"),
        ("incident-evidence-record.sample.json", "incident-evidence-record.schema.json"),
        ("observation-envelope.sample.json", "observation-envelope.schema.json"),
        ("feature-envelope.sample.json", "feature-envelope.schema.json"),
    ],
)
def test_samples_match_contracts(sample: str, schema: str):
    payload = json.loads((ROOT / "samples" / sample).read_text(encoding="utf-8"))
    validate_payload(schema, payload)


def test_invalid_error_rate_is_rejected():
    payload = json.loads((ROOT / "samples" / "telemetry.sample.json").read_text(encoding="utf-8"))
    payload["error_rate"] = 1.5
    with pytest.raises(ValueError):
        validate_payload("telemetry.schema.json", payload)


def test_schema_loading_rejects_unknown_names_and_path_traversal():
    for name in ("../../config.json", "not-present.schema.json"):
        with pytest.raises(ValueError):
            load_schema(name)
