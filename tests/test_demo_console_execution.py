"""Execution traces and independent OOM adapter; all side effects are mocked."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.demo_console.app import (
    DeploymentRunner, ExecutionJournal, Settings, create_app, safe_output,
)


BASE = Settings(enabled=True, payment_url="http://payment.test", inventory_url="http://inventory.test")
HEADERS = {"origin": BASE.origin, "x-console-action": "apply"}


@pytest.fixture(autouse=True)
def prevent_processes(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No real scenario subprocess may run in these tests")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@contextmanager
def console(settings=BASE, *, upstream=None, runner=None, oom_runner=None):
    state = {service: {"service": service + "-service", "mode": "healthy", "delay_ms": 0}
             for service in ("payment", "inventory")}
    calls = []

    def handle(request):
        service = request.url.host.split(".")[0]
        calls.append((request.method, service))
        if upstream:
            result = upstream(request, state)
            if result is not None:
                return result
        if request.method == "POST":
            state[service].update(json.loads(request.content))
        return httpx.Response(200, json=state[service])

    app = create_app(settings, transport=httpx.MockTransport(handle), runner=runner, oom_runner=oom_runner)
    with TestClient(app, base_url=settings.origin, client=("127.0.0.1", 1000)) as client:
        yield client, state, calls


def action(status, identifier):
    return next(row for row in status["actions"] if row["id"] == identifier)


def test_configured_http_actions_and_unconfigured_oom_are_distinguished():
    with console() as (client, _state, _calls):
        status = client.get("/api/status").json()
        assert action(status, "payment-error")["available"] is True
        oom = action(status, "oom")
        assert oom["services"] == [] and oom["fault"] is None
        assert oom["available"] is False
        assert "DEMO_CONSOLE_ENABLE_OOM" in oom["unavailable_reason"]
        assert status["oom"]["configured"] is False
        assert client.post("/api/actions/oom", headers=HEADERS).status_code == 403
        terminal = client.get("/api/status").json()["terminal"]
        assert terminal["entries"][-1]["state"] == "blocked"
        assert not any(row["command"] for row in terminal["entries"])


def test_oom_opt_in_does_not_replace_configured_script():
    with console(replace(BASE, oom_enabled=True)) as (client, _state, calls):
        status = client.get("/api/status").json()
        assert action(status, "oom")["available"] is False
        assert "DEMO_CONSOLE_OOM_SCRIPT" in action(status, "oom")["unavailable_reason"]
        assert client.post("/api/actions/oom", headers=HEADERS).status_code == 503
        assert all(method == "GET" for method, _service in calls)


def test_missing_service_disables_its_injection_without_disabling_other_service():
    def payment_down(request, _state):
        if request.url.host == "payment.test":
            return httpx.Response(404)

    with console(upstream=payment_down) as (client, _state, _calls):
        status = client.get("/api/status").json()
        assert action(status, "payment-error")["available"] is False
        assert action(status, "both-error")["available"] is False
        assert action(status, "inventory-error")["available"] is True
        # A reset of the reachable endpoint remains possible, reported partial.
        assert action(status, "recover")["available"] is True
        assert client.post("/api/actions/recover", headers=HEADERS).status_code == 502


def test_trace_records_actual_command_payload_and_readback_without_polling_noise():
    with console() as (client, _state, _calls):
        assert client.get("/api/status").json()["terminal"]["entries"] == []
        assert client.post("/api/actions/payment-latency-950", headers=HEADERS).status_code == 200
        first = client.get("/api/status").json()["terminal"]["entries"]
        assert first == client.get("/api/status").json()["terminal"]["entries"]
        posts = [row for row in first if row["command"].startswith("POST")]
        assert [row["state"] for row in posts] == ["running", "acknowledged"]
        assert json.loads(posts[0]["command"].split(" ", 2)[2]) == {"mode": "latency", "delay_ms": 950}
        assert posts[0]["command"].startswith("POST http://payment.test/internal/faults ")
        assert first[0]["command"] == "GET http://payment.test/internal/faults"
        assert first[-1]["state"] == "verified"
        assert [row["sequence"] for row in first] == list(range(1, len(first) + 1))
        assert all(row["at"].endswith("+00:00") for row in first)


def test_timeout_stays_unknown_and_final_result_unconfirmed_even_if_mutation_applied():
    def applied_without_ack(request, state):
        if request.method == "POST":
            state["payment"].update(json.loads(request.content))
            raise httpx.ReadTimeout("lost response", request=request)

    with console(upstream=applied_without_ack) as (client, _state, _calls):
        assert client.post("/api/actions/payment-error", headers=HEADERS).status_code == 502
        status = client.get("/api/status").json()
        assert status["services"]["payment"]["mode"] == "error"
        entries = status["terminal"]["entries"]
        assert any(row["state"] == "unknown" and row["command"].startswith("POST") for row in entries)
        assert entries[-1]["state"] == "unconfirmed"


def test_partial_post_failure_is_visible_and_server_body_is_never_reflected():
    def inventory_reject(request, _state):
        if request.method == "POST" and request.url.host == "inventory.test":
            return httpx.Response(503, text="upstream-secret=very-private")

    with console(upstream=inventory_reject) as (client, _state, _calls):
        assert client.post("/api/actions/both-error", headers=HEADERS).status_code == 502
        status = client.get("/api/status").json()
        assert status["services"]["payment"]["mode"] == "error"
        assert status["services"]["inventory"]["mode"] == "healthy"
        entries = status["terminal"]["entries"]
        assert any(row["state"] == "failed" and "HTTP 503" in row["detail"] for row in entries)
        assert "very-private" not in json.dumps(status)
        assert entries[-1]["state"] == "unconfirmed"


def test_oom_uses_literal_argv_and_holds_global_scenario_lock(monkeypatch, tmp_path):
    script = tmp_path / "oom scenario;literal.sh"
    script.write_text("# No real execution\n")
    calls = []

    class Process:
        code = None

        def poll(self):
            return self.code

    process = Process()

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        kwargs["stdout"].write(b"PATCH deployment/sentinelops-oom-demo: 256Mi -> 64Mi\n")
        kwargs["stdout"].flush()
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    runner = DeploymentRunner(str(script), environment_key="DEMO_CONSOLE_OOM_SCRIPT")
    try:
        with console(replace(BASE, oom_enabled=True), oom_runner=runner) as (client, _state, upstream):
            response = client.post("/api/actions/oom", headers=HEADERS)
            assert response.status_code == 202 and response.json()["oom"]["state"] == "running"
            assert upstream == []
            assert calls[0][0] == ["bash", str(script)]
            assert calls[0][1].get("shell", False) is False
            assert calls[0][1]["stdin"] == subprocess.DEVNULL
            for identifier in ("deploy-bad-image", "oom", "payment-error", "recover"):
                assert client.post("/api/actions/" + identifier, headers=HEADERS).status_code == 409
            status = client.get("/api/status").json()
            assert status["busy"] is True
            assert all(not row["available"] for row in status["actions"])
            assert "256Mi -> 64Mi" in status["terminal"]["logs"]["oom"]
            assert len([row for row in status["terminal"]["entries"] if row["state"] == "starting"]) == 1
            process.code = 0
            status = client.get("/api/status").json()
            final = client.get("/api/status").json()
            assert status["oom"]["returncode"] == 0
            assert len([row for row in final["terminal"]["entries"] if row["state"] == "exited"]) == 1
            assert "recovery is unconfirmed" in final["terminal"]["entries"][-1]["detail"]
            assert client.post("/api/actions/recover", headers=HEADERS).status_code == 200
    finally:
        if runner.output:
            runner.output.close()


def test_running_deployment_also_blocks_oom(monkeypatch, tmp_path):
    script = tmp_path / "demo.sh"
    script.write_text("# No execution\n")

    class Process:
        def poll(self):
            return None

    runner = DeploymentRunner(str(script))
    runner.process = Process()
    with console(replace(BASE, oom_enabled=True, oom_script=str(script)), runner=runner) as (client, _state, calls):
        assert client.post("/api/actions/oom", headers=HEADERS).status_code == 409
        assert calls == []


def test_global_enable_remains_required_even_with_oom_explicitly_enabled(tmp_path):
    script = tmp_path / "oom.sh"
    script.write_text("# No execution\n")
    with console(replace(BASE, enabled=False, oom_enabled=True, oom_script=str(script))) as (client, _state, calls):
        assert client.post("/api/actions/oom", headers=HEADERS).status_code == 403
        assert calls == []


def test_oom_settings_use_only_explicit_true(monkeypatch):
    monkeypatch.setenv("DEMO_CONSOLE_OOM_SCRIPT", "/reviewed/oom.sh")
    for flag, enabled in (("true", True), ("TRUE", True), ("1", False), ("true ", False)):
        monkeypatch.setenv("DEMO_CONSOLE_ENABLE_OOM", flag)
        settings = Settings.from_env()
        assert settings.oom_enabled is enabled
        assert settings.oom_script == "/reviewed/oom.sh"


def test_terminal_redacts_secrets_and_escape_controls(monkeypatch):
    monkeypatch.setenv("CUSTOM_SECRET", "secret-without-label")
    source = ("curl authorization: Bearer private-access-token\n"
              "DATABASE_URL=postgresql://user:pass@private/db\n"
              "https://private.invalid/path?X-Amz-Signature=private-signature\n"
              '  "api_token": "private-api-token",\n'
              "--password private-password\n"
              "secret-without-label\n"
              "\x1b[31mGET /health\x1b[0m\x00\n"
              "-----BEGIN RSA PRIVATE KEY-----\nprivate-key-material\n-----END RSA PRIVATE KEY-----\n")
    cleaned = safe_output(source)
    for secret in ("private-access-token", "user:pass", "private-signature", "private-api-token",
                   "private-password", "secret-without-label", "private-key-material", "\x1b", "\x00"):
        assert secret not in cleaned
    assert "GET /health" in cleaned


def test_terminal_never_emits_truncated_leading_credential_line():
    runner = DeploymentRunner("")
    try:
        runner.output = tempfile.TemporaryFile()
        runner.output.write(b"Authorization: Bearer " + b"x" * 20000 + b"\nGET /health: 200\n")
        runner.output.flush()
        tail = runner.snapshot()["log_tail"]
        assert "xxx" not in tail
        assert "GET /health: 200" in tail
        runner.output.write(b"\n" + b"y" * 20000)
        runner.output.flush()
        assert runner.snapshot()["log_tail"] == "[log line exceeds preview bound]"
    finally:
        runner.output.close()


@pytest.mark.parametrize("source,secret", [
    ("api_key=fixture-key-not-a-real-secret\n", "fixture-key-not-a-real-secret"),
    ('{"password":\n"fixture-password-not-a-real-secret"}\n', "fixture-password-not-a-real-secret"),
    ("Authorization:\nBearer fixture-token-not-a-real-secret\nGET /health: 200\n", "fixture-token-not-a-real-secret"),
    ("Authorization: Bearer\nfixture-token-not-a-real-secret\n", "fixture-token-not-a-real-secret"),
    ("API_KEY=\n\nfixture-key-not-a-real-secret\n", "fixture-key-not-a-real-secret"),
    ('{"secret": [\n"fixture-value-a",\n"fixture-value-b"]}\n', "fixture-value"),
    ('before\n{"credentials": {\n"nested": ["fixture-value-a", {"other":"fixture-value-b"}]\n}}\nafter\n', "fixture-value"),
    ('{"api_key":\n["fixture-value-a", {"nested": ["fixture-value-b"]}]}\n', "fixture-value"),
])
def test_terminal_redacts_api_keys_and_multiline_values(source, secret):
    assert secret not in safe_output(source)


def test_journal_is_thread_safe_and_bounded():
    journal = ExecutionJournal()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda index: journal.add("test", "GET /health", "observed", str(index)), range(250)))
    rows = journal.snapshot()
    assert len(rows) == 200
    assert [row["sequence"] for row in rows] == list(range(51, 251))
    # The caller cannot mutate retained entries by editing a status response.
    rows[-1]["detail"] = "changed"
    assert journal.snapshot()[-1]["detail"] != "changed"
