"""Persisted action compatibility uses the real journal decoder, without a database."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

import pytest

from sentinelops.contracts import utc_time
from sentinelops.remediation.domain import Policy, Request
from sentinelops.remediation.journal import ActionJournal
from sentinelops.telemetry.core import stable_id
from test_remediation_kubernetes import TARGET


INVALID_FRESHNESS = [-1, 0, 179, 181, 600, 899, 901, True, False, 180.0, 900.0, '180', '900', None, [], {}]
INVALID_VERIFICATION = [-1, 0, 599, 601, 1199, 1201, True, False, 600.0, 1200.0,
                        '600', '1200', None, [], {}]


def stored_action(freshness_seconds, verification_seconds=600):
    # Build the stored payload independently of today's Policy constructor/default.
    policy = {'target': asdict(TARGET), 'enabled': True, 'max_pods': 1, 'max_surge': 0,
              'cooldown_seconds': 600, 'verification_seconds': verification_seconds,
              'freshness_seconds': freshness_seconds, 'change_window_seconds': 600}
    request = Request('INC-historical-policy', 2, 'historical-policy-0001')
    request_hash = request.fingerprint(TARGET)
    action_id = stable_id({'request_hash': request_hash})
    intent = {'intent_version': 'payment-rollback-intent/v1', 'request': asdict(request),
              'policy': policy, 'policy_sha256': stable_id({'policy_version': 'payment-rollback/local-v1', **policy}),
              'evidence': None}
    facts = {'reason': 'runbook-disabled-or-kill-switch'}
    event = {'action_id': action_id, 'revision': 1, 'phase': 'blocked', 'facts': facts, 'previous_hash': None}
    event_hash = stable_id(event)
    recorded_at = '2026-01-01T00:00:00Z'
    return {'action_id': action_id, 'request_hash': request_hash, 'target_key': TARGET.key,
            'incident_id': request.incident_id, 'incident_revision': request.incident_revision,
            'intent_hash': stable_id(intent), 'payload': intent, 'facts': facts, 'phase': 'blocked',
            'revision': 1, 'recorded_at': recorded_at,
            'audit': [{**event, 'event_hash': event_hash, 'recorded_at': recorded_at}]}


async def read_stored_action(path):
    row = json.loads(path.read_text())
    row['recorded_at'] = utc_time(row['recorded_at'])
    for field in ('payload', 'facts', 'audit'):
        row[field] = json.dumps(row[field])  # asyncpg's default JSONB representation.
    class Connection:
        async def fetchrow(self, sql, action_id):
            assert action_id == row['action_id']
            return deepcopy(row)
    return await ActionJournal(None)._read(Connection(), row['action_id'])


def test_new_policy_defaults_to_900_seconds():
    assert Policy(TARGET).freshness_seconds == 900


def test_new_policy_defaults_to_1200_second_executor_verification_budget():
    assert Policy(TARGET).verification_seconds == 1200


@pytest.mark.parametrize('verification_seconds', [600, 1200])
def test_policy_accepts_current_and_historical_verification_budgets(verification_seconds):
    assert Policy(TARGET, verification_seconds=verification_seconds).verification_seconds == verification_seconds


@pytest.mark.parametrize('verification_seconds', INVALID_VERIFICATION)
def test_policy_rejects_other_or_non_integer_verification_budgets(verification_seconds):
    with pytest.raises(ValueError, match='unsupported payment-rollback/local-v1 bounds'):
        Policy(TARGET, verification_seconds=verification_seconds)


@pytest.mark.parametrize('freshness_seconds', [180, 900])
def test_stored_action_reads_with_original_policy_and_hashes(tmp_path, freshness_seconds):
    row = stored_action(freshness_seconds)
    path = tmp_path / 'action.json'
    path.write_text(json.dumps(row))
    original = path.read_bytes()
    result = asyncio.run(read_stored_action(path))
    assert result['intent'] == row['payload']
    assert result['intent']['policy']['freshness_seconds'] == freshness_seconds
    assert result['intent']['policy_sha256'] == Policy(
        TARGET, enabled=True, freshness_seconds=freshness_seconds, verification_seconds=600).identity
    assert result['action_id'] == row['action_id'] and result['phase'] == 'blocked'
    assert result['last_event_hash'] == row['audit'][0]['event_hash']
    assert path.read_bytes() == original


def test_historical_action_with_600_second_verification_budget_still_reads(tmp_path):
    row = stored_action(180, verification_seconds=600)
    path = tmp_path / 'historical-action.json'
    path.write_text(json.dumps(row))
    result = asyncio.run(read_stored_action(path))
    assert result['intent']['policy']['verification_seconds'] == 600
    assert result['intent']['policy_sha256'] == Policy(
        TARGET, enabled=True, freshness_seconds=180, verification_seconds=600).identity


@pytest.mark.parametrize('verification_seconds,expected_phase', [(1200, 'recovered'), (600, 'escalated')])
def test_journal_recovery_commit_uses_the_persisted_verification_budget(
        verification_seconds, expected_phase):
    async def run():
        dispatched=datetime(2026, 11, 20, 4, 17, 26, tzinfo=timezone.utc)
        state={'action_id':'a'*64, 'revision':3, 'phase':'verifying',
               'facts':{'dispatch_started_at':dispatched.isoformat().replace('+00:00', 'Z')},
               'intent':{'policy':{'verification_seconds':verification_seconds}}}
        class Context:
            def __init__(self, value): self.value=value
            async def __aenter__(self): return self.value
            async def __aexit__(self, *args): pass
        class Connection:
            def transaction(self): return Context(self)
            async def fetchval(self, sql): return dispatched+timedelta(seconds=750)
        connection=Connection()
        class Pool:
            def acquire(self): return Context(connection)
        journal=ActionJournal(Pool())
        async def read(conn, action_id, *, lock=False):
            assert conn is connection and action_id==state['action_id'] and lock
            return deepcopy(state)
        async def advance(conn, current, phase, facts, *, release=False):
            return {'phase':phase, 'facts':facts, 'release':release}
        journal._read=read
        journal._advance=advance
        candidate={**state['facts'], 'telemetry':{'status':'telemetry_verified'}}
        result=await journal.transition(state['action_id'],state['revision'],'recovered',candidate,release=True)
        assert result['phase']==expected_phase
        if expected_phase=='recovered':
            assert result['facts']['telemetry']['status']=='telemetry_verified' and result['release'] is True
        else:
            assert result['facts']['reason']=='verification-expired-before-recovery-commit'
            assert 'telemetry' not in result['facts'] and result['release'] is False
    asyncio.run(run())


@pytest.mark.parametrize('freshness_seconds', INVALID_FRESHNESS)
def test_policy_and_stored_action_reject_other_freshness_values(tmp_path, freshness_seconds):
    with pytest.raises(ValueError, match='unsupported payment-rollback/local-v1 bounds'):
        Policy(TARGET, freshness_seconds=freshness_seconds)
    path = tmp_path / 'action.json'
    path.write_text(json.dumps(stored_action(freshness_seconds)))
    with pytest.raises(ValueError, match='unsupported payment-rollback/local-v1 bounds'):
        asyncio.run(read_stored_action(path))


def test_read_rejects_rewriting_historical_policy_without_its_original_hash(tmp_path):
    row = stored_action(180)
    row['payload']['policy']['freshness_seconds'] = 900
    row['intent_hash'] = stable_id(row['payload'])
    path = tmp_path / 'action.json'
    path.write_text(json.dumps(row))
    with pytest.raises(RuntimeError, match='stored action intent failed integrity verification'):
        asyncio.run(read_stored_action(path))
