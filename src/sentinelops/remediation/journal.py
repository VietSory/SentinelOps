"""Durable intent, append-only audit, CAS and one dispatch budget in PostgreSQL.

No transaction is held across a Kubernetes/telemetry request. Ambiguous or failed
dispatches keep the target quarantined after escalation, pending operator review.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import json

import asyncpg

from sentinelops.remediation.domain import ActionConflict, Blocked, Policy, Request, Target
from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id, timestamp

TRANSITIONS = {'prepared': {'blocked','dispatching'}, 'dispatching': {'dispatching','verifying','escalated'},
               'verifying': {'verifying','recovered','escalated'}, 'blocked': set(), 'recovered': set(), 'escalated': set()}


def decode(value):
    return json.loads(value) if isinstance(value,str) else value


class ActionJournal:
    def __init__(self,pool):
        self.pool=pool

    async def check_ready(self):
        async with self.pool.acquire() as conn:
            if await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM sentinel_remediation.schema_migrations') != [1]:
                raise RuntimeError('explicit remediation migration 1 required')
            unsafe=await conn.fetchval('''SELECT rolsuper OR rolcreaterole OR rolcreatedb
                OR pg_has_role(current_user,'control_owner','MEMBER')
                OR has_schema_privilege('sentinel_remediation','CREATE')
                OR has_schema_privilege('sentinel_control','CREATE')
                OR has_table_privilege('sentinel_control.incident_versions','INSERT,UPDATE,DELETE,TRUNCATE')
                FROM pg_roles WHERE rolname=current_user''')
            if unsafe: raise RuntimeError('action runtime must not own schema or write IER history')
            core_writes=await conn.fetchval('''SELECT coalesce(bool_or(has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')),false)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='sentinel_control' AND c.relkind IN ('r','p')''')
            if core_writes: raise RuntimeError('action runtime can mutate upstream core evidence')
            for table in ('intents','events','dispatches','states','targets'):
                for privilege in ('SELECT','INSERT'):
                    if not await conn.fetchval('SELECT has_table_privilege($1,$2)','sentinel_remediation.'+table,privilege):
                        raise RuntimeError('action runtime lacks required journal privileges')
            for table,columns in (('states',('revision','phase','facts','updated_at')),('targets',('active_action','last_dispatch_at'))):
                for column in columns:
                    if not await conn.fetchval("SELECT has_column_privilege($1,$2,'UPDATE')",'sentinel_remediation.'+table,column):
                        raise RuntimeError('action runtime lacks required projection privileges')
                if await conn.fetchval("SELECT has_table_privilege($1,'DELETE,TRUNCATE')",'sentinel_remediation.'+table):
                    raise RuntimeError('action runtime can delete durable projection/target locks')
            for table in ('intents','events','dispatches','schema_migrations'):
                if await conn.fetchval("SELECT has_table_privilege($1,'UPDATE,DELETE,TRUNCATE')",'sentinel_remediation.'+table):
                    raise RuntimeError('action runtime can rewrite immutable intent/audit/dispatch history')

    async def _read(self,conn,action_id,*,lock=False):
        if lock:
            await conn.fetchrow('SELECT action_id FROM sentinel_remediation.states WHERE action_id=$1 FOR UPDATE',action_id)
        # State and its events share one statement snapshot, including when an
        # idempotent reader races a committing transition under READ COMMITTED.
        row=await conn.fetchrow('''SELECT i.*,s.revision,s.phase,s.facts,
            (SELECT jsonb_agg(e ORDER BY e.revision) FROM (
                SELECT * FROM sentinel_remediation.events WHERE action_id=i.action_id ORDER BY revision LIMIT 101
            ) e) AS audit
            FROM sentinel_remediation.intents i JOIN sentinel_remediation.states s USING(action_id)
            WHERE i.action_id=$1''',action_id)
        if row is None: return None
        intent=decode(row['payload']);facts=decode(row['facts'])
        request=Request(**intent['request']);target=Target(**intent['policy']['target'])
        policy=Policy(**{**intent['policy'],'target':target})
        if (row['intent_hash']!=stable_id(intent) or row['request_hash']!=request.fingerprint(target)
            or row['action_id']!=stable_id({'request_hash':row['request_hash']}) or target.key!=row['target_key']
            or request.incident_id!=row['incident_id'] or request.incident_revision!=row['incident_revision']
            or intent['intent_version']!='payment-rollback-intent/v1' or intent['policy_sha256']!=policy.identity):
            raise RuntimeError('stored action intent failed integrity verification')
        events=decode(row['audit'])
        if not events or len(events)>100: raise RuntimeError('action audit missing or exceeds event budget')
        prior=None;phase=None
        for index,event in enumerate(events,1):
            card={'action_id':action_id,'revision':index,'phase':event['phase'],'facts':decode(event['facts']),'previous_hash':prior}
            if (event['revision']!=index or event['previous_hash']!=prior or event['event_hash']!=stable_id(card)
                or phase is not None and event['phase'] not in TRANSITIONS[phase]
                or phase is None and event['phase'] not in ('prepared','blocked')):
                raise RuntimeError('action audit chain/transition failed integrity verification')
            prior,phase=event['event_hash'],event['phase']
        if (row['revision']!=len(events) or phase!=row['phase'] or facts!=decode(events[-1]['facts'])):
            raise RuntimeError('action state differs from immutable audit')
        return {'action_id':action_id,'request_hash':row['request_hash'],'intent':intent,'revision':row['revision'],
                'phase':row['phase'],'facts':facts,'last_event_hash':prior,'recorded_at':timestamp(row['recorded_at'])}

    async def get(self,action_id):
        async with self.pool.acquire() as conn,conn.transaction(isolation='repeatable_read',readonly=True):
            return await self._read(conn,action_id)

    async def find(self,request,target):
        async with self.pool.acquire() as conn,conn.transaction(isolation='repeatable_read',readonly=True):
            identifier=await conn.fetchval('SELECT action_id FROM sentinel_remediation.intents WHERE idempotency_key=$1',request.idempotency_key)
            if identifier is None: return None
            stored=await self._read(conn,identifier)
            if stored['request_hash']!=request.fingerprint(target): raise ActionConflict('idempotency key has different contents')
            return stored

    async def incident_current(self,request):
        async with self.pool.acquire() as conn:
            row=await conn.fetchrow('SELECT current_revision,lifecycle_stage FROM sentinel_control.incidents WHERE incident_id=$1',request.incident_id)
        return bool(row and row['current_revision']==request.incident_revision and row['lifecycle_stage']=='RCA_READY')

    async def _event(self,conn,action_id,revision,phase,facts,previous):
        card={'action_id':action_id,'revision':revision,'phase':phase,'facts':facts,'previous_hash':previous}
        await conn.execute('''INSERT INTO sentinel_remediation.events(action_id,revision,phase,facts,previous_hash,event_hash)
            VALUES($1,$2,$3,$4::jsonb,$5,$6)''',action_id,revision,phase,json.dumps(facts,allow_nan=False),previous,stable_id(card))

    async def propose(self,request,policy,*,evidence=None,blocked_reason=None):
        try:
            return await self._propose(request,policy,evidence=evidence,blocked_reason=blocked_reason)
        except asyncpg.UniqueViolationError:
            raise ActionConflict('action idempotency identity conflicts with another target') from None
        except asyncpg.ForeignKeyViolationError:
            raise Blocked('incident-version-does-not-exist') from None

    async def _propose(self,request,policy,*,evidence=None,blocked_reason=None):
        payload={'intent_version':'payment-rollback-intent/v1','request':asdict(request),'policy':asdict(policy),
                 'policy_sha256':policy.identity,'evidence':evidence}
        fingerprint=request.fingerprint(policy.target);identifier=stable_id({'request_hash':fingerprint})
        async with self.pool.acquire() as conn,conn.transaction():
            await conn.execute('INSERT INTO sentinel_remediation.targets(target_key) VALUES($1) ON CONFLICT DO NOTHING',policy.target.key)
            target=await conn.fetchrow('SELECT * FROM sentinel_remediation.targets WHERE target_key=$1 FOR UPDATE',policy.target.key)
            existing=await conn.fetchval('SELECT action_id FROM sentinel_remediation.intents WHERE idempotency_key=$1',request.idempotency_key)
            if existing:
                stored=await self._read(conn,existing)
                if stored['request_hash']!=fingerprint: raise ActionConflict('idempotency key has different contents')
                return stored
            now=await conn.fetchval('SELECT clock_timestamp()')
            latest_dispatch=await conn.fetchval('SELECT max(recorded_at) FROM sentinel_remediation.dispatches WHERE target_key=$1',policy.target.key)
            dispatch_clocks=[v for v in (latest_dispatch,target['last_dispatch_at']) if v is not None]
            if blocked_reason is None:
                row=await conn.fetchrow('SELECT current_revision,lifecycle_stage FROM sentinel_control.incidents WHERE incident_id=$1',request.incident_id)
                if not row or row['current_revision']!=request.incident_revision or row['lifecycle_stage']!='RCA_READY':
                    blocked_reason='incident-revision-or-lifecycle-changed'
                elif target['active_action']: blocked_reason='target-active-or-quarantined'
                elif dispatch_clocks and now-max(dispatch_clocks)<timedelta(seconds=policy.cooldown_seconds):
                    blocked_reason='target-cooldown'
                elif await conn.fetchval('SELECT EXISTS(SELECT FROM sentinel_remediation.dispatches WHERE incident_id=$1 AND target_key=$2)',request.incident_id,policy.target.key):
                    blocked_reason='incident-target-attempt-budget'
            phase='blocked' if blocked_reason else 'prepared';facts={'reason':blocked_reason} if blocked_reason else {}
            await conn.execute('''INSERT INTO sentinel_remediation.intents(action_id,idempotency_key,request_hash,intent_hash,target_key,incident_id,incident_revision,payload)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb)''',identifier,request.idempotency_key,fingerprint,stable_id(payload),
                policy.target.key,request.incident_id,request.incident_revision,json.dumps(payload,allow_nan=False))
            await conn.execute('INSERT INTO sentinel_remediation.states(action_id,revision,phase,facts) VALUES($1,1,$2,$3::jsonb)',identifier,phase,json.dumps(facts))
            await self._event(conn,identifier,1,phase,facts,None)
            if phase=='prepared': await conn.execute('UPDATE sentinel_remediation.targets SET active_action=$2 WHERE target_key=$1',policy.target.key,identifier)
            return await self._read(conn,identifier)

    async def _advance(self,conn,state,phase,facts,*,release=False):
        if phase not in TRANSITIONS[state['phase']]: raise ActionConflict('invalid action transition')
        seq=state['revision']+1;identifier=state['action_id']
        await self._event(conn,identifier,seq,phase,facts,state['last_event_hash'])
        changed=await conn.execute('''UPDATE sentinel_remediation.states SET revision=$2,phase=$3,facts=$4::jsonb,
            updated_at=clock_timestamp() WHERE action_id=$1 AND revision=$5''',identifier,seq,phase,json.dumps(facts,allow_nan=False),state['revision'])
        if changed!='UPDATE 1': raise ActionConflict('action revision changed')
        if release:
            await conn.execute('UPDATE sentinel_remediation.targets SET active_action=NULL WHERE active_action=$1',identifier)
        return await self._read(conn,identifier)

    async def arm(self,action_id,revision):
        try:
            async with self.pool.acquire() as conn,conn.transaction():
                state=await self._read(conn,action_id,lock=True)
                if not state or state['revision']!=revision or state['phase']!='prepared': raise ActionConflict('dispatch already armed or action changed')
                request=Request(**state['intent']['request']);target_key=Target(**state['intent']['policy']['target']).key
                target=await conn.fetchrow('SELECT * FROM sentinel_remediation.targets WHERE target_key=$1 FOR UPDATE',target_key)
                current=await conn.fetchrow('SELECT current_revision,lifecycle_stage FROM sentinel_control.incidents WHERE incident_id=$1',request.incident_id)
                if (target['active_action']!=action_id or not current or current['current_revision']!=request.incident_revision or current['lifecycle_stage']!='RCA_READY'):
                    raise Blocked('dispatch-target-or-incident-changed')
                started=await conn.fetchval('''INSERT INTO sentinel_remediation.dispatches(action_id,target_key,incident_id)
                    VALUES($1,$2,$3) RETURNING recorded_at''',action_id,target_key,request.incident_id)
                await conn.execute('UPDATE sentinel_remediation.targets SET last_dispatch_at=$2 WHERE target_key=$1',target_key,started)
                return await self._advance(conn,state,'dispatching',{'dispatch_started_at':timestamp(started)})
        except asyncpg.UniqueViolationError:
            raise ActionConflict('incident-target dispatch budget exhausted') from None

    async def transition(self,action_id,revision,phase,facts,*,release=False):
        async with self.pool.acquire() as conn,conn.transaction():
            state=await self._read(conn,action_id,lock=True)
            if not state or state['revision']!=revision: raise ActionConflict('action revision changed')
            if phase=='recovered':
                now=await conn.fetchval('SELECT clock_timestamp()')
                if now-utc_time(state['facts']['dispatch_started_at'])>=timedelta(seconds=state['intent']['policy']['verification_seconds']):
                    phase,facts,release='escalated',{**state['facts'],'reason':'verification-expired-before-recovery-commit'},False
            return await self._advance(conn,state,phase,facts,release=release)
