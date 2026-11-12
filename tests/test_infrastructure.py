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


@pytest.mark.parametrize("case", ["visible", "unknown-onset", "hidden", "future-wait"])
def test_lock_wait_capture_preserves_missing_visibility_and_native_clocks(monkeypatch, case):
    measured = datetime.now(timezone.utc) - timedelta(seconds=1)
    started = measured - timedelta(minutes=5)
    wait = measured - timedelta(seconds=2)
    row = {
        "measured_at": measured, "postgres_started_at": started, "database_oid": 40,
        "stats_visible": case != "hidden", "waiting_clients": 0 if case == "hidden" else 1,
        "earliest_wait_start": None if case in ("unknown-onset", "hidden") else wait,
    }
    if case == "future-wait":
        row["earliest_wait_start"] = measured + timedelta(seconds=1)
    source = PostgresInfrastructureSource("postgresql://private:private-secret@127.0.0.1:5432/app")

    async def fetch(_query):
        return row

    monkeypatch.setattr(source, "_fetch", fetch)
    if case in ("hidden", "future-wait"):
        with pytest.raises(ValueError, match="visibility" if case == "hidden" else "clocks"):
            asyncio.run(source.capture_lock_waits())
        return
    item = asyncio.run(source.capture_lock_waits())
    assert item["kind"] == "postgres-lock-wait-snapshot/v1"
    assert item["database_oid"] == 40 and item["waiting_clients"] == 1
    assert item["earliest_wait_start"] == (None if case == "unknown-onset" else wait.isoformat().replace("+00:00", "Z"))
    assert "private" not in json.dumps(item)
