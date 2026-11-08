from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import asyncio
from unittest.mock import AsyncMock

import pytest
import asyncpg

from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker, latest_bucket, local_endpoint

POLICY = json.loads(Path("configs/slo_health_policy.json").read_text())


@pytest.mark.parametrize("endpoint", ["https://127.0.0.1:9090", "http://example.com:9090", "http://127.0.0.1:9090/path",
    "http://user:secret@127.0.0.1:9090", "http://localhost:9090?token=hidden", "http://localhost:9090#fragment",
    "http://localhost:99999", "http://localhost:bad"])
def test_worker_source_scope_rejects_external_or_credential_bearing_endpoints(endpoint):
    with pytest.raises(ValueError): local_endpoint(endpoint)


def test_scheduler_uses_real_30_second_source_watermark_and_retains_late_start_bucket():
    at = datetime(2026, 10, 28, 0, 10, tzinfo=timezone.utc)
    assert latest_bucket(at + timedelta(seconds=29)) == at - timedelta(minutes=1)
    assert latest_bucket(at + timedelta(seconds=30)) == at
    assert latest_bucket(at + timedelta(seconds=89)) == at
    assert latest_bucket(at + timedelta(seconds=90)) == at + timedelta(minutes=1)
    with pytest.raises(ValueError): latest_bucket(at.replace(tzinfo=None))


def test_restarting_same_source_resumes_same_stream_but_policy_root_or_config_change_cannot_join(tmp_path):
    policy = deepcopy(POLICY)
    def worker(*, root=tmp_path, config="a" * 64, p=policy):
        return HealthWorker(None, ArtifactStore(root), p, endpoint="http://127.0.0.1:9090/",
                            environment="fixture-identity", configuration_hash=config)
    first = worker()
    assert first.stream_id == worker().stream_id
    assert first.stream_id != worker(root=tmp_path / "other").stream_id
    assert first.stream_id != worker(config="b" * 64).stream_id
    policy["minimum_requests"] += 1
    assert first.policy == POLICY and first.stream_id != worker().stream_id
    assert -(2 ** 63) <= first.lock_id < 2 ** 63


@pytest.mark.parametrize("closed", [True, False])
def test_disconnect_during_capture_is_retryable_but_programming_errors_are_not(tmp_path, closed):
    class Connection:
        fetchval = AsyncMock(return_value=True)
        def is_closed(self): return closed
    conn = Connection()
    class Acquire:
        async def __aenter__(self): return conn
        async def __aexit__(self, *args): pass
    class Pool:
        def acquire(self, *, timeout): return Acquire()
    worker = HealthWorker(Pool(), ArtifactStore(tmp_path), POLICY, endpoint="http://localhost:9090",
                          environment="fixture-connection", configuration_hash="a" * 64)
    worker._locked_tick = AsyncMock(side_effect=asyncpg.InterfaceError("closed or misused connection"))
    with pytest.raises(ConnectionError if closed else asyncpg.InterfaceError):
        asyncio.run(worker.tick())


@pytest.mark.parametrize("during", ["lock", "capture"])
def test_asyncpg_detached_proxy_keeps_database_disconnect_retryable(tmp_path, during):
    class PhysicalConnection(asyncpg.Connection):
        def __init__(self): self._proxy = None
        def __del__(self): pass
        async def fetchval(self, *args): return True

    proxy = asyncpg.pool.PoolConnectionProxy(None, PhysicalConnection())

    class Acquire:
        async def __aenter__(self):
            if during == "lock":
                proxy._detach()
            return proxy
        async def __aexit__(self, *args): pass

    class Pool:
        def acquire(self, *, timeout): return Acquire()

    async def lose_database(*args):
        proxy._detach()
        raise asyncpg.ConnectionDoesNotExistError("connection lost during work")

    worker = HealthWorker(Pool(), ArtifactStore(tmp_path), POLICY, endpoint="http://localhost:9090",
                          environment="fixture-detached", configuration_hash="a" * 64)
    worker._locked_tick = lose_database
    expected = ConnectionError if during == "lock" else asyncpg.ConnectionDoesNotExistError
    with pytest.raises(expected):
        asyncio.run(worker.tick())


def test_processing_budget_includes_returning_the_connection_to_its_pool(tmp_path):
    class Connection:
        fetchval = AsyncMock(return_value=True)
        def is_closed(self): return False
    conn = Connection()
    class Acquire:
        async def __aenter__(self): return conn
        async def __aexit__(self, *args): await asyncio.sleep(.02)
    class Pool:
        def acquire(self, *, timeout): return Acquire()
    worker = HealthWorker(Pool(), ArtifactStore(tmp_path), POLICY, endpoint="http://localhost:9090",
                          environment="fixture-budget", configuration_hash="a" * 64)
    worker._locked_tick = AsyncMock(return_value={"status": "COMMITTED"})
    result = asyncio.run(worker.tick())
    assert result["processing_ms"] >= 15


def test_database_lease_bounds_real_asyncpg_cancellation_drain_and_frees_holder(tmp_path):
    """Exercise pinned driver release code, not just a mock timeout argument.

    The simulated dead server never finishes cancellation. Only the fixture
    scales ten seconds to ten milliseconds; production timeouts stay unchanged.
    """
    from types import SimpleNamespace
    from asyncpg.pool import PoolConnectionHolder
    from sentinelops.workers.health import DATABASE_TIMEOUT_SECONDS
    async def run():
        drained = asyncio.Event()
        async def never_drains(): await drained.wait()
        backend = SimpleNamespace(_generation=0, _reset=None, _queue=asyncio.LifoQueue())
        holder = PoolConnectionHolder(backend, max_queries=50000, setup=None, max_inactive_time=0)
        class Connection:
            closed = False
            fetchval = AsyncMock(return_value=True)
            _protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: True,
                                        _wait_for_cancellation=never_drains)
            def is_closed(self): return self.closed
            def terminate(self):
                self.closed = True
                holder._release_on_close()
        conn = Connection();holder._con = conn;holder._generation = 0
        holder._in_use = asyncio.get_running_loop().create_future()
        class Acquire:
            def __init__(self, timeout): self.timeout = timeout
            async def __aenter__(self): return conn
            async def __aexit__(self, *args):
                await holder.release(None if self.timeout is None else self.timeout / 1000)
        class Pool:
            def acquire(self, *, timeout=None): return Acquire(timeout)
        worker = HealthWorker(Pool(), ArtifactStore(tmp_path), POLICY, endpoint='http://localhost:9090',
                              environment='fixture-cancellation', configuration_hash='a' * 64)
        worker._locked_tick = AsyncMock(return_value={'status': 'COMMITTED'})
        task = asyncio.create_task(worker.tick())
        try:
            done, _ = await asyncio.wait({task}, timeout=1)
            assert task in done, 'unbounded connection reset stalled the worker heartbeat'
            with pytest.raises(TimeoutError): await task
            assert conn.closed and holder._in_use is None and backend._queue.qsize() == 1
            assert DATABASE_TIMEOUT_SECONDS == 10
        finally:
            if not task.done():
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
    asyncio.run(run())
