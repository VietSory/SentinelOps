"""Persisted action compatibility uses the real journal decoder, without a database."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from sentinelops.contracts import utc_time
from sentinelops.remediation.domain import Policy, Request
from sentinelops.remediation.journal import ActionJournal
from sentinelops.telemetry.core import stable_id
from test_remediation_kubernetes import TARGET


INVALID_FRESHNESS = [-1, 0, 179, 181, 600, 899, 901, True, False, 180.0, 900.0, '180', '900', None, [], {}]


def stored_action(freshness_seconds):
    # Build the stored payload independently of today's Policy constructor/default.
    policy = {'target': asdict(TARGET), 'enabled': True, 'max_pods': 1, 'max_surge': 0,
              'cooldown_seconds': 600, 'verification_seconds': 600,
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


@pytest.mark.parametrize('freshness_seconds', [180, 900])
def test_stored_action_reads_with_original_policy_and_hashes(tmp_path, freshness_seconds):
    row = stored_action(freshness_seconds)
    path = tmp_path / 'action.json'
    path.write_text(json.dumps(row))
    original = path.read_bytes()
    result = asyncio.run(read_stored_action(path))
    assert result['intent'] == row['payload']
    assert result['intent']['policy']['freshness_seconds'] == freshness_seconds
    assert result['intent']['policy_sha256'] == Policy(TARGET, enabled=True, freshness_seconds=freshness_seconds).identity
    assert result['action_id'] == row['action_id'] and result['phase'] == 'blocked'
    assert result['last_event_hash'] == row['audit'][0]['event_hash']
    assert path.read_bytes() == original


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
