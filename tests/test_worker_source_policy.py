import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import scripts.run_health_worker as health_cli
import scripts.run_incident_worker as incident_cli
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers import health
from sentinelops.workers.source_policy import SourcePolicy, source_policy_arguments

PROMETHEUS = "https://prometheus.observability.svc.cluster.local:9090"
DATABASE = "postgresql://control.sentinelops.svc.cluster.local:5432/sentinelops_control?sslmode=verify-full"
POLICY = json.loads(Path("configs/slo_health_policy.json").read_text())


@pytest.fixture
def remote(tmp_path):
    token, password = tmp_path / "token", tmp_path / "password"
    for path in (token, password):
        path.write_text("synthetic-test-credential")
        path.chmod(0o600)
    return SourcePolicy("remote", PROMETHEUS, DATABASE, "control_writer", password, token)


def test_policy_defaults_are_local_for_cli_and_programmatic_callers():
    parser = argparse.ArgumentParser()
    source_policy_arguments(parser)
    assert SourcePolicy.from_args(parser.parse_args([])) == SourcePolicy()
    assert SourcePolicy.from_args(SimpleNamespace()) == SourcePolicy()
    with pytest.raises(ValueError, match="unsupported"):
        SourcePolicy("auto")


@pytest.mark.parametrize("endpoint", ["http://localhost", "http://127.0.0.1:9090/", "http://localhost:1"])
def test_local_endpoint_keeps_accepted_urls_and_normalization(endpoint):
    assert SourcePolicy().prometheus(endpoint) == health.local_endpoint(endpoint) == endpoint.rstrip("/")


@pytest.mark.parametrize("endpoint,message", [
    (PROMETHEUS, "credential-free loopback Prometheus endpoint"),
    ("http://prometheus.observability.svc.cluster.local:9090", "credential-free loopback Prometheus endpoint"),
    ("http://localhost:bad", "valid loopback port"),
    ("http://localhost:65536", "valid loopback port"),
    ("http://user:secret@localhost:9090", "credential-free loopback Prometheus endpoint"),
    ("http://localhost:9090/path", "credential-free loopback Prometheus endpoint"),
    ("http://localhost:9090?token=x", "credential-free loopback Prometheus endpoint"),
    ("http://localhost:9090#x", "credential-free loopback Prometheus endpoint"),
])
def test_local_endpoint_keeps_rejections_and_error_messages(endpoint, message):
    with pytest.raises(ValueError) as error:
        SourcePolicy().prometheus(endpoint)
    assert str(error.value) == "local health worker requires a " + message


@pytest.mark.parametrize("worker", ["health", "incident"])
def test_local_database_keeps_dedicated_loopback_guard(worker):
    policy = SourcePolicy()
    assert policy.database_options("postgresql://control_writer:synthetic@localhost:5433/sentinelops_control",
                                   worker=worker) == {}
    for dsn in (DATABASE, "postgresql://control_writer@remote:5432/sentinelops_control",
                "postgresql://other@localhost:5432/sentinelops_control",
                "postgresql://control_writer@localhost:5432/other"):
        with pytest.raises(ValueError) as error:
            policy.database_options(dsn, worker=worker)
        expected = ("health worker requires the dedicated loopback control_writer database" if worker == "health"
                    else "continuous incident worker requires the explicit loopback control_writer database")
        assert str(error.value) == expected


@pytest.mark.parametrize("suffix", ["", "?legacy=1", "#legacy"])
def test_local_database_preserves_existing_difference_between_workers(suffix):
    dsn = "postgresql://control_writer@localhost/sentinelops_control" + suffix
    assert SourcePolicy().database_options(dsn, worker="health") == {}
    with pytest.raises(ValueError, match="explicit loopback"):
        SourcePolicy().database_options(dsn, worker="incident")


@pytest.mark.parametrize("endpoint", [PROMETHEUS.replace("https:", "http:"),
    PROMETHEUS.replace("://", "://user:secret@"), PROMETHEUS.replace("://", "://user@"),
    PROMETHEUS + "/path", PROMETHEUS + "?token=secret", PROMETHEUS + "?", PROMETHEUS + "#fragment",
    PROMETHEUS + "#", PROMETHEUS.replace(":9090", ""), PROMETHEUS.replace(":9090", ":0"),
    PROMETHEUS.replace(":9090", ":65536"), PROMETHEUS.replace(":9090", ":bad"),
    PROMETHEUS + "/;params", PROMETHEUS + "\n"])
def test_remote_prometheus_rejects_unsafe_urls_even_when_pinned(remote, endpoint):
    with pytest.raises(ValueError):
        replace(remote, remote_prometheus=endpoint).prometheus(endpoint)


@pytest.mark.parametrize("dsn", [DATABASE.replace("postgresql:", "postgres:"),
    DATABASE.replace("postgresql:", "http:"), DATABASE.replace("?sslmode=verify-full", ""),
    DATABASE.replace("verify-full", "require"), DATABASE.replace("verify-full", "disable"),
    DATABASE.replace("://", "://control_writer:secret@"), DATABASE.replace("://", "://control_writer@"),
    DATABASE.replace("/sentinelops_control", "/other"), DATABASE + "&password=secret",
    DATABASE + "&sslmode=disable", DATABASE + "&host=other", DATABASE + "#fragment", DATABASE + "#",
    DATABASE.replace(":5432", ""), DATABASE.replace(":5432", ":0"), DATABASE.replace(":5432", ":bad")])
def test_remote_database_rejects_unsafe_urls_even_when_pinned(remote, dsn):
    with pytest.raises(ValueError):
        replace(remote, remote_control_database=dsn).database_options(dsn, worker="health")


@pytest.mark.parametrize("worker", ["health", "incident"])
def test_remote_accepts_only_explicit_pinned_sources_with_separate_credentials(remote, worker):
    assert remote.prometheus(PROMETHEUS) == PROMETHEUS
    assert remote.prometheus(PROMETHEUS + "/") == PROMETHEUS
    options = remote.database_options(DATABASE, worker=worker)
    assert options["user"] == "control_writer" and options["password"] == "synthetic-test-credential"
    assert options["ssl"].verify_mode == ssl.CERT_REQUIRED and options["ssl"].check_hostname
    assert remote.headers() == {"Authorization": "Bearer synthetic-test-credential"}
    for endpoint in (PROMETHEUS.replace("prometheus.", "other."), "https://localhost:9090"):
        with pytest.raises(ValueError): remote.prometheus(endpoint)
    with pytest.raises(ValueError): remote.database_options(DATABASE.replace("control.", "other."), worker=worker)
    with pytest.raises(ValueError): replace(remote, remote_prometheus=None).prometheus(PROMETHEUS)
    with pytest.raises(ValueError): replace(remote, remote_control_database=None).database_options(DATABASE, worker=worker)
    with pytest.raises(ValueError): replace(remote, control_database_user="other").database_options(DATABASE, worker=worker)
    with pytest.raises(ValueError): replace(remote, control_database_user=None).database_options(DATABASE, worker=worker)


@pytest.mark.parametrize("kind", ["missing", "relative", "public", "symlink", "empty", "multiline"])
def test_remote_credentials_require_private_file_references(remote, tmp_path, kind):
    path = remote.prometheus_token_file
    if kind == "missing": path = None
    elif kind == "relative": path = Path("inline-secret")
    elif kind == "public": path.chmod(0o644)
    elif kind == "symlink":
        path = tmp_path / "link"
        path.symlink_to(remote.prometheus_token_file)
    else: path.write_text("" if kind == "empty" else "first\nsecond")
    with pytest.raises(ValueError): replace(remote, prometheus_token_file=path).headers()
    with pytest.raises(ValueError):
        replace(remote, control_database_password_file=path).database_options(DATABASE, worker="health")


@pytest.mark.parametrize("cli,worker", [(health_cli, "health"), (incident_cli, "incident")])
def test_both_entrypoints_enforce_policy_before_any_database_connection(monkeypatch, cli, worker):
    monkeypatch.setenv("CONTROL_DATABASE_URL", DATABASE)
    pool = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(cli.asyncpg, "create_pool", pool)
    with pytest.raises(ValueError, match="loopback"):
        asyncio.run(cli.run(SimpleNamespace()))
    with pytest.raises(ValueError, match="pinned"):
        asyncio.run(cli.run(SimpleNamespace(source_policy="remote")))
    pool.assert_not_awaited()


def test_health_adapter_uses_remote_policy_for_configuration_and_capture(remote, monkeypatch, tmp_path):
    monkeypatch.setattr(health_cli.asyncio, "to_thread", AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw)))
    source = SimpleNamespace(client=SimpleNamespace(timeout=None, headers={}), close=Mock(),
                             capture=Mock(return_value={}), verify_configuration=Mock(return_value={}))
    factory = Mock(return_value=source)
    monkeypatch.setattr(health, "PrometheusSource", factory)
    end = datetime(2026, 11, 17, tzinfo=timezone.utc)
    assert health.native_capture(PROMETHEUS, "payment-service", end, source_policy=remote) == {}
    factory.assert_called_once_with(PROMETHEUS)
    assert source.client.headers == remote.headers()
    source.close.assert_called_once()
    worker = health.HealthWorker(None, ArtifactStore(tmp_path / "store"), POLICY, endpoint=PROMETHEUS,
        environment="fixture-remote", configuration_hash="a" * 64, source_policy=remote)
    assert worker.source_policy is remote and worker.endpoint == PROMETHEUS
    assert "synthetic-test-credential" not in json.dumps(worker.identity)
    with pytest.raises(ValueError, match="loopback"):
        health.HealthWorker(None, worker.store, POLICY, endpoint=PROMETHEUS,
                            environment="fixture-remote", configuration_hash="a" * 64)
    monkeypatch.setattr(health_cli, "PrometheusSource", factory)
    pool = AsyncMock(side_effect=ConnectionError("synthetic stop before database connection"))
    monkeypatch.setattr(health_cli.asyncpg, "create_pool", pool)
    monkeypatch.setenv("CONTROL_DATABASE_URL", DATABASE)
    args = SimpleNamespace(source_policy="remote", prometheus=PROMETHEUS, status=False,
        **{key: getattr(remote, key) for key in remote.__dataclass_fields__ if key != "name"})
    with pytest.raises(ConnectionError, match="synthetic stop"):
        asyncio.run(health_cli.run(args))
    source.verify_configuration.assert_called_once()
    assert pool.call_args.kwargs["user"] == "control_writer"
    assert pool.call_args.kwargs["ssl"].check_hostname
