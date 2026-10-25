import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from sentinelops.telemetry.infrastructure import PostgresInfrastructureSource


def test_read_only_real_metric_shape_excludes_credentials_and_monitor_connection(monkeypatch):
    state = {"closed":False}

    class Connection:
        async def fetchrow(self, query, **kwargs):
            assert "pid <> pg_backend_pid()" in query
            assert "INSERT" not in query
            return {"measured_at":datetime.now(timezone.utc)-timedelta(seconds=1),
                    "max_connections":100,"reserved_connections":3,"connections":10}

        async def close(self):
            state["closed"] = True

    async def connect(dsn, **kwargs):
        assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
        return Connection()

    monkeypatch.setattr("sentinelops.telemetry.infrastructure.asyncpg.connect", connect)
    item = asyncio.run(PostgresInfrastructureSource("postgresql://user:private-secret@127.0.0.1:5432/app").capture())
    assert item["metrics"]["db_connections_pct"] == pytest.approx(10/97*100)
    assert "private-secret" not in json.dumps(item)
    assert "user" not in item["source_ref"]
    assert state["closed"]


def test_database_failure_closes_connection_without_fabricating_metrics(monkeypatch):
    state = {"closed":False}

    class Connection:
        async def fetchrow(self, *args, **kwargs):
            raise RuntimeError("database read unavailable")

        async def close(self):
            state["closed"] = True

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr("sentinelops.telemetry.infrastructure.asyncpg.connect", connect)
    with pytest.raises(RuntimeError):
        asyncio.run(PostgresInfrastructureSource("postgresql://local/app").capture())
    assert state["closed"]
