"""S4 keeps its owned loopback source while using an alternate PostgreSQL port."""
import asyncio
import json

import pytest

from sentinelops.experiments import runner, s4_postgres
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2


@pytest.fixture
def owned_sources(monkeypatch):
    project = 'sentinelops-s4-port-test'
    monkeypatch.setenv('COMPOSE_PROJECT_NAME', project)
    monkeypatch.setenv('SENTINELOPS_S4_POSTGRES_PORT', '25432')
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    items = []
    for service, (private, published) in s4_postgres.PORTS.items():
        if service == 'postgres':
            published = '25432'
        items.append({'Config': {'Labels': {'com.docker.compose.service': service,
            'com.docker.compose.project': project}}, 'State': {'Running': True},
            'NetworkSettings': {'Ports': {private: [{'HostIp': '127.0.0.1', 'HostPort': published}]}},
            'Mounts': [{'Destination': '/var/lib/postgresql/data', 'Type': 'volume',
                        'Name': project + '_sentinelops_postgres'}] if service == 'postgres' else []})
    def docker(args, **kwargs):
        if args[1] == 'context':
            return 'unix:///var/run/docker.sock\n'
        if args[1] == 'compose':
            return '\n'.join('container-' + str(i) for i in range(len(items)))
        assert args[1] == 'inspect'
        return json.dumps(items)
    monkeypatch.setattr(s4_postgres.subprocess, 'check_output', docker)
    return project, items


def test_selected_postgres_port_must_resolve_to_the_owned_loopback_volume(tmp_path, owned_sources):
    project, _ = owned_sources
    assert s4_postgres.require_sandbox(tmp_path) == project
    assert s4_postgres.database_url() == 'postgresql://sentinel:sentinel@127.0.0.1:25432/sentinelops'


@pytest.mark.parametrize('corruption', ['shared-project', 'public-port', 'different-port', 'shared-volume'])
def test_alternate_port_does_not_relax_source_ownership(tmp_path, owned_sources, corruption):
    project, items = owned_sources
    postgres = next(i for i in items if i['Config']['Labels']['com.docker.compose.service'] == 'postgres')
    if corruption == 'shared-project':
        postgres['Config']['Labels']['com.docker.compose.project'] = 'other-project'
    elif corruption == 'public-port':
        postgres['NetworkSettings']['Ports']['5432/tcp'][0]['HostIp'] = '0.0.0.0'
    elif corruption == 'different-port':
        postgres['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'] = '5432'
    else:
        postgres['Mounts'][0]['Name'] = 'shared-production-data'
    with pytest.raises(ValueError):
        s4_postgres.require_sandbox(tmp_path)


@pytest.mark.parametrize('port', ['', '0', '-1', '65536', '5432/path', '05432', '１２３', '1.5'])
def test_invalid_postgres_port_is_rejected(monkeypatch, port):
    monkeypatch.setenv('SENTINELOPS_S4_POSTGRES_PORT', port)
    with pytest.raises(ValueError, match='port'):
        s4_postgres.database_url()


def test_default_postgres_port_stays_compatible(monkeypatch):
    monkeypatch.delenv('SENTINELOPS_S4_POSTGRES_PORT', raising=False)
    assert s4_postgres.database_url() == s4_postgres.DATABASE_URL


def test_lock_injector_connects_to_the_verified_alternate_port(tmp_path, monkeypatch, owned_sources):
    project, _ = owned_sources
    connections, closed = [], []
    class Transaction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    class Connection:
        def transaction(self):
            return Transaction()
        async def execute(self, statement):
            raise asyncio.CancelledError
        async def close(self, **kwargs):
            closed.append(True)
    async def connect(dsn, **kwargs):
        connections.append(dsn)
        return Connection()
    monkeypatch.setattr(s4_postgres.asyncpg, 'connect', connect)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(s4_postgres.lock_pulses(1.2, tmp_path, project))
    assert connections == ['postgresql://sentinel:sentinel@127.0.0.1:25432/sentinelops']
    assert closed == [True]


def test_profile_v2_s4_reaches_capture_only_after_required_warmup_and_all_entries(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    called = []
    async def capture(config, **kwargs):
        called.append((config, kwargs))
        return tmp_path / 'native-run'
    monkeypatch.setattr(runner, '_execute_locked', capture)
    config = runner.ExperimentConfig('S4', 'validation', warmup_seconds=1800, all_entry_traffic=True)
    assert asyncio.run(runner.execute(config, project=tmp_path, output=tmp_path / 'runs',
        source_profile=SOURCE_PROFILE_V2, s4_lead_in=True)) == tmp_path / 'native-run'
    assert len(called) == 1 and called[0][1]['source_profile'] == SOURCE_PROFILE_V2


@pytest.mark.parametrize('changes', [dict(warmup_seconds=1799), dict(all_entry_traffic=False), dict(scenario='S5')])
def test_v2_extension_preserves_existing_capture_guards(tmp_path, monkeypatch, changes):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    config = runner.ExperimentConfig(**(dict(scenario='S4', split='validation', warmup_seconds=1800,
                                            all_entry_traffic=True) | changes))
    with pytest.raises(ValueError, match='ADR-016 capture'):
        asyncio.run(runner.execute(config, project=tmp_path, output=tmp_path / 'runs',
                                   source_profile=SOURCE_PROFILE_V2))


def test_s4_refuses_a_database_url_for_the_wrong_local_port(tmp_path, monkeypatch):
    monkeypatch.setenv('SENTINELOPS_S4_POSTGRES_PORT', '25432')
    monkeypatch.setenv('DATABASE_URL', s4_postgres.DATABASE_URL)
    config = runner.ExperimentConfig('S4', 'validation', warmup_seconds=1800, all_entry_traffic=True)
    with pytest.raises(ValueError, match='dedicated Compose PostgreSQL'):
        asyncio.run(runner.execute(config, project=tmp_path, output=tmp_path / 'runs',
                                   source_profile=SOURCE_PROFILE_V2))


def test_lock_timeout_rolls_back_and_retries_after_the_fixed_gap(tmp_path, monkeypatch, owned_sources):
    project, _ = owned_sources
    events, sleeps, stats = [], [], {}
    class Transaction:
        async def __aenter__(self):
            events.append('begin')
        async def __aexit__(self, kind, *args):
            events.append('rollback' if kind else 'commit')
    class Connection:
        attempts = 0
        def transaction(self):
            return Transaction()
        async def execute(self, statement):
            self.attempts += 1
            if self.attempts == 1:
                raise s4_postgres.asyncpg.LockNotAvailableError('fixture contention')
            events.append('acquired')
        async def close(self, **kwargs):
            events.append('closed')
    async def connect(dsn, **kwargs):
        assert kwargs['server_settings']['lock_timeout'] == '250ms'
        assert kwargs['server_settings']['statement_timeout'] == '2500ms'
        return Connection()
    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(s4_postgres.asyncpg, 'connect', connect)
    monkeypatch.setattr(s4_postgres.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(s4_postgres.lock_pulses(.35, tmp_path, project, stats=stats))
    assert sleeps == [s4_postgres.PULSE_GAP_SECONDS, .35]
    assert events == ['begin', 'rollback', 'begin', 'acquired', 'rollback', 'closed']
    assert stats == {'attempts': 2, 'acquired': 1, 'lock_timeouts': 1}


@pytest.mark.parametrize('error', [s4_postgres.asyncpg.PostgresConnectionError, s4_postgres.asyncpg.QueryCanceledError])
def test_lock_pulses_do_not_hide_connection_or_statement_failures(tmp_path, monkeypatch, owned_sources, error):
    project, _ = owned_sources
    closed = []
    class Transaction:
        async def __aenter__(self):
            pass
        async def __aexit__(self, *args):
            pass
    class Connection:
        def transaction(self):
            return Transaction()
        async def execute(self, statement):
            raise error('fixture source failure')
        async def close(self, **kwargs):
            closed.append(True)
    async def connect(*args, **kwargs):
        return Connection()
    monkeypatch.setattr(s4_postgres.asyncpg, 'connect', connect)
    with pytest.raises(error):
        asyncio.run(s4_postgres.lock_pulses(.35, tmp_path, project))
    assert closed == [True]


def test_lock_log_reader_connects_to_the_selected_read_only_database(tmp_path, monkeypatch, owned_sources):
    project, _ = owned_sources
    connections, closed = [], []
    class Connection:
        async def fetchrow(self, *args, **kwargs):
            return {'enabled': 'off'}
        async def close(self, **kwargs):
            closed.append(True)
    async def connect(dsn, **kwargs):
        connections.append(dsn)
        assert kwargs['server_settings'] == {'default_transaction_read_only': 'on'}
        return Connection()
    monkeypatch.setattr(s4_postgres.asyncpg, 'connect', connect)
    assert asyncio.run(s4_postgres.capture_lock_logs(tmp_path, project,
        '2026-11-14T00:00:00Z', '2026-11-14T00:01:00Z')) is None
    assert connections == ['postgresql://sentinel:sentinel@127.0.0.1:25432/sentinelops']
    assert closed == [True]


def test_native_lock_log_identity_matches_the_selected_port(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from sentinelops.telemetry.infrastructure import PostgresInfrastructureSource
    monkeypatch.setenv('SENTINELOPS_S4_POSTGRES_PORT', '25432')
    project = 'sentinelops-s4-log-test'
    def docker(args, **kwargs):
        if args[1] == 'compose':
            return 'a' * 64
        if args[1] == 'inspect':
            return project + ' postgres'
        assert args[1] == 'logs'
        return ''
    monkeypatch.setattr(s4_postgres.subprocess, 'check_output', docker)
    record = s4_postgres._read_lock_logs(tmp_path, project,
        {'postgres_started_at': datetime(2026, 11, 14, tzinfo=timezone.utc), 'database_oid': 23, 'relation_oid': 17},
        '2026-11-14T00:00:00Z', '2026-11-14T00:01:00Z')
    assert record['source_ref'] == PostgresInfrastructureSource(s4_postgres.database_url()).source_ref
    assert record['source_ref'] != PostgresInfrastructureSource(s4_postgres.DATABASE_URL).source_ref
