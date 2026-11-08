import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import run_g6_trial


@pytest.mark.parametrize('pending,receipt', [(True, None), (True, '{"retain_control_db": false}'),
                                            (True, '{'), (False, None)])
def test_cancelled_trial_keeps_database_when_audit_is_pending(tmp_path, monkeypatch, pending, receipt):
    root = tmp_path / 'trial'
    sandbox = SimpleNamespace(name='trial', docker=Mock(return_value=b'trial'), cleanup=Mock())
    monkeypatch.setattr(run_g6_trial, 'BusinessSandbox', lambda *_: sandbox)
    monkeypatch.setattr(run_g6_trial, 'clean_commit', lambda: 'a' * 40)
    monkeypatch.setattr(run_g6_trial.ModelRegistry, 'load', lambda *_: SimpleNamespace(available=True))
    monkeypatch.setattr(run_g6_trial, 'deploy', lambda _: {'application': 'fixture', 'postgres': 'fixture'})
    monkeypatch.setattr(run_g6_trial, 'bad_image', lambda *_: 'fixture-bad')
    monkeypatch.setattr(run_g6_trial, 'emit', Mock())
    monkeypatch.setattr(run_g6_trial.signal, 'signal', Mock())

    def control_db(sandbox, postgres, owned):
        owned.append('owned-db')
        return '1', 'fixture'

    async def cancel_with_pending_audit(*_):
        if pending:
            (root / 'action-audit-pending.json').write_text('{}')
        if receipt is not None:
            (root / 'failure-receipt.json').write_text(receipt)
        raise asyncio.CancelledError()

    monkeypatch.setattr(run_g6_trial, 'control_db', control_db)
    monkeypatch.setattr(run_g6_trial, 'trial', cancel_with_pending_audit)
    monkeypatch.setattr('sys.argv', ['run_g6_trial', '--kind', 'fixture', '--output', str(root),
        '--model-store', str(tmp_path), '--model-release-ref', 'fixture', '--approver', 'operator'])

    with pytest.raises(asyncio.CancelledError):
        run_g6_trial.main()
    sandbox.cleanup.assert_called_once_with()
    if pending:
        sandbox.docker.assert_not_called()
        assert any(call.args[0] == 'control-db-retained-for-audit' for call in run_g6_trial.emit.call_args_list)
    else:
        sandbox.docker.assert_called_with('rm', '-f', '-v', 'owned-db')
