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
        def acquire(self): return Acquire()
    worker = HealthWorker(Pool(), ArtifactStore(tmp_path), POLICY, endpoint="http://localhost:9090",
                          environment="fixture-connection", configuration_hash="a" * 64)
    worker._locked_tick = AsyncMock(side_effect=asyncpg.InterfaceError("closed or misused connection"))
    with pytest.raises(ConnectionError if closed else asyncpg.InterfaceError):
        asyncio.run(worker.tick())
