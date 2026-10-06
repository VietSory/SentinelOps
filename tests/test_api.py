import json
from pathlib import Path

from fastapi.testclient import TestClient

from sentinelops.main import app

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_detect_skeleton_respects_contract():
    payload = json.loads((ROOT / "samples" / "feature-window.sample.json").read_text(encoding="utf-8"))
    response = client.post("/v1/detect", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "isolation-forest"
    assert body["service"] == "payment-service"


def test_incident_contract_endpoint():
    payload = json.loads((ROOT / "samples" / "incident-evidence-record.sample.json").read_text(encoding="utf-8"))
    response = client.post("/v1/incidents", json=payload)
    assert response.status_code == 200
    assert response.json()["incident_id"] == "INC-DEMO-001"
