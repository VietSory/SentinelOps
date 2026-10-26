import json
from pathlib import Path

from fastapi.testclient import TestClient

from sentinelops.main import app
from sentinelops.api import create_app
from sentinelops.api_settings import Settings

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_detect_skeleton_respects_contract():
    payload = json.loads((ROOT / "samples" / "feature-window.sample.json").read_text(encoding="utf-8"))
    demo = TestClient(create_app(Settings(demo_mode=True)))
    response = demo.post("/v1/detect", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "isolation-forest"
    assert body["service"] == "payment-service"
    assert response.headers["x-sentinelops-mode"]=="demo"


def test_unconfigured_incident_endpoint_never_echoes_as_a_successful_write():
    payload = json.loads((ROOT / "samples" / "incident-evidence-record.sample.json").read_text(encoding="utf-8"))
    response = client.post("/v1/incidents", json=payload)
    assert response.status_code == 503
    assert response.json()["code"]=="authentication_unavailable"


def test_default_api_fails_closed_without_models_and_disables_demo():
    payload = json.loads((ROOT/"samples/feature-window.sample.json").read_text())
    assert client.post("/v1/detect",json=payload).status_code==503
    assert client.post("/v1/detect",json={}).status_code==422
    assert client.post("/v1/incidents/demo").status_code==404
    ready = client.get("/ready")
    assert ready.status_code==503 and not ready.json()["dependencies"]["model"]


def test_storage_errors_hide_dsn_and_preserve_liveness():
    class Broken:
        async def check_ready(self):
            raise RuntimeError("postgresql://user:SECRET@internal/control")
    token="x"*32
    local=TestClient(create_app(Settings(api_token=token),repository=Broken()))
    response=local.get("/ready/incidents",headers={"authorization":"Bearer "+token})
    assert response.status_code==503 and "SECRET" not in response.text
    assert response.json()["request_id"]==response.headers["x-request-id"]
    assert local.get("/health").status_code==200


def test_auth_missing_invalid_schemes_and_production_adapter_fail_closed():
    class Ready:
        async def check_ready(self): pass
    token="x"*32
    local=TestClient(create_app(Settings(api_token=token),repository=Ready()))
    for headers in ({},{"authorization":"Bearer wrong"},{"authorization":"Basic abc"}):
        response=local.get("/ready/incidents",headers=headers)
        assert response.status_code==401 and response.headers["www-authenticate"]=="Bearer"
    prod=TestClient(create_app(Settings(environment="aws",api_token=token),repository=Ready()))
    assert prod.get("/ready/incidents",headers={"authorization":"Bearer "+token}).status_code==503


def test_demo_cannot_be_enabled_outside_sandbox_and_weak_token_is_rejected():
    import pytest
    for settings in ({"environment":"aws","demo_mode":True},{"api_token":"short"}):
        with pytest.raises(ValueError):
            Settings(**settings)


def test_request_body_limit_applies_to_chunked_data_without_trusting_content_length():
    import asyncio
    import httpx
    async def check():
        async def chunks():
            yield b"x"*600_000
            yield b"x"*600_000
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(Settings())),base_url="http://api") as caller:
            response=await caller.post("/v1/incidents",content=chunks())
            assert response.status_code==413 and response.json()["code"]=="body_too_large"
            assert response.headers["x-request-id"]==response.json()["request_id"]
    asyncio.run(check())


def test_unexpected_repository_failure_is_sanitized_with_request_identity(caplog):
    class Broken:
        async def check_ready(self): pass
        async def get(self,*args):
            raise RuntimeError("postgresql://user:SECRET@internal/db")
    token="x"*32
    api=TestClient(create_app(Settings(api_token=token),repository=Broken()))
    response=api.get("/v1/incidents/fixture",headers={"authorization":"Bearer "+token})
    assert response.status_code==500 and "SECRET" not in response.text and "SECRET" not in caplog.text
    assert response.headers["x-request-id"]==response.json()["request_id"]
