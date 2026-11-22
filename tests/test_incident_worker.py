import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg

import pytest

from sentinelops.workers.incident import IncidentWorker, _Session


def test_lock_scoped_journal_cannot_get_another_writer_connection_after_session_loss():
    class Connection:
        closed = False
        def is_closed(self): return self.closed
    connection = Connection()
    async def check():
        session = _Session(connection)
        async with session.acquire() as selected:
            assert selected is connection
        connection.closed = True
        with pytest.raises(ConnectionError, match="lock connection"):
            async with session.acquire():
                pytest.fail("lost lock session must never supply another writer connection")
    asyncio.run(check())


def test_consumer_requires_dedicated_writer_and_closes_busy_session_without_journal_work():
    journal = SimpleNamespace(consumer_id="a"*64)
    with pytest.raises(ValueError, match="nonpooled writer"):
        IncidentWorker(journal, None, connect=None)
    class Connection(asyncpg.Connection):
        # asyncpg's metaclass deliberately rejects Mock(spec=Connection).
        def __init__(self):
            self.fetchval = AsyncMock(return_value=False)
            self.close = AsyncMock()
        def is_closed(self): return False
        def __del__(self): pass
    connection = Connection()
    worker = IncidentWorker(journal, None, connect=AsyncMock(return_value=connection))
    assert -(2**63) <= worker.lock_id < 2**63
    assert asyncio.run(worker.tick()) == {"status": "BUSY", "consumer_id": "a"*64}
    assert connection.fetchval.await_count == 1
    connection.close.assert_awaited_once_with(timeout=5.)


def test_pool_proxy_is_rejected_before_locking_or_writing():
    class Proxy(asyncpg.pool.PoolConnectionProxy):
        def __init__(self): self.fetchval = AsyncMock()
    proxy = Proxy()
    # The pinned library intentionally treats pool proxies as Connection
    # instances. The worker must reject their actual type, not rely on isinstance.
    assert isinstance(proxy, asyncpg.Connection)
    worker = IncidentWorker(SimpleNamespace(consumer_id="a"*64), None, connect=AsyncMock(return_value=proxy))
    with pytest.raises(ValueError, match="dedicated nonpooled"):
        asyncio.run(worker.tick())
    proxy.fetchval.assert_not_awaited()


@pytest.mark.parametrize("failure_phase", ["lock", "ingest", "close"])
def test_worker_discards_a_session_with_an_asyncpg_protocol_error(failure_phase, monkeypatch):
    error = asyncpg.InternalClientError("protocol is in the failed state")

    class Connection(asyncpg.Connection):
        def __init__(self):
            self.closed = False
            self.fetchval = AsyncMock(return_value=failure_phase == "ingest",
                                      side_effect=error if failure_phase == "lock" else None)
            self.close = AsyncMock(side_effect=error if failure_phase == "close" else None)

        def is_closed(self):
            return self.closed

        def terminate(self):
            self.closed = True

        def __del__(self):
            pass

    ingest = AsyncMock(side_effect=error)
    monkeypatch.setattr("sentinelops.workers.incident.IncidentJournal",
                        lambda *args, **kwargs: SimpleNamespace(ingest_next=ingest))
    journal = SimpleNamespace(consumer_id="a"*64, source=None, store=None, topology=None,
                              rca_policy=None, identity={"evidence_profile": None}, model_registry=None)
    connection = Connection()
    worker = IncidentWorker(journal, None, connect=AsyncMock(return_value=connection))

    with pytest.raises(ConnectionError, match="PostgreSQL session was lost") as failure:
        asyncio.run(worker.tick())

    assert failure.value.__cause__ is error
    assert connection.closed
    connection.fetchval.assert_awaited_once_with("SELECT pg_try_advisory_lock($1)", worker.lock_id)
    assert ingest.await_count == (1 if failure_phase == "ingest" else 0)
    assert connection.close.await_count == (1 if failure_phase == "close" else 0)


def test_worker_reports_a_driver_failure_during_connect_as_unavailable():
    error = asyncpg.AdminShutdownError()
    worker = IncidentWorker(SimpleNamespace(consumer_id="a"*64), None,
                            connect=AsyncMock(side_effect=error))

    with pytest.raises(ConnectionError, match="during connect") as failure:
        asyncio.run(worker.tick())

    assert failure.value.__cause__ is error
