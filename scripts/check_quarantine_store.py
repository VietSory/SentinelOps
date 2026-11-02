"""Exercise operator ACL/CAS/atomic receipts in the caller's private fixture DB.

All seeded clocks, image/telemetry references and action results are explicit
STORAGE FIXTURES, not observed Kubernetes/SLO evidence or a real H4 grant.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx

from sentinelops.api import create_app
from sentinelops.api_settings import Settings
from sentinelops.contracts import utc_time
from sentinelops.remediation.domain import ActionConflict, Blocked, Policy, Request
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.quarantine import QuarantineJournal,FUNCTION
from sentinelops.remediation.reader import ActionReader
from sentinelops.telemetry.core import stable_id,timestamp

PROJECT=Path(__file__).resolve().parents[1]


async def exercise(bootstrap,writer,reader_pool,repository,incident,target,port,password):
    for _ in range(2): await bootstrap.execute((PROJECT/'infra/control/006-quarantine-review.sql').read_text())
    sql=await bootstrap.fetchval("SELECT format('CREATE ROLE quarantine_operator_test LOGIN PASSWORD %L IN ROLE control_action_resolver NOSUPERUSER NOCREATEDB NOCREATEROLE', $1::text)",password)
    await bootstrap.execute(sql)
    pool=await asyncpg.create_pool(f'postgresql://quarantine_operator_test:{password}@127.0.0.1:{port}/sentinelops_control',min_size=1,max_size=10,command_timeout=5)
    journal=ActionJournal(writer);reader=ActionReader(reader_pool);operator=QuarantineJournal(pool)
    checks=[]
    try:
        await journal.check_ready();await reader.check_ready();await operator.check_ready()
        try:
            await ActionReader(pool).check_ready()
            raise AssertionError('operator function credential accepted by read-only API')
        except RuntimeError as exc: assert 'release quarantine' in str(exc)
        for forbidden in (writer,reader_pool):
            try:
                await forbidden.fetchval('SELECT '+FUNCTION.split('(')[0]+'($1,$2,$3,$4,$5,$6::jsonb)',
                    'a'*64,1,'b'*64,'artifact://manifests/'+'c'*64+'.json','fixture-key-0000001','{}')
                raise AssertionError('nonoperator executed quarantine release')
            except asyncpg.InsufficientPrivilegeError: pass
        for statement in ('UPDATE sentinel_remediation.targets SET active_action=NULL',
            'UPDATE sentinel_remediation.states SET phase=phase','DELETE FROM sentinel_remediation.quarantine_releases',
            'INSERT INTO sentinel_remediation.quarantine_releases(action_id) VALUES(NULL)',
            'UPDATE sentinel_control.incident_versions SET reason=reason','SET ROLE control_owner'):
            try:
                await pool.execute(statement)
                raise AssertionError('operator bypassed guarded function or mutated core/audit')
            except asyncpg.InsufficientPrivilegeError: pass
        checks.append('operator has no raw DML; API/writer cannot execute release; API rejects operator credential')

        async def seed(*,phase='escalated',old=True):
            selected=replace(target,deployment_uid='quarantine-fixture-'+uuid4().hex)
            request=Request(await incident(),2,uuid4().hex);policy=Policy(selected,enabled=True)
            intent={'intent_version':'payment-rollback-intent/v1','request':asdict(request),'policy':asdict(policy),
                    'policy_sha256':policy.identity,'evidence':{'fixture_only':True}}
            request_hash=request.fingerprint(selected);identifier=stable_id({'request_hash':request_hash})
            now=await bootstrap.fetchval('SELECT clock_timestamp()');at=now-timedelta(minutes=20) if old else now
            facts={'dispatch_started_at':timestamp(at),'reason':'explicit synthetic quarantine storage fixture'}
            async with bootstrap.transaction():
                await bootstrap.execute('''INSERT INTO sentinel_remediation.intents(action_id,idempotency_key,request_hash,intent_hash,
                    target_key,incident_id,incident_revision,payload,recorded_at) VALUES($1,$2,$3,$4,$5,$6,2,$7::jsonb,$8)''',
                    identifier,request.idempotency_key,request_hash,stable_id(intent),selected.key,request.incident_id,json.dumps(intent),at)
                await bootstrap.execute('INSERT INTO sentinel_remediation.states(action_id,revision,phase,facts,updated_at) VALUES($1,3,$2,$3::jsonb,$4)',identifier,phase,json.dumps(facts),at)
                prior=None
                for seq,status in enumerate(('prepared','dispatching',phase),1):
                    body={'action_id':identifier,'revision':seq,'phase':status,'facts':{} if seq==1 else facts,'previous_hash':prior}
                    event_hash=stable_id(body)
                    await bootstrap.execute('''INSERT INTO sentinel_remediation.events(action_id,revision,phase,facts,previous_hash,event_hash,recorded_at)
                        VALUES($1,$2,$3,$4::jsonb,$5,$6,$7)''',identifier,seq,status,json.dumps(body['facts']),prior,event_hash,at)
                    prior=event_hash
                await bootstrap.execute('INSERT INTO sentinel_remediation.targets(target_key,active_action,last_dispatch_at) VALUES($1,$2,$3)',selected.key,identifier,at)
                await bootstrap.execute('INSERT INTO sentinel_remediation.dispatches(action_id,target_key,incident_id,recorded_at) VALUES($1,$2,$3,$4)',identifier,selected.key,request.incident_id,at)
            state=await journal.get(identifier)
            return state,selected,request

        async def payload(state,selected):
            now=await bootstrap.fetchval('SELECT clock_timestamp()')
            # Fixtures avoid scheduling a concurrent RPC across a watermark edge;
            # the exact commit-time edge rejection is separately exercised below.
            if now.second in (28,29,58,59):
                await asyncio.sleep(3)
                now=await bootstrap.fetchval('SELECT clock_timestamp()')
            end=(now-timedelta(seconds=30)).replace(second=0,microsecond=0)
            return {'release_version':'manual-quarantine-release/v1','review_ref':'artifact://manifests/'+stable_id(state['action_id'])+'.json',
                'action_id':state['action_id'],'action_revision':state['revision'],'terminal_event_hash':state['last_event_hash'],
                'target_key':selected.key,'opened_at':timestamp(now-timedelta(minutes=5)),'observed_at':timestamp(now),
                'verification_as_of':timestamp(now),'last_bucket_end':timestamp(end),
                'before_ref':'artifact://observations/'+'a'*64+'.json','after_ref':'artifact://observations/'+'b'*64+'.json',
                'health_ref':'artifact://manifests/'+'c'*64+'.json',
                'dispatch_authorized':False,'recovery_accepted':False,'ier_mutated':False}

        async def call(state,proof,key,*,raw=False):
            args=dict(action_id=state['action_id'],revision=state['revision'],event_hash=state['last_event_hash'],
                      review_ref=proof['review_ref'],key=key,payload=proof)
            if raw:
                return await pool.fetchval('SELECT '+FUNCTION.split('(')[0]+'($1,$2,$3,$4,$5,$6::jsonb)',
                    args['action_id'],args['revision'],args['event_hash'],args['review_ref'],key,json.dumps(proof))
            return await operator.release(**args)

        state,selected,request=await seed();baseline=await journal.get(state['action_id'])
        for problem in ('null-clock','missing-clock','future','stale','wrong-anchor','too-short','approval','extra-actor'):
            proof=await payload(state,selected)
            if problem=='null-clock': proof['observed_at']=None
            if problem=='missing-clock': del proof['opened_at']
            if problem=='future': proof['observed_at']=timestamp((await bootstrap.fetchval('SELECT clock_timestamp()'))+timedelta(seconds=10))
            if problem=='stale': proof['verification_as_of']=timestamp((await bootstrap.fetchval('SELECT clock_timestamp()'))-timedelta(seconds=31))
            if problem=='wrong-anchor': proof['last_bucket_end']=timestamp((await bootstrap.fetchval('SELECT clock_timestamp()'))-timedelta(minutes=2))
            if problem=='too-short': proof['opened_at']=timestamp(utc_time(proof['last_bucket_end'])-timedelta(seconds=179))
            if problem=='approval': proof['recovery_accepted']=True
            if problem=='extra-actor': proof['operator_actor']='forged-owner'
            try:
                await call(state,proof,uuid4().hex,raw=True)
                raise AssertionError('database accepted '+problem)
            except asyncpg.CheckViolationError: pass
            assert (await operator.context(state['action_id']))['active_action']==state['action_id']
        checks.append('DB rejects null/missing/future/stale clocks, wrong watermark, short interval and forged approval/actor')
        inflight,t,_=await seed(phase='verifying')
        try:
            await call(inflight,await payload(inflight,t),uuid4().hex)
            raise AssertionError('inflight action quarantine released')
        except ActionConflict: pass
        recent,t,_=await seed(old=False)
        try:
            await call(recent,await payload(recent,t),uuid4().hex)
            raise AssertionError('release skipped dispatch drain period')
        except Blocked: pass
        checks.append('inflight/nonterminal states and unexpired dispatch drain stay locked')

        # A failing receipt INSERT must leave the target held, atomically.
        await bootstrap.execute("ALTER TABLE sentinel_remediation.quarantine_releases ADD CONSTRAINT fixture_receipt_failure CHECK(action_id <> '"+state['action_id']+"') NOT VALID")
        try:
            await call(state,await payload(state,selected),uuid4().hex)
            raise AssertionError('receipt failure ignored')
        except Blocked: pass
        assert (await operator.context(state['action_id']))['active_action']==state['action_id']
        await bootstrap.execute('ALTER TABLE sentinel_remediation.quarantine_releases DROP CONSTRAINT fixture_receipt_failure')
        proof=await payload(state,selected);key=uuid4().hex
        results=await asyncio.gather(*(call(state,proof,key) for _ in range(8)),return_exceptions=True)
        errors=[result for result in results if isinstance(result,BaseException)]
        if errors: raise errors[0]  # Await every contender before closing its pool.
        assert all(result==results[0] for result in results)
        assert results[0]['operator_actor']=='quarantine_operator_test'
        assert await pool.fetchval('SELECT count(*) FROM sentinel_remediation.quarantine_releases WHERE action_id=$1',state['action_id'])==1
        assert await journal.get(state['action_id'])==baseline
        assert (await operator.context(state['action_id']))['active_action'] is None
        assert await pool.fetchval('SELECT last_dispatch_at FROM sentinel_remediation.targets WHERE target_key=$1',selected.key)==await pool.fetchval('SELECT recorded_at FROM sentinel_remediation.dispatches WHERE action_id=$1',state['action_id'])
        assert (await repository.get(request.incident_id))['revision']==2
        attempt=await journal.propose(replace(request,idempotency_key=uuid4().hex),Policy(selected,enabled=True))
        assert attempt['phase']=='blocked' and attempt['facts']['reason']=='incident-target-attempt-budget'
        try:
            await call(state,proof,uuid4().hex)
            raise AssertionError('changed idempotency key accepted after release')
        except ActionConflict: pass
        checks.append('eight concurrent retries produce one attributed receipt; receipt failure preserves lock; action/IER/cooldown/attempt history unchanged')
        observed=await reader.quarantine(state['action_id'])
        assert observed['quarantine_state']=='released' and observed['action_phase']=='escalated'
        token=uuid4().hex+uuid4().hex
        app=create_app(Settings(api_token=token),repository=repository,action_reader=reader)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer '+token}) as client:
            path='/v1/actions/'+state['action_id']+'/quarantine'
            response=await client.get(path)
            assert response.status_code==200 and response.json()==observed
            assert (await client.get(path,headers={'If-None-Match':response.headers['etag']})).status_code==304
            assert (await client.post(path,json={'released':True})).status_code==405
        checks.append('read-only HTTP exposes immutable manual receipt separately from escalated action and rejects POST')
        return checks,state['action_id'],observed
    finally:
        await pool.close()
