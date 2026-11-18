"""Operator receipts are explicit, immutable, content bound and race safe."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sys

import pytest

from scripts import approve_runbook
from sentinelops.remediation.approvals import find_proposal, record_approval, read_slot
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def setup(tmp_path):
    store = ArtifactStore(tmp_path)
    proposal = {'proposal_version': 'runbook-proposal/v1', 'proposal_id': stable_id(['INC-1', 2, 'rollback']),
        'incident_id': 'INC-1', 'incident_revision': 2, 'decision': 'oncall',
        'runbook_id': 'rollback', 'created_at': timestamp(NOW),
        'expires_at': timestamp(NOW + timedelta(minutes=15))}
    store.write('manifests', proposal)
    return store, proposal


def write(store, proposal, **changes):
    args = dict(proposal_id=proposal['proposal_id'], approved_by='test-oncall', decision='approve',
                reason='reviewed', confirm_oncall_approval=True, now=NOW)
    return record_approval(store, **{**args, **changes})


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_receipt_contains_operator_time_and_exact_proposal_hash(tmp_path, decision):
    store, proposal = setup(tmp_path)
    ref = write(store, proposal, decision=decision)
    approval = store.read(ref)
    assert approval['approved_by'] == 'test-oncall' and approval['approved_at'] == timestamp(NOW)
    assert approval['decision'] == decision and approval['proposal_sha256'] == stable_id(proposal)
    assert find_proposal(store, proposal['proposal_id']) == proposal
    assert read_slot(store, 'approval', proposal['proposal_id']) == approval
    with pytest.raises(ValueError, match='already recorded'):
        write(store, proposal, decision='reject')
    assert store.read(ref) == approval


@pytest.mark.parametrize('changes', [
    {'approved_by': ''}, {'approved_by': ' \t'}, {'approved_by': None},
    {'confirm_oncall_approval': False}, {'decision': 'auto'},
    {'proposal_id': 'f'*64}, {'proposal_id': '../../escape'},
    {'now': NOW - timedelta(seconds=1)}, {'now': NOW + timedelta(minutes=15)},
])
def test_invalid_approval_never_publishes_receipt(tmp_path, changes):
    store, proposal = setup(tmp_path)
    with pytest.raises(ValueError): write(store, proposal, **changes)
    assert read_slot(store, 'approval', proposal['proposal_id']) is None


def test_concurrent_operators_publish_exactly_one_receipt(tmp_path):
    store, proposal = setup(tmp_path)
    def attempt(index):
        try:
            return write(store, proposal, approved_by=f'oncall-{index}')
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(attempt, range(8)))
    assert len([ref for ref in refs if ref]) == 1


@pytest.mark.parametrize('missing', ['--approved-by', '--confirm-oncall-approval'])
def test_cli_requires_explicit_identity_and_confirmation(missing):
    args = ['--store', '/store', '--proposal-id', 'a'*64, '--approved-by', 'operator',
            '--decision', 'approve', '--confirm-oncall-approval']
    index = args.index(missing)
    del args[index:index + (2 if missing == '--approved-by' else 1)]
    with pytest.raises(SystemExit): approve_runbook.parser().parse_args(args)


def test_cli_records_receipt_and_prints_reference(tmp_path, monkeypatch, capsys):
    store, proposal = setup(tmp_path)
    monkeypatch.setattr(approve_runbook, 'record_approval', lambda selected, **args:
        record_approval(selected, **args, now=NOW))
    monkeypatch.setattr(sys, 'argv', ['approve_runbook', '--store', str(tmp_path),
        '--proposal-id', proposal['proposal_id'], '--approved-by', 'operator', '--decision', 'reject',
        '--reason', 'needs investigation', '--confirm-oncall-approval'])
    approve_runbook.main()
    result = json.loads(capsys.readouterr().out)
    assert store.read(result['approval_ref'])['decision'] == 'reject'
