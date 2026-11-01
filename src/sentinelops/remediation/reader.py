"""Read-only, bounded action observations, separate from the exact IER.

Reuses the journal's intent/hash/transition integrity verifier. The API credential
must have SELECT only, including no column-level writes, in both control schemas.
"""
from __future__ import annotations

import base64
import json
import re

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.remediation.domain import RUNBOOK
from sentinelops.remediation.journal import ActionJournal
from sentinelops.telemetry.core import stable_id


def observation(state):
    evidence = state['intent']['evidence']
    if evidence is None:
        evidence = {}
    if not isinstance(evidence,dict):
        raise RuntimeError('stored action evidence is not an object')
    result = {'observation_version': 'action-observation/v1', 'action_id': state['action_id'],
        'incident_id': state['intent']['request']['incident_id'],
        'incident_revision': state['intent']['request']['incident_revision'],
        'action_revision': state['revision'], 'runbook': RUNBOOK, 'phase': state['phase'],
        'recorded_at': state['recorded_at'], 'updated_at': state['updated_at'],
        'target': state['intent']['policy']['target'], 'reason': state['facts'].get('reason'),
        'before_image': evidence.get('before', {}).get('image'),
        'known_good_image': evidence.get('known_good', {}).get('image'), 'history': state['history']}
    try:
        validate_payload('action-observation.schema.json', result)
    except ValueError:
        raise RuntimeError('stored action cannot be projected as a valid observation') from None
    return result


def decode_cursor(value, incident_id, max_revision):
    if value is None:
        return None
    try:
        if len(value) > 2048:
            raise ValueError()
        card = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if (set(card) != {'recorded_at', 'action_id', 'scope'}
            or card['scope'] != stable_id([incident_id, max_revision])
            or not re.fullmatch(r'[0-9a-f]{64}', card['action_id'])):
            raise ValueError()
        return utc_time(card['recorded_at']), card['action_id']
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('invalid action cursor or incident revision scope') from None


class ActionReader:
    def __init__(self, pool):
        self.pool, self._snapshots = pool, ActionJournal(pool)

    async def check_ready(self):
        async with self.pool.acquire() as conn:
            if await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM sentinel_remediation.schema_migrations') != [1]:
                raise RuntimeError('explicit action schema required')
            unsafe = await conn.fetchval('''SELECT rolsuper OR rolcreaterole OR rolcreatedb
                OR pg_has_role(current_user,'control_owner','MEMBER')
                OR has_schema_privilege('sentinel_control','CREATE')
                OR has_schema_privilege('sentinel_remediation','CREATE')
                FROM pg_roles WHERE rolname=current_user''')
            writes = await conn.fetchval('''SELECT coalesce(bool_or(
                has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                OR has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES')),false)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('sentinel_control','sentinel_remediation') AND c.relkind IN ('r','p')''')
            if unsafe or writes:
                raise RuntimeError('action API requires a dedicated read-only runtime')
            for table in ('intents', 'states', 'events', 'targets', 'dispatches'):
                if not await conn.fetchval("SELECT has_table_privilege($1,'SELECT')", 'sentinel_remediation.' + table):
                    raise RuntimeError('action reader lacks required SELECT privileges')

    async def get(self, action_id):
        state = await self._snapshots.get(action_id)
        return observation(state) if state else None

    async def list(self, incident_id, max_revision, *, limit=10, after=None):
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError('action page limit outside bounds')
        async with self.pool.acquire() as conn, conn.transaction(isolation='repeatable_read', readonly=True):
            rows = await conn.fetch('''SELECT action_id,recorded_at FROM sentinel_remediation.intents
                WHERE incident_id=$1 AND incident_revision<=$2
                AND ($4::timestamptz IS NULL OR (recorded_at,action_id)>($4,$5))
                ORDER BY recorded_at,action_id LIMIT $3''', incident_id, max_revision, limit + 1,
                after[0] if after else None, after[1] if after else None)
            items = [observation(await self._snapshots._read(conn, row['action_id'])) for row in rows[:limit]]
        cursor = None
        if len(rows) > limit:
            last = items[-1]
            cursor = base64.urlsafe_b64encode(json.dumps({'recorded_at': last['recorded_at'],
                'action_id': last['action_id'], 'scope': stable_id([incident_id, max_revision])}).encode()).decode().rstrip('=')
        return {'observation_version': 'action-list/v1', 'incident_id': incident_id,
                'incident_revision': max_revision, 'items': items, 'next_cursor': cursor}
