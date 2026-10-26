import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinelops.contracts import validate_payload
from sentinelops.main import app
from sentinelops.api import create_app
from sentinelops.api_settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def sample(name):
    return json.loads((ROOT / "samples" / f"{name}.sample.json").read_text())


@pytest.mark.parametrize("mutation", ["duplicate", "dangling", "empty", "unordered"])
def test_incident_references_and_ranking_are_semantic_not_just_schema(mutation):
    payload = sample("incident-evidence-record")
    if mutation == "duplicate":
        payload["evidence"].append(payload["evidence"][0])
    elif mutation == "dangling":
        payload["root_cause_ranking"][0]["evidence_refs"] = ["DOES-NOT-EXIST"]
    elif mutation == "empty":
        payload["root_cause_ranking"][0]["evidence_refs"] = []
    else:
        payload["root_cause_ranking"].reverse()
    with pytest.raises(ValueError):
        validate_payload("incident-evidence-record.schema.json", payload)
    class NoWrites:
        async def check_ready(self): pass
        async def write(self,command):
            pytest.fail("invalid IER reached persistence")
    token="contract-test-token-"+"x"*32
    api=TestClient(create_app(Settings(api_token=token),repository=NoWrites()))
    assert api.post("/v1/incidents",json=payload,headers={"authorization":"Bearer "+token,
        "if-match":'"0"',"idempotency-key":"invalid-contract-fixture"}).status_code == 422


def test_time_bounds_nonfinite_numbers_and_non_utc_are_rejected():
    for mutation in ("reversed", "nan", "offset"):
        payload = sample("feature-window")
        if mutation == "reversed":
            payload["window_start"] = payload["window_end"]
        elif mutation == "nan":
            payload["features"]["bad"] = float("nan")
        else:
            payload["window_start"] = "2026-10-06T14:15:00+07:00"
        with pytest.raises(ValueError):
            validate_payload("feature-window.schema.json", payload)
