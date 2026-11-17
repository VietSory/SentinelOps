"""Demo lifecycle/credential isolation fixtures; never start infrastructure."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import lane_b_host as host, run_g6_trial as g6
from scripts.summarize_g6_trials import summarize, trial


@pytest.mark.parametrize('marker', ['demo_mode', 'q09_eligible', 'run_purpose', 'file'])
def test_q09_refuses_demo_even_when_recovered(tmp_path, marker):
    receipt = {'model_release_ref': 'same-release', 'action_outcome': 'recovered'}
    if marker == 'file':
        (tmp_path / 'DEMO_ONLY').write_text('demo')
    else:
        receipt[marker] = {'demo_mode': True, 'q09_eligible': False, 'run_purpose': 'demo'}[marker]
    (tmp_path / 'verification.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='ineligible for Q-09'):
        trial(tmp_path)
    if marker != 'file':
        with pytest.raises(ValueError, match='ineligible for Q-09'):
            summarize([{'release': 'same-release', **receipt}])


def test_reader_grants_only_select_and_never_writer_membership(monkeypatch):
    conn = SimpleNamespace(fetchval=AsyncMock(return_value='CREATE ROLE fixture'),
                           execute=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(g6.asyncpg, 'connect', AsyncMock(return_value=conn))
    dsn = asyncio.run(g6.demo_reader_dsn('23456', 'bootstrap-secret'))
    assert dsn.startswith('postgresql://g6_api_reader:') and ':23456/' in dsn
    sql = conn.fetchval.call_args.args[0]
    assert 'IN ROLE control_action_reader' in sql and 'writer' not in sql
    assert [c.args[0] for c in conn.execute.call_args_list][1:] == [
        'GRANT USAGE ON SCHEMA sentinel_control TO g6_api_reader',
        'GRANT SELECT ON ALL TABLES IN SCHEMA sentinel_control TO g6_api_reader']
    conn.close.assert_awaited_once()


@pytest.mark.parametrize('label,binding,allowed', [
    ('owned', '127.0.0.1:23456', True), ('foreign', '127.0.0.1:23456', False),
    ('owned', '0.0.0.0:23456', False), ('owned', '127.0.0.1:15433', False)])
def test_dynamic_reader_port_requires_exact_ownership_and_binding(label, binding, allowed):
    sandbox = SimpleNamespace(name='owned', docker=Mock(side_effect=[label.encode(), binding.encode()]))
    dsn = 'postgresql://reader:secret@127.0.0.1:23456/sentinelops_control'
    if allowed:
        assert host.validate_action_reader(dsn, 23456, sandbox=sandbox, container='owned-id') == dsn
    else:
        with pytest.raises(host.HostGuard):
            host.validate_action_reader(dsn, 23456, sandbox=sandbox, container='owned-id')
    with pytest.raises(host.HostGuard):
        host.validate_action_reader(dsn, 23456)
    with pytest.raises(host.HostGuard):
        host.validate_action_reader(dsn, 15433, sandbox=sandbox, container='owned-id')


def test_api_environment_pins_both_readers_model_and_operator_only(tmp_path, monkeypatch):
    monkeypatch.setenv('SENTINELOPS_ACTION_DATABASE_URL', 'writer-secret')
    monkeypatch.setenv('SENTINELOPS_API_TOKEN', 'producer-secret')
    monkeypatch.setenv('SENTINELOPS_OIDC_ISSUER', 'foreign')
    args = SimpleNamespace(model_store=tmp_path, model_release_ref='release')
    env = g6.demo_api_environment('reader-dsn', args, 'operator-token')
    assert env['CONTROL_DATABASE_URL'] == env['SENTINELOPS_ACTION_READ_DATABASE_URL'] == 'reader-dsn'
    assert env['SENTINELOPS_MODEL_STORE'] == str(tmp_path.resolve())
    assert env['SENTINELOPS_MODEL_RELEASE_REF'] == 'release'
    assert env['SENTINELOPS_OPERATOR_TOKEN'] == 'operator-token'
    assert env['SENTINELOPS_ENABLE_DEMO'] == 'false'
    assert not {'SENTINELOPS_ACTION_DATABASE_URL', 'SENTINELOPS_API_TOKEN', 'SENTINELOPS_OIDC_ISSUER'} & env.keys()


def test_keep_alive_publishes_demo_receipt_and_waits_for_operator(tmp_path):
    async def exercise():
        alive = Mock()
        task = asyncio.create_task(g6.keep_demo_alive(tmp_path, {'action_outcome': 'recovered'}, alive))
        await asyncio.sleep(0)
        assert not task.done()
        receipt = json.loads((tmp_path / 'verification.json').read_text())
        assert receipt['q09_eligible'] is False and receipt['demo_mode'] is True
        alive.assert_called_once()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(exercise())


@pytest.mark.parametrize('demo', [False, True])
def test_main_demo_is_explicit_and_retains_sandbox_after_operator_stop(tmp_path, monkeypatch, demo):
    root = tmp_path / 'run'
    sandbox = SimpleNamespace(root=root, name='owned', docker=Mock(return_value=b'owned'), cleanup=Mock())
    monkeypatch.setattr(g6, 'BusinessSandbox', lambda *_: sandbox)
    monkeypatch.setattr(g6, 'clean_commit', lambda: 'a' * 40)
    monkeypatch.setattr(g6.ModelRegistry, 'load', lambda *_: SimpleNamespace(available=True))
    monkeypatch.setattr(g6, 'deploy', lambda _: {'application': 'healthy', 'postgres': 'postgres'})
    monkeypatch.setattr(g6, 'bad_image', lambda *_: 'bad')
    monkeypatch.setattr(g6.signal, 'signal', Mock())
    monkeypatch.setattr(g6, 'emit', Mock())
    def database(_, __, owned):
        owned.append('db-id')
        return '23456', 'bootstrap'
    monkeypatch.setattr(g6, 'control_db', database)
    api = Mock()
    api.poll.return_value = None
    start = AsyncMock(return_value=api)
    monkeypatch.setattr(g6, 'start_demo_api', start)
    async def stopped(*_):
        if demo:
            assert json.loads((root / 'verification.json').read_text())['demo_mode'] is True
            assert (root / 'DEMO_ONLY').exists()
        raise asyncio.CancelledError()
    monkeypatch.setattr(g6, 'trial', stopped)
    argv = ['g6', '--kind', 'fixture', '--output', str(root), '--model-store', str(tmp_path),
            '--model-release-ref', 'release', '--approver', 'operator']
    monkeypatch.setattr('sys.argv', argv + (['--demo-keep-alive'] if demo else []))
    with pytest.raises(asyncio.CancelledError):
        g6.main()
    if demo:
        start.assert_awaited_once()
        api.terminate.assert_called_once()
        sandbox.cleanup.assert_not_called()
        sandbox.docker.assert_not_called()
        with pytest.raises(ValueError, match='ineligible for Q-09'):
            trial(root)
    else:
        start.assert_not_awaited()
        sandbox.cleanup.assert_called_once()
        sandbox.docker.assert_called_with('rm', '-f', '-v', 'db-id')


def test_demo_api_starts_on_owned_socket_with_trial_reader_and_token(tmp_path, monkeypatch):
    reader = 'postgresql://g6_api_reader:secret@127.0.0.1:23456/sentinelops_control'
    provision = AsyncMock(return_value=reader)
    monkeypatch.setattr(g6, 'demo_reader_dsn', provision)
    guard = Mock()
    monkeypatch.setattr(g6, 'validate_action_reader', guard)
    listener = Mock()
    listener.__enter__ = Mock(return_value=listener)
    listener.__exit__ = Mock(return_value=False)
    listener.getsockname.return_value = ('127.0.0.1', 18001)
    listener.fileno.return_value = 42
    monkeypatch.setattr(g6, 'socket', SimpleNamespace(socket=Mock(return_value=listener)))
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(g6.subprocess, 'Popen', popen)
    client = Mock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(g6.httpx, 'AsyncClient', Mock(return_value=client))
    sandbox = SimpleNamespace(root=tmp_path)
    args = SimpleNamespace(model_store=tmp_path, model_release_ref='release', demo_api_port=18001)
    assert asyncio.run(g6.start_demo_api(sandbox, '23456', 'bootstrap', 'owned-db', args)) is process
    listener.bind.assert_called_once_with(('127.0.0.1', 18001))
    guard.assert_called_once()
    assert popen.call_args.kwargs['pass_fds'] == (42,)
    assert popen.call_args.kwargs['env']['SENTINELOPS_ACTION_READ_DATABASE_URL'] == reader
    token = (tmp_path / 'operator-token').read_text().strip()
    assert len(token) >= 32 and (tmp_path / 'operator-token').stat().st_mode & 0o077 == 0
    assert client.get.call_args.kwargs['headers'] == {'Authorization': 'Bearer ' + token}


def test_cli_rejects_mixed_real_and_demo_without_emitting_summary(tmp_path, monkeypatch, capsys):
    from scripts.summarize_g6_trials import main
    real = tmp_path / 'real'
    demo = tmp_path / 'demo'
    for path, receipt in ((real, {'model_release_ref': 'r', 'action_outcome': 'recovered'}),
                          (demo, {**g6.DEMO_RECEIPT, 'model_release_ref': 'r', 'action_outcome': 'recovered'})):
        path.mkdir()
        (path / 'verification.json').write_text(json.dumps(receipt))
    monkeypatch.setattr('sys.argv', ['summary', str(real), str(demo)])
    with pytest.raises(ValueError, match='ineligible for Q-09'):
        main()
    assert capsys.readouterr().out == ''


def test_demo_keeps_read_window_after_background_failure(tmp_path):
    async def exercise():
        task = asyncio.create_task(g6.keep_demo_alive(tmp_path, {}, Mock(side_effect=RuntimeError('worker stopped'))))
        await asyncio.sleep(0)
        assert not task.done()
        receipt = json.loads((tmp_path / 'verification.json').read_text())
        assert receipt['demo_background_failure'] == 'RuntimeError' and receipt['q09_eligible'] is False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(exercise())
