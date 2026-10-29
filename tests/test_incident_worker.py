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
