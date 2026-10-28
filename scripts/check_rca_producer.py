"""Computed RCA → IER → PostgreSQL/HTTP acceptance in an exclusive local DB."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime,timezone
import json
import hashlib
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import asyncpg
import httpx

from sentinelops.incidents.domain import IncidentConflict
from sentinelops.contracts import utc_time
from sentinelops.incidents.rca_producer import prepare_intent,project_commands,publish_intent,verified_intent
from sentinelops.incidents.repository import IncidentRepository
from sentinelops.telemetry.store import ArtifactStore


async def wait_for_cutoff(cutoff, *, maximum_wait_seconds=60):
    """Fresh replay clocks may lead wall time by the health policy's 60s lag.

    Keep prepare_intent's future-emission rejection; only this test driver waits.
    """
    started=time.monotonic()
    while True:
        remaining=(cutoff-datetime.now(timezone.utc)).total_seconds()
        if remaining<=0:return time.monotonic()-started
        if remaining>maximum_wait_seconds or time.monotonic()-started>=maximum_wait_seconds:
            raise ValueError('analytical cutoff exceeds the bounded acceptance wait')
        if time.monotonic()-started<.01:
            print('WAITING FOR ACTUAL CLOCK TO REACH ANALYTICAL CUTOFF',flush=True)
        await asyncio.sleep(min(remaining,10.))


async def check(args):
    dsn=os.environ.get("CONTROL_DATABASE_URL","");parsed=urlparse(dsn)
    if parsed.hostname not in ("127.0.0.1","localhost") or parsed.path!="/sentinelops_control" or parsed.username!="control_writer":
        raise ValueError("acceptance requires the exclusive local control sandbox")
    if args.database_container:
        ports=subprocess.check_output(["docker","port",args.database_container,"5432/tcp"],text=True).splitlines()
        if f"127.0.0.1:{parsed.port or 5432}" not in ports:
            raise ValueError("restart target does not expose the selected local control port")
    store=ArtifactStore(args.bundle);policy=json.loads(args.policy.read_text())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    pool=await asyncpg.create_pool(dsn,min_size=1,max_size=12,command_timeout=10,timeout=5)
    repository=IncidentRepository(pool);process=None;database_stopped=False
    try:
        await repository.check_ready()
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM sentinel_control.incidents")==0,"requires an empty exclusive acceptance DB"
        waited=await wait_for_cutoff(utc_time(store.read(args.report_ref)['as_of']))
        intent_ref=prepare_intent(store,args.report_ref,policy=policy)
        intent,source=verified_intent(store,intent_ref,policy=policy)
        assert source['group_count']>0,"requires a captured source with actual SLO openings"
        concurrent=await asyncio.gather(*(publish_intent(repository,store,intent_ref,policy=policy) for _ in range(8)))
        published=concurrent[0]
        assert all(row==published for row in concurrent),"concurrent retry changed stable replay projection"
        commands=project_commands(intent_ref,intent,source)
        for group in commands:
            assert await repository.write(group[0])==await repository.get(group[0].payload['incident_id'],1)
            for changed in (replace(group[0],reason="changed replay retry contents"),
                            replace(group[1],idempotency_key=group[1].idempotency_key+":stale")):
                try:
                    await repository.write(changed)
                    raise AssertionError("changed retry/stale CAS was accepted")
                except IncidentConflict:pass
        # A distinct replay intent simulates a crash after only OPEN committed.
        partial_ref=prepare_intent(store,args.report_ref,policy=policy)
        partial_intent,partial_source=verified_intent(store,partial_ref,policy=policy)
        partial_commands=project_commands(partial_ref,partial_intent,partial_source)
        await repository.write(partial_commands[0][0])
        resumed=await publish_intent(repository,store,partial_ref,policy=policy)
        expected=published['incidents']+resumed['incidents']
        for item in expected:
            assert (await repository.get(item['incident_id']))['payload']==item['payload']
            assert [e['lifecycle_stage'] for e in await repository.history(item['incident_id'])]==['OPEN','RCA_READY','ESCALATED']
            assert item['payload']['service_signals']==[] and item['context']['availability']=='UNKNOWN'
            assert all(t['source']=='slo' for t in item['context']['trigger_sources'])
        async with pool.acquire() as conn:
            event_ids=set(str(r['event_id']) for r in await conn.fetch('SELECT event_id FROM sentinel_control.outbox'))
            assert len(event_ids)==3*len(expected)
            assert await conn.fetchval('SELECT count(*) FROM sentinel_control.incidents')==len(expected)
        print('COMPUTED RCA DURABLE RETRIES/CAS/CRASH RESUME: PASS',flush=True)

        # Use a real protected API process to read the computed SLO/RCA records.
        token=secrets.token_urlsafe(32)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        env={**os.environ,'SENTINELOPS_API_TOKEN':token,'SENTINELOPS_ENV':'test','SENTINELOPS_ENABLE_DEMO':'false'}
        with (args.output.parent/'rca-api-server.log').open('w') as logs:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','sentinelops.main:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=logs,stderr=logs)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=15,trust_env=False) as client:
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('acceptance API exited')
                try:
                    if (await client.get('/health')).status_code==200:break
                except httpx.HTTPError:pass
                await asyncio.sleep(.2)
            else:raise RuntimeError('acceptance API did not become live')
            auth={'authorization':'Bearer '+token}
            identifier=expected[0]['incident_id']
            assert (await client.get('/v1/incidents/'+identifier)).status_code==401
            assert (await client.get('/v1/incidents/'+identifier,headers=auth)).json()==expected[0]['payload']
            envelope=(await client.get('/v1/incidents/'+identifier+'/context',headers=auth)).json()
            assert envelope['revision']==3 and envelope['context']==expected[0]['context']
            assert [e['lifecycle_stage'] for e in envelope['timeline']['events']]==['OPEN','RCA_READY','ESCALATED']
            assert (await client.get('/ready')).status_code==503
            print('COMPUTED RCA PROTECTED REAL HTTP READ/AUDIT: PASS',flush=True)
            if args.database_container:
                await asyncio.to_thread(subprocess.run,['docker','stop','--time','5',args.database_container],check=True,stdout=subprocess.DEVNULL)
                database_stopped=True
                assert (await client.get('/ready/incidents',headers=auth)).status_code==503
                assert (await client.get('/health')).status_code==200
                try:
                    await publish_intent(repository,store,intent_ref,policy=policy)
                    raise AssertionError('producer wrote successfully during DB outage')
                except (OSError,asyncpg.PostgresError,asyncpg.InterfaceError,TimeoutError):pass
                await asyncio.to_thread(subprocess.run,['docker','start',args.database_container],check=True,stdout=subprocess.DEVNULL)
                database_stopped=False
                ports=subprocess.check_output(['docker','port',args.database_container,'5432/tcp'],text=True).splitlines()
                if f'127.0.0.1:{parsed.port or 5432}' not in ports:
                    raise ValueError('restart changed the control DB port; use an explicit stable published port')
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    if (await client.get('/ready/incidents',headers=auth)).status_code==200:break
                    await asyncio.sleep(.5)
                else:raise RuntimeError('control DB did not recover')
                pool.terminate()
                pool=await asyncpg.create_pool(dsn,min_size=1,max_size=4,command_timeout=10,timeout=5)
                repository=IncidentRepository(pool)
                await repository.check_ready()
                assert await publish_intent(repository,store,intent_ref,policy=policy)==published
                for item in expected:
                    assert (await repository.get(item['incident_id']))['payload']==item['payload']
                    assert len(await repository.history(item['incident_id']))==3
                assert (await client.get('/v1/incidents/'+identifier,headers=auth)).json()==expected[0]['payload']
                print('COMPUTED RCA DB OUTAGE/RESTART/IDEMPOTENT RECOVERY: PASS',flush=True)
        claimed=[]
        while batch:=await repository.claim_events(limit=100):
            for event in batch:
                claimed.append(event)
                await repository.acknowledge(event['event_id'],event['lease_token'])
        assert set(e['event_id'] for e in claimed)==event_ids and len(claimed)==len(event_ids)
        async with pool.acquire() as conn:
            assert await conn.fetchval('SELECT count(*) FROM sentinel_control.outbox WHERE completed_at IS NULL')==0
            assert await conn.fetchval('SELECT count(*) FROM sentinel_control.incident_versions')==3*len(expected)
        report={'status':'PASS','source_report_ref':args.report_ref,'source_commit':source['source_commit'],
            'implementation_sha256':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in
                (Path(__file__),Path('src/sentinelops/incidents/rca_producer.py'),Path('src/sentinelops/incidents/repository.py'))},
            'intent_refs':[intent_ref,partial_ref],'database':'real-PostgreSQL-nonowner-control-writer',
            'transport':'real-protected-HTTP-uvicorn','concurrent_retries':8,'incidents':len(expected),
            'immutable_revisions':3*len(expected),'outbox_events_acknowledged':len(claimed),
            'restart_outage_verified':bool(args.database_container),'model_outputs_available':False,
            'waited_for_analytical_cutoff_seconds':waited,
            'actions_allowed':False,'historical_emissions_verified':False,
            'checks':['verified-raw-derived-RCA','stable-intent-emission','concurrent-idempotent-projection',
                'changed-retry-conflict','stale-CAS-conflict','crash-after-OPEN-resume','immutable-history',
                'computed-SLO-provenance','protected-HTTP-read','no-model-readiness','durable-outbox']}
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print('COMPUTED RCA PERSISTENCE ACCEPTANCE: PASS '+str(args.output),flush=True)
    finally:
        if database_stopped:
            await asyncio.to_thread(subprocess.run,['docker','start',args.database_container],check=True,stdout=subprocess.DEVNULL)
        if process is not None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        await pool.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--report-ref',required=True)
    parser.add_argument('--policy',type=Path,default=Path('configs/rca_policy.json'))
    parser.add_argument('--database-container',help='explicit exclusive test DB; stop/start only after checking its port mapping')
    parser.add_argument('--output',type=Path,default=Path('artifacts/incidents/rca-producer-acceptance.json'))
    args=parser.parse_args()
    try:asyncio.run(check(args))
    except Exception as exc:
        message=str(exc) if isinstance(exc,AssertionError) else type(exc).__name__
        raise SystemExit('Computed RCA acceptance failed: '+message) from None
