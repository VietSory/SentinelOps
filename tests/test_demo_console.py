"""Offline console checks: no sockets to services, kubectl, or real subprocesses."""

import asyncio
import json
import subprocess
from contextlib import contextmanager
from dataclasses import replace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scripts.demo_console import app as console_app
from scripts.demo_console.app import ACTIONS, DeploymentRunner, Settings, create_app
from services.common.faults import FaultRequest


BASE = Settings(payment_url="http://payment.test", inventory_url="http://inventory.test")
HEADERS = {"origin": BASE.origin, "x-console-action": "apply"}


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A test attempted to run a real process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@contextmanager
def console(*, settings=BASE, handler=None, runner=None, peer=("127.0.0.1", 50000)):
    states = {service: {"service": service + "-service", "mode": "healthy", "delay_ms": 0}
              for service in ("payment", "inventory")}
    calls = []

    def upstream(request):
        assert request.url.path == "/internal/faults"
        assert request.url.host in {"payment.test", "inventory.test"}
        service = request.url.host.split(".")[0]
        calls.append((request.method, service, json.loads(request.content) if request.content else None))
        if handler:
            response = handler(request, service, states)
            if response is not None:
                return response
        if request.method == "POST":
            payload = FaultRequest.model_validate_json(request.content).model_dump()
            states[service].update(payload)
        return httpx.Response(200, json=states[service])

    app = create_app(settings, transport=httpx.MockTransport(upstream), runner=runner)
    with TestClient(app, base_url=settings.origin, client=peer) as client:
        yield client, states, calls


def test_static_page_and_assets_are_local_and_do_not_contact_services():
    with console() as (client, _, calls):
        page = client.get("/")
        assert page.status_code == 200
        assert "Fault injection disrupts live services." in page.text
        assert 'lang="en"' in page.text
        assert "connect-src 'self'" in page.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["x-content-type-options"] == "nosniff"
        for name in ("app.js", "styles.css"):
            assert client.get("/assets/" + name).status_code == 200
        for path in ("/assets/app.py", "/assets/README.md", "/assets/..%2Fapp.py", "/docs", "/openapi.json"):
            assert client.get(path).status_code == 404
        assert calls == []


@pytest.mark.parametrize("value,enabled", [(None, False), ("false", False), ("1", False),
                                         ("yes", False), ("true ", False), ("true", True), ("TRUE", True)])
def test_explicit_enable_environment(monkeypatch, value, enabled):
    monkeypatch.delenv("DEMO_CONSOLE_ENABLE_FAULTS", raising=False)
    if value is not None:
        monkeypatch.setenv("DEMO_CONSOLE_ENABLE_FAULTS", value)
    assert Settings.from_env().enabled is enabled


@pytest.mark.parametrize("action", ACTIONS, ids=lambda action: action.id)
def test_every_mutation_disabled_by_default(action):
    with console() as (client, _, calls):
        response = client.post("/api/actions/" + action.id, headers=HEADERS)
        assert response.status_code == 403
        assert "DEMO_CONSOLE_ENABLE_FAULTS=true" in response.json()["detail"]
        assert calls == []


def test_status_always_reads_each_service_and_contains_eight_real_actions():
    with console() as (client, states, calls):
        first = client.get("/api/status").json()
        assert first["enabled"] is False
        assert len(first["actions"]) == 8
        assert first["deployment"]["configured"] is False
        assert first["services"]["payment"]["mode"] == "healthy"
        states["payment"].update(mode="latency", delay_ms=2000)
        second = client.get("/api/status").json()
        assert second["services"]["payment"]["mode"] == "latency"
        assert second["services"]["payment"]["delay_ms"] == 2000
        assert second["services"]["payment"]["observed_at"]
        assert calls == [("GET", "payment", None), ("GET", "inventory", None)] * 2
        for card in first["actions"]:
            if card["fault"]:
                assert FaultRequest.model_validate(card["fault"]).model_dump() == card["fault"]


@pytest.mark.parametrize("action", [a for a in ACTIONS if a.mode], ids=lambda action: action.id)
def test_card_posts_supported_payload_and_verifies_by_get(action):
    with console(settings=replace(BASE, enabled=True)) as (client, states, calls):
        for state in states.values():
            state.update(mode="latency", delay_ms=3500)
        response = client.post("/api/actions/" + action.id, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["verified"] is True
        posts = [(service, payload) for method, service, payload in calls if method == "POST"]
        assert posts == [(service, action.payload()) for service in action.services]
        for service in action.services:
            assert response.json()["services"][service]["mode"] == action.mode
            assert states[service]["delay_ms"] == action.delay_ms
        assert calls[-2:] == [("GET", "payment", None), ("GET", "inventory", None)]


@pytest.mark.parametrize("kind", ["connect", "timeout", "404", "503", "redirect", "bad-json", "wrong-service", "bad-mode", "bad-delay", "missing-field"])
def test_status_reports_unavailable_without_fabricated_healthy(kind):
    def broken(request, service, states):
        if service != "payment":
            return None
        if kind == "connect":
            raise httpx.ConnectError("refused", request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if kind in {"404", "503"}:
            return httpx.Response(int(kind))
        if kind == "redirect":
            return httpx.Response(307, headers={"location": "http://untrusted.test"})
        if kind == "bad-json":
            return httpx.Response(200, text="not json")
        payload = dict(states[service])
        if kind == "wrong-service":
            payload["service"] = "inventory-service"
        elif kind == "bad-mode":
            payload["mode"] = "cpu"
        elif kind == "bad-delay":
            payload["delay_ms"] = "950"
        else:
            payload.pop("mode")
        return httpx.Response(200, json=payload)

    with console(handler=broken) as (client, _, calls):
        response = client.get("/api/status")
        assert response.status_code == 200
        body = response.json()
        payment = body["services"]["payment"]
        assert payment["ok"] is False
        assert payment["mode"] is None and payment["delay_ms"] is None
        assert payment["observed_at"] is None and payment["error"]
        assert body["services"]["inventory"]["ok"] is True
        if kind == "404":
            assert "ALLOW_DEV_FAULT_INJECTION=true" in payment["error"]
        if kind in {"connect", "timeout"}:
            assert "port-forward" in payment["error"] and "http://payment.test" in payment["error"]
        assert len(calls) == 2


def test_wrong_service_identity_blocks_post():
    def wrong(request, service, states):
        return httpx.Response(200, json=states["inventory"])
    with console(settings=replace(BASE, enabled=True), handler=wrong) as (client, _, calls):
        response = client.post("/api/actions/payment-error", headers=HEADERS)
        assert response.status_code == 502
        assert not response.json()["results"]["payment"]["acknowledged"]
        assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_partial_write_attempts_both_services_and_reports_each(failure):
    def partial(request, service, states):
        if request.method == "POST" and service == "payment":
            if failure == "timeout":
                raise httpx.ReadTimeout("lost reply", request=request)
            return httpx.Response(503)
    with console(settings=replace(BASE, enabled=True), handler=partial) as (client, states, calls):
        for state in states.values():
            state.update(mode="error")
        response = client.post("/api/actions/recover", headers=HEADERS)
        assert response.status_code == 502
        body = response.json()
        assert body["verified"] is False
        assert body["results"]["payment"]["acknowledged"] is False
        assert body["results"]["inventory"]["acknowledged"] is True
        assert states["payment"]["mode"] == "error"
        assert states["inventory"]["mode"] == "healthy"
        assert len([call for call in calls if call[0] == "POST"]) == 2


def test_successful_post_echo_does_not_replace_current_get_observation():
    def no_effect(request, service, states):
        if request.method == "POST":
            return httpx.Response(200, json={"service": service + "-service", "mode": "error", "delay_ms": 0})
    with console(settings=replace(BASE, enabled=True), handler=no_effect) as (client, _, _):
        response = client.post("/api/actions/payment-error", headers=HEADERS)
        assert response.status_code == 502
        assert response.json()["results"]["payment"]["acknowledged"] is True
        assert response.json()["services"]["payment"]["mode"] == "healthy"


def test_post_acknowledgement_followed_by_unreadable_get_is_not_success():
    written = False

    def lost(request, service, states):
        nonlocal written
        if service == "payment":
            if request.method == "POST":
                written = True
            elif written:
                raise httpx.ConnectError("rollout", request=request)
    with console(settings=replace(BASE, enabled=True), handler=lost) as (client, _, _):
        response = client.post("/api/actions/payment-error", headers=HEADERS)
        assert response.status_code == 502
        assert response.json()["services"]["payment"]["mode"] is None


@pytest.mark.parametrize("headers", [{}, {"origin": "https://evil.test", "x-console-action": "apply"},
                                     {"origin": BASE.origin},
                                     {**HEADERS, "sec-fetch-site": "cross-site"}])
def test_cross_origin_or_unmarked_posts_are_blocked(headers):
    with console(settings=replace(BASE, enabled=True)) as (client, _, calls):
        assert client.post("/api/actions/payment-error", headers=headers).status_code == 403
        assert calls == []


def test_host_rebinding_and_non_loopback_client_are_blocked():
    with console(settings=replace(BASE, enabled=True)) as (client, _, calls):
        assert client.get("/api/status", headers={"host": "evil.test"}).status_code == 400
        assert calls == []
    with console(peer=("192.0.2.2", 50000)) as (client, _, calls):
        assert client.get("/api/status").status_code == 403
        assert calls == []


def test_unknown_action_does_not_dispatch_and_no_get_can_mutate():
    with console(settings=replace(BASE, enabled=True)) as (client, _, calls):
        assert client.post("/api/actions/arbitrary-command", headers=HEADERS).status_code == 404
        assert client.get("/api/actions/payment-error").status_code == 405
        assert calls == []


@pytest.mark.parametrize("url", ["file:///tmp/faults", "http://user:secret@localhost", "http://localhost/path", "http://localhost/?q=x", "http://localhost/#fragment", "http://localhost:invalid", "http://"])
def test_invalid_target_urls_fail_at_configuration(url):
    with pytest.raises(ValueError):
        replace(BASE, payment_url=url)


def test_configuration_and_loopback_launcher(monkeypatch):
    monkeypatch.setenv("DEMO_CONSOLE_PAYMENT_URL", "https://payment.test/")
    monkeypatch.setenv("DEMO_CONSOLE_INVENTORY_URL", "http://inventory.test:9000")
    monkeypatch.setenv("DEMO_CONSOLE_PORT", "8877")
    monkeypatch.setenv("DEMO_CONSOLE_DEPLOY_SCRIPT", "/opt/demo/b7_run.sh")
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    settings = Settings.from_env()
    assert settings.endpoint("payment") == "https://payment.test/internal/faults"
    assert settings.deploy_script == "/opt/demo/b7_run.sh"
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(kwargs))
    console_app.main()
    assert calls == [{"host": "127.0.0.1", "port": 8877, "proxy_headers": False, "workers": 1}]


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_console_port_is_rejected(port):
    with pytest.raises(ValueError):
        replace(BASE, port=port)


@pytest.mark.parametrize("script", ["", "relative/b7_run.sh", "/missing/path/b9_hold.sh"])
def test_unconfigured_deploy_is_clear_and_never_spawns(script):
    with console(settings=replace(BASE, enabled=True, deploy_script=script)) as (client, _, calls):
        assert client.get("/api/status").json()["deployment"]["configured"] is False
        response = client.post("/api/actions/deploy-bad-image", headers=HEADERS)
        assert response.status_code == 503
        assert "DEMO_CONSOLE_DEPLOY_SCRIPT" in response.json()["detail"]
        assert all(method == "GET" for method, _, _ in calls)


def test_deploy_uses_env_path_literal_argv_inherits_gates_and_tracks_real_process(monkeypatch, tmp_path):
    script = tmp_path / "b7 script;literal.sh"
    script.write_text("# Must never actually run\n", encoding="utf-8")
    monkeypatch.setenv("B7_EXECUTE", "1")
    monkeypatch.setenv("AWS_PROFILE", "console")
    calls = []

    class Process:
        code = None

        def poll(self):
            return self.code

    process = Process()

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        kwargs["stdout"].write(b"mock script output\n")
        kwargs["stdout"].flush()
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    runner = DeploymentRunner(str(script))
    try:
        with console(settings=replace(BASE, enabled=True), runner=runner) as (client, _, upstream):
            response = client.post("/api/actions/deploy-bad-image", headers=HEADERS)
            assert response.status_code == 202
            assert response.json()["deployment"]["state"] == "running"
            assert upstream == []
            assert calls[0][0] == ["bash", str(script)]
            kwargs = calls[0][1]
            assert kwargs.get("shell", False) is False
            assert kwargs["cwd"] == console_app.ROOT
            assert kwargs["env"]["B7_EXECUTE"] == "1"
            assert kwargs["env"]["AWS_PROFILE"] == "console"
            assert kwargs["start_new_session"] is True
            assert kwargs["stdin"] == subprocess.DEVNULL
            for action in ("deploy-bad-image", "payment-error", "recover"):
                assert client.post("/api/actions/" + action, headers=HEADERS).status_code == 409
            assert len(calls) == 1
            observed = client.get("/api/status").json()
            assert observed["deployment"]["log_tail"] == "mock script output\n"
            assert observed["services"]["payment"]["mode"] == "healthy"
            # Exit 2 is a script failure (e.g. missing its gate), not service health.
            process.code = 2
            final = client.get("/api/status").json()["deployment"]
            assert final["state"] == "exited" and final["returncode"] == 2
            assert client.post("/api/actions/recover", headers=HEADERS).status_code == 200
    finally:
        runner.output.close()


def test_deploy_spawn_failure_is_reported(monkeypatch, tmp_path):
    script = tmp_path / "b9_hold.sh"
    script.write_text("# no execution\n")

    def failed(*args, **kwargs):
        raise OSError("missing bash")

    monkeypatch.setattr(subprocess, "Popen", failed)
    runner = DeploymentRunner(str(script))
    with pytest.raises(HTTPException) as exc:
        runner.start()
    assert exc.value.status_code == 503
    assert runner.output is None and runner.process is None


def test_concurrent_mutations_rejected_while_status_remains_readable():
    async def check():
        entered = asyncio.Event()
        release = asyncio.Event()
        state = {"payment": "healthy", "inventory": "healthy"}

        async def upstream(request):
            service = request.url.host.split(".")[0]
            if request.method == "POST":
                entered.set()
                await release.wait()
                state[service] = "error"
            return httpx.Response(200, json={"service": service + "-service", "mode": state[service], "delay_ms": 0})

        app = create_app(replace(BASE, enabled=True), transport=httpx.MockTransport(upstream))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url=BASE.origin) as client:
                first = asyncio.create_task(client.post("/api/actions/payment-error", headers=HEADERS))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    second = await client.post("/api/actions/inventory-error", headers=HEADERS)
                    assert second.status_code == 409
                    status = await client.get("/api/status")
                    assert status.status_code == 200 and status.json()["busy"] is True
                finally:
                    release.set()
                    result = await first
                assert result.status_code == 200
                assert state["inventory"] == "healthy"

    asyncio.run(check())
