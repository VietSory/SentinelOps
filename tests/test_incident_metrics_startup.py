import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import scripts.run_incident_worker as cli


def settings(monkeypatch):
    # Disposable configuration only; mocks prevent every network/DB operation.
    value = 'postgresql://' + 'control_writer' + ':' + 'synthetic' + '@127.0.0.1:15443/sentinelops_control'
    monkeypatch.setenv('CONTROL_DATABASE_URL', value)
    return SimpleNamespace(metrics_otlp_endpoint='http://127.0.0.1:4317', metrics_instance='incidents-0', status=False)


def test_invalid_metrics_configuration_is_rejected_before_opening_db_pool(monkeypatch):
    args = settings(monkeypatch)
    pool = SimpleNamespace(close=AsyncMock())
    create = AsyncMock(return_value=pool)
    monkeypatch.setattr(cli.asyncpg, 'create_pool', create)
    monkeypatch.setattr(cli.IncidentWorkerMetrics, 'otlp', Mock(side_effect=ValueError('invalid stable slot')))
    with pytest.raises(ValueError, match='stable slot'):
        asyncio.run(cli.run(args))
    create.assert_not_awaited()


def test_startup_db_failure_is_observable_and_closes_private_metrics(monkeypatch):
    args = settings(monkeypatch)
    metrics = SimpleNamespace(heartbeat=Mock(), error=Mock(), close=Mock())
    factory = Mock(return_value=metrics)
    monkeypatch.setattr(cli.IncidentWorkerMetrics, 'otlp', factory)
    monkeypatch.setattr(cli.asyncpg, 'create_pool', AsyncMock(side_effect=ConnectionError('database unavailable')))
    with pytest.raises(ConnectionError):
        asyncio.run(cli.run(args))
    factory.assert_called_once()
    metrics.heartbeat.assert_any_call(ready=False)
    metrics.error.assert_called_once_with('database')
    metrics.close.assert_called_once()


def test_database_retry_refreshes_real_loop_heartbeat_while_remaining_not_ready(monkeypatch, tmp_path):
    args = settings(monkeypatch)
    args.__dict__.update(health_policy=Path('configs/slo_health_policy.json'), source_store=tmp_path / 'source',
                         health_stream='fixture-stream', project='fixture-project', jaeger='http://127.0.0.1:16686',
                         prometheus='http://127.0.0.1:9090', model_store=None, model_release_ref=None,
                         store=tmp_path / 'consumer', topology=Path('configs/topology.yaml'),
                         rca_policy=Path('configs/rca_policy.json'), max_ticks=2, poll_seconds=.25)
    tick_number, heartbeats = [0], []
    metrics = SimpleNamespace(heartbeat=lambda *, ready: heartbeats.append((tick_number[0], ready)),
                              error=Mock(), close=Mock())
    monkeypatch.setattr(cli.IncidentWorkerMetrics, 'otlp', Mock(return_value=metrics))
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value={'fixture': True}))

    @asynccontextmanager
    async def acquire():
        yield connection

    pool = SimpleNamespace(acquire=acquire, close=AsyncMock())
    collector = SimpleNamespace(profile={}, verify_source=AsyncMock(), close=AsyncMock())
    journal = SimpleNamespace(consumer_id='a' * 64, initialize=AsyncMock(), store=SimpleNamespace())
    monkeypatch.setattr(cli.asyncpg, 'create_pool', AsyncMock(return_value=pool))
    monkeypatch.setattr(cli, 'HealthLedgerReader', Mock(return_value=SimpleNamespace(stream_id='fixture-stream')))
    monkeypatch.setattr(cli, 'NativeIncidentEvidence', Mock(return_value=collector))
    monkeypatch.setattr(cli, 'IncidentJournal', Mock(return_value=journal))
    monkeypatch.setattr(cli, 'validate_stream', Mock(return_value={}))

    async def tick():
        tick_number[0] += 1
        raise ConnectionError('database unavailable')

    monkeypatch.setattr(cli, 'IncidentWorker', Mock(return_value=SimpleNamespace(tick=tick)))
    asyncio.run(cli.run(args))
    assert tick_number[0] == 2
    assert (1, False) in heartbeats, 'a live retry loop needs a fresh not-ready heartbeat after the first failure'
    assert not any(ready for _, ready in heartbeats)
    assert metrics.error.call_count == 2
    pool.close.assert_awaited_once()
    collector.close.assert_awaited_once()
