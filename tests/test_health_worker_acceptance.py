import json
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.check_health_worker_live import require_port, target
from scripts.run_health_worker import run


def test_destructive_live_acceptance_requires_exact_project_service_and_loopback_owner(monkeypatch):
    info = {"Config": {"Labels": {"com.docker.compose.project": "owned", "com.docker.compose.service": "prometheus"}},
            "State": {"Running": True}, "NetworkSettings": {"Ports": {"9090/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9090"}]}}}
    monkeypatch.setattr("scripts.check_health_worker_live.docker", lambda *args: json.dumps([info]).encode())
    require_port(target("container", "owned", "prometheus"), "9090/tcp", 9090)
    with pytest.raises(ValueError): target("container", "another-project", "prometheus")
    with pytest.raises(ValueError): target("container", "owned", "control-postgres")
    info["NetworkSettings"]["Ports"]["9090/tcp"][0]["HostIp"] = "0.0.0.0"
    with pytest.raises(ValueError): require_port(info, "9090/tcp", 9090)
    info["NetworkSettings"]["Ports"]["9090/tcp"][0]["HostIp"] = "127.0.0.1"
    with pytest.raises(ValueError): require_port(info, "9090/tcp", 9091)
    info["State"]["Running"] = False
    with pytest.raises(ValueError): require_port(info, "9090/tcp", 9090)


@pytest.mark.parametrize("error,retry", [(PermissionError("immutable store denied"), False),
                                        (ConnectionRefusedError("local database unavailable"), True)])
def test_worker_retries_database_transport_but_fails_on_artifact_io(monkeypatch, tmp_path, error, retry):
    class Source:
        def __init__(self, *args): self.client = SimpleNamespace(timeout=None)
        def verify_configuration(self): return {"configuration_hash": "a" * 64}
        def close(self): pass
    pool = SimpleNamespace(close=AsyncMock())
    worker = SimpleNamespace(check_ready=AsyncMock(), tick=AsyncMock(side_effect=[error, {"status": "COMMITTED"}]),
                             status=AsyncMock(return_value={}), stream_id="fixture", identity={})
    monkeypatch.setenv("CONTROL_DATABASE_URL", "postgresql://control_writer@127.0.0.1:5433/sentinelops_control")
    monkeypatch.setattr("scripts.run_health_worker.PrometheusSource", Source)
    monkeypatch.setattr("scripts.run_health_worker.asyncpg.create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr("scripts.run_health_worker.HealthWorker", lambda *a, **k: worker)
    args = SimpleNamespace(prometheus="http://localhost:9090", store=tmp_path,
                           policy=Path("configs/slo_health_policy.json"), environment="fixture", status=False,
                           max_buckets=1, poll_seconds=.25)
    if retry: asyncio.run(asyncio.wait_for(run(args), 2))
    else:
        with pytest.raises(PermissionError): asyncio.run(asyncio.wait_for(run(args), 2))
    pool.close.assert_awaited_once()
