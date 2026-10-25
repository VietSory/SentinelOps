import json
from pathlib import Path

import pytest

from scripts.replay import validate_jsonl

SAMPLE = Path(__file__).resolve().parents[1]/"samples"/"telemetry.sample.json"


def test_jsonl_admission_streams_and_matches_api_semantics(tmp_path):
    value = json.loads(SAMPLE.read_text())
    path = tmp_path/"run.jsonl"
    path.write_text("\n"+json.dumps(value)+"\n\n"+json.dumps(value)+"\n")
    assert validate_jsonl(path) == 2
    value["p95_latency_ms"] = float("nan")
    path.write_text(json.dumps(value)+"\n")
    with pytest.raises(ValueError, match="line 1.*non-finite"):
        validate_jsonl(path)
    path.write_text("\n{invalid\n")
    with pytest.raises(ValueError, match="line 2"):
        validate_jsonl(path)
