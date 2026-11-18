import json

import pytest
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
    prod=TestClient(create_app(Settings(environment="aws"),repository=Ready()))
    assert prod.get("/ready/incidents",headers={"authorization":"Bearer "+token}).status_code==503


def test_demo_cannot_be_enabled_outside_sandbox_and_weak_token_is_rejected():
    import pytest
    for settings in ({"environment":"aws","demo_mode":True},{"api_token":"short"},
                     {"environment":"aws","api_token":"x"*32},{"environment":"aws","operator_token":"y"*32}):
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


def test_relative_alibaba_model_rejects_single_window_direct_inference_with_a_pointer():
    class Relative:
        available, is_alibaba = True, True

        def infer(self, *args, **kwargs):  # must never be reached
            raise AssertionError("direct inference was attempted")

    payload = json.loads((ROOT / "samples" / "feature-window.sample.json").read_text())
    api = TestClient(create_app(Settings(), model_registry=Relative()))
    for route in ("/v1/detect", "/v1/risk"):
        response = api.post(route, json=payload)
        assert response.status_code == 409 and response.json()["code"] == "model_requires_stream_history"
        assert "service_signals" in response.json()["message"]
        assert api.post(route, json={}).status_code == 422
    assert api.get("/ready").json()["direct_inference"] is False


@pytest.mark.parametrize('database_url', [
    'postgresql://control.example.invalid:5432/sentinelops_control?sslmode=verify-full',
    'postgres://control.example.invalid:5432/sentinelops_control?sslmode=verify-full',
])
def test_remote_control_database_options_accept_the_worker_rds_contract(tmp_path, database_url):
    import os, ssl
    from sentinelops.api import control_database_options
    assert control_database_options(Settings()) == {}
    secret = tmp_path / 'database-password'
    secret.write_text('s3cret-value\n'); os.chmod(secret, 0o600)
    options = control_database_options(Settings(environment='aws', control_database_url=database_url,
                                                control_database_user='control_writer',
                                                control_database_password_file=str(secret)))
    assert options['user'] == 'control_writer' and options['password'] == 's3cret-value'
    assert isinstance(options['ssl'], ssl.SSLContext) and options['ssl'].check_hostname


@pytest.mark.parametrize(('database_url', 'database_user'), [
    ('postgresql://control_writer:secret@control.example.invalid:5432/sentinelops_control?sslmode=verify-full',
     'control_writer'),
    ('postgresql://control.example.invalid:5432/sentinelops_control?sslmode=verify-full', 'other_writer'),
    ('postgresql://control.example.invalid:5432/other?sslmode=verify-full', 'control_writer'),
    ('postgresql://control.example.invalid/sentinelops_control?sslmode=verify-full', 'control_writer'),
    ('postgresql://control.example.invalid:5432/sentinelops_control?sslmode=disable', 'control_writer'),
    ('postgresql://control.example.invalid:5432/sentinelops_control', 'control_writer'),
    ('postgresql://control.example.invalid:5432/sentinelops_control?sslmode=verify-full&application_name=api',
     'control_writer'),
    ('postgresql://control.example.invalid:5432/sentinelops_control?sslmode=verify-full#fragment',
     'control_writer'),
])
def test_remote_control_database_options_reject_each_invalid_rds_contract(tmp_path, database_url, database_user):
    import os
    from sentinelops.api import control_database_options
    secret = tmp_path / 'database-password'
    secret.write_text('s3cret-value\n'); os.chmod(secret, 0o600)
    with pytest.raises(ValueError):
        control_database_options(Settings(environment='aws', control_database_url=database_url,
                                          control_database_user=database_user,
                                          control_database_password_file=str(secret)))


def test_remote_control_database_options_reject_a_nonprivate_password_file(tmp_path):
    import os
    from sentinelops.api import control_database_options
    secret = tmp_path / 'database-password'
    secret.write_text('s3cret-value\n'); os.chmod(secret, 0o644)
    with pytest.raises(ValueError):
        control_database_options(Settings(environment='aws',
                                          control_database_url='postgresql://control.example.invalid:5432/sentinelops_control?sslmode=verify-full',
                                          control_database_user='control_writer',
                                          control_database_password_file=str(secret)))


def test_invalid_remote_control_database_configuration_never_creates_a_pool(tmp_path, monkeypatch, caplog):
    import logging
    import os
    from unittest.mock import AsyncMock
    import sentinelops.api as api_module

    secret = tmp_path / 'database-password'
    secret.write_text('s3cret-value\n'); os.chmod(secret, 0o600)
    create_pool = AsyncMock()
    monkeypatch.setattr(api_module.asyncpg, 'create_pool', create_pool)
    settings = Settings(environment='aws',
                        control_database_url='postgresql://control.example.invalid:5432/other?sslmode=verify-full',
                        control_database_user='control_writer', control_database_password_file=str(secret))
    caplog.set_level(logging.ERROR, logger='sentinelops-api')
    with TestClient(create_app(settings)):
        pass
    create_pool.assert_not_awaited()
    assert 'control_store_configuration_invalid' in caplog.text


@pytest.mark.parametrize('settings', [
    {'control_database_user': 'control_writer'},
    {'control_database_password_file': '/credentials/database-password'},
    {'control_database_user': 'control_writer', 'control_database_password_file': '/credentials/database-password'},
])
def test_remote_control_database_settings_must_be_complete(settings):
    with pytest.raises(ValueError):
        Settings(**settings)
