"""Real PostgreSQL action-journal acceptance in an exclusively owned disposable DB.

The incidents, clocks and terminal action states are SOFTWARE FIXTURES. No
Kubernetes operation or genuine RCA/action/recovery acceptance is claimed.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import hashlib
from pathlib import Path
import secrets
import socket
import subprocess
from uuid import uuid4
from types import SimpleNamespace

import asyncpg

from sentinelops.incidents.domain import IncidentWrite
from sentinelops.incidents.repository import IncidentRepository
from sentinelops.remediation.authority import NativeH4Authority
from sentinelops.remediation.domain import ActionConflict,Blocked,Policy,Request,Target
from sentinelops.remediation.journal import ActionJournal
from sentinelops.remediation.executor import RollbackExecutor

PROJECT=Path(__file__).resolve().parents[1]
OWNER='sentinelops-b-action-tests'


def docker(*args,data=None):
    return subprocess.check_output(['docker',*args],input=data,stderr=subprocess.PIPE,timeout=90)


def require_owned(name):
    labels=json.loads(docker('inspect','--format','{{json .Config.Labels}}',name))
    if labels.get('sentinelops.owner')!=OWNER or labels.get('sentinelops.checkout')!=str(PROJECT):
        raise ValueError('refuse to mutate an unowned action-test container')


async def test_database(name,port,bootstrap_password,runtime_password):
    bootstrap=await asyncpg.connect(f'postgresql://control_bootstrap:{bootstrap_password}@127.0.0.1:{port}/sentinelops_control')
    core=action=None
    checks=[]
    try:
        for filename in ('001-local-roles.sql','002-incident-store.sql','003-health-worker.sql',
                         '004-incident-consumer.sql','005-remediation.sql','005-remediation.sql'):
            await bootstrap.execute((PROJECT/'infra/control'/filename).read_text())
        statement=await bootstrap.fetchval("SELECT format('CREATE ROLE remediation_test LOGIN PASSWORD %L IN ROLE control_action_writer NOSUPERUSER NOCREATEDB NOCREATEROLE', $1::text)",runtime_password)
        await bootstrap.execute(statement)
        action=await asyncpg.create_pool(f'postgresql://remediation_test:{runtime_password}@127.0.0.1:{port}/sentinelops_control',min_size=1,max_size=12,command_timeout=10)
        core=await asyncpg.create_pool(f'postgresql://control_writer:local-control-writer@127.0.0.1:{port}/sentinelops_control',min_size=1,max_size=2)
        journal=ActionJournal(action);repository=IncidentRepository(core)
        await journal.check_ready();await repository.check_ready()
        await bootstrap.execute('REVOKE INSERT ON sentinel_remediation.events FROM control_action_writer')
        try:
            await journal.check_ready()
            raise AssertionError('readiness accepted a role unable to commit audit')
        except RuntimeError as exc: assert 'lacks required' in str(exc)
        await bootstrap.execute('GRANT INSERT ON sentinel_remediation.events TO control_action_writer')
        await bootstrap.execute('GRANT DELETE ON sentinel_remediation.targets TO control_action_writer')
        try:
            await journal.check_ready()
            raise AssertionError('readiness accepted mutable target lock deletion')
        except RuntimeError as exc: assert 'delete durable' in str(exc)
        await bootstrap.execute('REVOKE DELETE ON sentinel_remediation.targets FROM control_action_writer')
        await journal.check_ready()
        checks.append('readiness rejects missing audit INSERT and excess durable target DELETE privileges')
        assert await bootstrap.fetchval('SELECT array_agg(version ORDER BY version) FROM sentinel_control.schema_migrations')==[1,2,3]
        checks.append('optional action migration is idempotent and preserves core [1,2,3] readiness')
        async def incident():
            payload=json.loads((PROJECT/'samples/incident-evidence-record.sample.json').read_text())
            payload['incident_id']='INC-ACTION-STORE-'+uuid4().hex
            context={'availability':'ELIGIBLE','recovery_source':None,'source_refs':['fixture://action-store'],
                'config_refs':['config://action-store-fixture'], 'trigger_sources':[{'source':'manual',
                    'signal_id':uuid4().hex,'emitted_at':payload['detected_at'],'source_ref':'fixture://action-store'}]}
            command=IncidentWrite(payload,'OPEN',0,uuid4().hex,'action-store-fixture','software storage fixture, not H4',context)
            await repository.write(command)
            await repository.write(replace(command,expected_revision=1,lifecycle_stage='RCA_READY',idempotency_key=uuid4().hex))
            return payload['incident_id']
        target=Target('cluster-fixture','sentinelops-b-actions','namespace-fixture','payment-service','deployment-fixture','payment')
        policy=Policy(target,enabled=True)
        identifier=await incident();request=Request(identifier,2,uuid4().hex)
        native=NativeH4Authority(SimpleNamespace(pool=action,source=SimpleNamespace(stream_id='a'*64),consumer_id='b'*64))
        try:
            await native.assess(request,target,now=None)
            raise AssertionError('manual imported IER obtained native H4 authority')
        except Blocked as exc:
            assert str(exc)=='incident-has-no-verified-native-job'
        checks.append('actual source query rejects manual IER imports as native action authority')
        class NoKubernetesCalls:
            async def observe(self): raise AssertionError('unverified manual IER reached Kubernetes')
            async def patch(self,*args,**kwargs): raise AssertionError('unverified manual IER reached image patch')
        executor=RollbackExecutor(journal,NoKubernetesCalls(),native,None,None,policy,
            json.loads((PROJECT/'configs/rca_policy.json').read_text()),enabled=lambda:True)
        rejected=await executor.execute(replace(request,idempotency_key=uuid4().hex))
        assert rejected['phase']=='blocked' and rejected['facts']['reason']=='incident-has-no-verified-native-job'
        assert (await repository.get(identifier))['revision']==2
        checks.append('native executor persists blocked manual IER in real PostgreSQL without Kubernetes access or IER mutation')
        proposals=await asyncio.gather(*(journal.propose(request,policy,evidence={'fixture_only':True}) for _ in range(8)))
        state=proposals[0]
        assert all(p==state for p in proposals) and state['phase']=='prepared' and state['revision']==1
        checks.append('eight concurrent identical proposals share one intent, target claim and audit event')
        with_conflict=replace(request,incident_revision=1)
        try:
            await journal.propose(with_conflict,policy)
            raise AssertionError('changed idempotency contents accepted')
        except ActionConflict: pass
        competitor=await journal.propose(replace(request,idempotency_key=uuid4().hex),policy)
        assert competitor['phase']=='blocked' and competitor['facts']['reason']=='target-active-or-quarantined'
        armed=await asyncio.gather(*(journal.arm(state['action_id'],1) for _ in range(8)),return_exceptions=True)
        assert sum(isinstance(v,dict) for v in armed)==1 and sum(isinstance(v,ActionConflict) for v in armed)==7
        state=await journal.get(state['action_id'])
        assert state['phase']=='dispatching' and state['revision']==2
        assert await bootstrap.fetchval('SELECT count(*) FROM sentinel_remediation.dispatches')==1
        checks.append('single CAS winner commits one dispatch budget before any side effect')
        async with action.acquire() as conn:
            for statement in ('UPDATE sentinel_remediation.events SET phase=phase',
                'DELETE FROM sentinel_remediation.intents','UPDATE sentinel_remediation.dispatches SET incident_id=incident_id',
                "UPDATE sentinel_control.incident_versions SET reason=reason",'SET ROLE control_owner'):
                try:
                    await conn.execute(statement)
                    raise AssertionError('runtime mutated protected history or assumed owner')
                except asyncpg.InsufficientPrivilegeError: pass
            try:
                await conn.execute('''INSERT INTO sentinel_remediation.dispatches(action_id,target_key,incident_id)
                    VALUES($1,$2,$3)''',competitor['action_id'],target.key,identifier)
                raise AssertionError('a second request bypassed per-incident/target attempt budget')
            except asyncpg.UniqueViolationError: pass
        async with core.acquire() as conn:
            try:
                await conn.execute('SELECT count(*) FROM sentinel_remediation.events')
                raise AssertionError('AI/API incident role obtained action access')
            except asyncpg.InsufficientPrivilegeError: pass
        checks.append('action role cannot alter history/IER or assume owner; incident role cannot access action schema')
        checks.append('immutable dispatch unique constraint rejects a different idempotency key for the same incident/target')
        state=await journal.transition(state['action_id'],state['revision'],'escalated',{**state['facts'],'reason':'fixture ambiguous transport'})
        blocked=await journal.propose(Request(await incident(),2,uuid4().hex),policy)
        assert blocked['phase']=='blocked' and blocked['facts']['reason']=='target-active-or-quarantined'
        checks.append('ambiguous escalation keeps a durable target quarantine across incidents')
        # Separate target identities below remain synthetic storage fixtures.
        second=replace(policy,target=replace(target,deployment_uid='second-fixture'))
        request2=Request(await incident(),2,uuid4().hex)
        state2=await journal.propose(request2,second,evidence={'fixture_only':True})
        state2=await journal.arm(state2['action_id'],state2['revision'])
        state2=await journal.transition(state2['action_id'],state2['revision'],'verifying',state2['facts'])
        state2=await journal.transition(state2['action_id'],state2['revision'],'recovered',state2['facts'],release=True)
        assert state2['phase']=='recovered'
        cooled=await journal.propose(Request(await incident(),2,uuid4().hex),second)
        assert cooled['phase']=='blocked' and cooled['facts']['reason']=='target-cooldown'
        checks.append('terminal fixture releases active claim but the 600-second cooldown remains enforced')
        third=replace(policy,target=replace(target,deployment_uid='third-fixture'))
        state3=await journal.propose(Request(await incident(),2,uuid4().hex),third,evidence={'fixture_only':True})
        # This private fixture DB deliberately rejects an audit write after the
        # dispatch insert. No production role or source is modified.
        await bootstrap.execute("ALTER TABLE sentinel_remediation.events ADD CONSTRAINT fixture_audit_failure CHECK (action_id <> '"+state3['action_id']+"' OR phase <> 'dispatching') NOT VALID")
        try:
            await journal.arm(state3['action_id'],1)
            raise AssertionError('audit failure was ignored')
        except asyncpg.CheckViolationError: pass
        assert (await journal.get(state3['action_id']))['phase']=='prepared'
        assert not await bootstrap.fetchval('SELECT EXISTS(SELECT FROM sentinel_remediation.dispatches WHERE action_id=$1)',state3['action_id'])
        await bootstrap.execute('ALTER TABLE sentinel_remediation.events DROP CONSTRAINT fixture_audit_failure')
        state3=await journal.arm(state3['action_id'],1)
        assert state3['phase']=='dispatching'
        checks.append('failure after dispatch insertion rolls back intent state, dispatch and target clock; retry commits once')
        async def snapshots():
            for _ in range(50):
                await journal.get(state3['action_id'])
                await asyncio.sleep(.001)
        async def transitions():
            next_state=await journal.transition(state3['action_id'],state3['revision'],'verifying',state3['facts'])
            return await journal.transition(next_state['action_id'],next_state['revision'],'escalated',next_state['facts'])
        await asyncio.gather(snapshots(),transitions())
        checks.append('concurrent read snapshots match the immutable audit while transitions commit')
        before=await journal.get(state3['action_id'])
        require_owned(name);docker('stop',name)
        try:
            await journal.get(state3['action_id'])
            raise AssertionError('action DB read succeeded during actual database outage')
        except (asyncpg.PostgresConnectionError,asyncpg.CannotConnectNowError,OSError): pass
        require_owned(name);docker('start',name)
        for _ in range(60):
            try:
                await journal.check_ready();break
            except (asyncpg.PostgresConnectionError,asyncpg.CannotConnectNowError,OSError):
                await asyncio.sleep(.25)
        else: raise AssertionError('action store failed to reconnect')
        assert await journal.get(state3['action_id'])==before
        await repository.check_ready()
        checks.append('actual PostgreSQL stop/start preserves exact immutable action history and core readiness')
        return {'status':'PASS','fixture_only':True,'kubernetes_mutated':False,'H4_accepted':False,
                'core_migrations':[1,2,3],'action_migrations':[1],'checks':checks}
    finally:
        if action: await action.close()
        if core: await core.close()
        await bootstrap.close()


async def run(output):
    if output.exists(): raise ValueError('exclusive action acceptance output already exists')
    hashes={name:hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() for name in (
        'scripts/check_remediation_store.py','src/sentinelops/remediation/journal.py','infra/control/005-remediation.sql')}
    image=docker('image','inspect','postgres:16-alpine','--format','{{.Id}}').decode().strip()
    name='sentinelops-b-actions-'+uuid4().hex[:12]
    password,runtime_password=secrets.token_hex(24),secrets.token_hex(24)
    created=False
    try:
        # Docker's published-port=0 can allocate a DIFFERENT port after stop/start.
        # Reserve a free loopback port and make that mapping explicit before the
        # outage test. Create first so every failed start has owned cleanup.
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0));selected_port=reservation.getsockname()[1]
            docker('create','--name',name,'--label','sentinelops.owner='+OWNER,
                '--label','sentinelops.checkout='+str(PROJECT),'--memory','256m','--cpus','1',
                '-e','POSTGRES_USER=control_bootstrap','-e','POSTGRES_DB=sentinelops_control',
                '-e','POSTGRES_PASSWORD='+password,'-p',f'127.0.0.1:{selected_port}:5432',image)
            created=True
        require_owned(name);docker('start',name)
        for _ in range(100):
            try:
                # The image's bootstrap server accepts Unix sockets before the
                # final TCP server starts; host asyncpg must wait for TCP.
                docker('exec',name,'pg_isready','-h','127.0.0.1','-U','control_bootstrap','-d','sentinelops_control');break
            except subprocess.CalledProcessError: await asyncio.sleep(.2)
        else: raise RuntimeError('private test PostgreSQL did not start')
        ports=json.loads(docker('inspect','--format','{{json .NetworkSettings.Ports}}',name))['5432/tcp']
        if len(ports)!=1 or ports[0]['HostIp']!='127.0.0.1' or int(ports[0]['HostPort'])!=selected_port:
            raise ValueError('test DB must publish the selected private loopback port')
        report=await test_database(name,int(ports[0]['HostPort']),password,runtime_password)
        report['loaded_source_sha256']=hashes
        report['observer_changed_during_run']=any(hashlib.sha256((PROJECT/name).read_bytes()).hexdigest()!=sha for name,sha in hashes.items())
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x') as stream: json.dump(report,stream,indent=2)
        print('REMEDIATION POSTGRESQL ACCEPTANCE: PASS '+str(output))
    finally:
        if created:
            require_owned(name)
            docker('rm','-f','-v',name)  # Only this newly owned disposable container/anonymous data.


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args().output))
