import asyncio
import base64
from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace

import httpx
import pytest

from sentinelops.api import create_app
from sentinelops.api_settings import Settings
from sentinelops.contracts import validate_payload
from sentinelops.remediation.domain import RUNBOOK
from sentinelops.remediation.reader import decode_cursor,observation
from sentinelops.telemetry.core import stable_id
from test_remediation_kubernetes import TARGET

TOKEN='readonly-fixture-token-'+'x'*32
AT='2026-10-31T12:00:00Z'


def state():
    return {'action_id':'a'*64,'intent':{'request':{'incident_id':'INC-fixture','incident_revision':2},
        'policy':{'target':asdict(TARGET)},'evidence':{}},'revision':1,'phase':'blocked',
        'facts':{'reason':'rca-abstained'},'recorded_at':AT,'updated_at':AT,
        'history':[{'revision':1,'phase':'blocked','recorded_at':AT,'reason':'rca-abstained',
                    'event_hash':'b'*64,'previous_hash':None}]}


class Reader:
    unavailable=False
    corrupt=False
    async def check_ready(self):
        if self.unavailable: raise RuntimeError('postgresql://SECRET@internal/db')
    async def get(self,key):
        if self.corrupt: raise RuntimeError('SECRET invalid audit')
        return observation(state()) if key=='a'*64 else None
    async def list(self,incident_id,revision,**kwargs):
        self.args=(incident_id,revision,kwargs)
        return {'observation_version':'action-list/v1','incident_id':incident_id,
            'incident_revision':revision,'items':[observation(state())] if revision>=2 else [],'next_cursor':None}
    async def quarantine(self,key):
        if self.corrupt: raise RuntimeError('SECRET optional quarantine migration unavailable')
        return {'observation_version':'quarantine-status/v1','action_id':key,'action_revision':3,
                'action_phase':'escalated','quarantine_state':'held','resolution':None} if key=='a'*64 else None


class Incidents:
    async def check_ready(self): pass
    async def get(self,incident_id,revision=None): return {'revision':revision or 3}


def test_authenticated_observations_have_etag_strict_schema_and_no_write_route():
    async def run():
        reader=Reader();app=create_app(Settings(api_token=TOKEN),repository=Incidents(),action_reader=reader)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api') as client:
            path='/v1/actions/'+'a'*64
            assert (await client.get(path)).status_code==401
            client.headers['Authorization']='Bearer '+TOKEN
            result=await client.get(path)
            assert result.status_code==200 and result.headers['etag']=='"1"'
            validate_payload('action-observation.schema.json',result.json())
            assert 'idempotency_key' not in result.text and 'dispatch_authority' not in result.text
            assert (await client.get(path,headers={'If-None-Match':'"1"'})).status_code==304
            assert (await client.post(path,json={'approved':True})).status_code==405
            assert (await client.post('/v1/incidents/INC-fixture/actions',json={'image':'forged'})).status_code in (404,405)
            assert (await client.get('/v1/actions/'+'c'*64)).status_code==404
            assert (await client.get('/v1/actions/invalid')).status_code==422
            assert (await client.get('/ready/actions')).json()['dispatch_enabled'] is False
            assert (await client.post('/v1/risk',json={})).status_code==422
    asyncio.run(run())


def test_incident_revision_bounds_actions_and_cursor_cannot_cross_scope():
    async def run():
        reader=Reader();app=create_app(Settings(api_token=TOKEN),repository=Incidents(),action_reader=reader)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api',
                                     headers={'Authorization':'Bearer '+TOKEN}) as client:
            path='/v1/incidents/INC-fixture/actions'
            old=await client.get(path,params={'revision':1})
            assert old.status_code==200 and old.json()['items']==[] and old.headers['etag']=='"1"'
            current=await client.get(path,params={'revision':3,'limit':2})
            validate_payload('action-list.schema.json',current.json())
            assert reader.args==('INC-fixture',3,{'limit':2,'after':None})
            cursor=base64.urlsafe_b64encode(json.dumps({'recorded_at':AT,'action_id':'a'*64,
                'scope':stable_id(['INC-fixture',2])}).encode()).decode().rstrip('=')
            assert decode_cursor(cursor,'INC-fixture',2)[1]=='a'*64
            assert (await client.get(path,params={'revision':3,'cursor':cursor})).status_code==422
            for limit in (0,26): assert (await client.get(path,params={'limit':limit})).status_code==422
    asyncio.run(run())


@pytest.mark.parametrize('mode',['unconfigured','unavailable','corrupt','production'])
def test_unavailable_or_unsafe_reader_fails_closed_without_leaking_dsn(mode,caplog):
    async def run():
        reader=Reader();reader.unavailable=mode=='unavailable';reader.corrupt=mode=='corrupt'
        app=create_app(Settings(environment='aws') if mode=='production' else Settings(api_token=TOKEN,environment='test'),
                       action_reader=None if mode=='unconfigured' else reader)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api',
                                     headers={'Authorization':'Bearer '+TOKEN}) as client:
            response=await client.get('/v1/actions/'+'a'*64)
            assert response.status_code==503 and 'SECRET' not in response.text and 'SECRET' not in caplog.text
            assert response.headers['cache-control']=='no-store'
            assert response.headers['x-request-id']==response.json()['request_id']
            assert (await client.get('/health')).status_code==200
    asyncio.run(run())


def test_action_projection_rejects_unsupported_data_and_does_not_expose_raw_facts():
    card=state();card['facts']['token']='SECRET'
    assert 'SECRET' not in json.dumps(observation(card))
    card['history'][0]['phase']='rolled_back'
    with pytest.raises(RuntimeError): observation(card)


def test_blocked_action_with_no_preflight_evidence_still_has_a_valid_observation():
    card=state();card['intent']['evidence']=None
    result=observation(card)
    assert result['before_image'] is None and result['known_good_image'] is None


def test_action_read_settings_hide_dsn_and_contract_is_advertised_in_openapi(monkeypatch):
    monkeypatch.setenv('SENTINELOPS_ACTION_READ_DATABASE_URL','postgresql://reader:SECRET@127.0.0.1/db')
    settings=Settings.from_env()
    assert settings.action_database_url and 'SECRET' not in repr(settings)
    schema=create_app(settings).openapi()['paths']['/v1/actions/{action_id}']['get']['responses']['200']['content']['application/json']['schema']
    assert schema['properties']['observation_version']['const']=='action-observation/v1'


def test_existing_positional_settings_keep_the_api_token_and_optional_dependency_order():
    settings=Settings('test',None,TOKEN,False,None,None)
    assert settings.api_token==TOKEN and settings.action_database_url is None
    async def run():
        app=create_app(settings,repository=Incidents(),action_reader=Reader())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api',
                                     headers={'Authorization':'Bearer '+TOKEN}) as client:
            assert (await client.get('/ready/incidents')).status_code==200
            assert (await client.get('/ready/actions')).status_code==200
    asyncio.run(run())


def test_quarantine_status_is_authenticated_read_only_and_keeps_its_own_etag():
    async def run():
        app=create_app(Settings(api_token=TOKEN),action_reader=Reader())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api') as client:
            path='/v1/actions/'+'a'*64+'/quarantine'
            assert (await client.get(path)).status_code==401
            client.headers['Authorization']='Bearer '+TOKEN
            response=await client.get(path)
            assert response.status_code==200 and response.json()['quarantine_state']=='held'
            validate_payload('quarantine-status.schema.json',response.json())
            assert (await client.get(path,headers={'If-None-Match':response.headers['etag']})).status_code==304
            assert (await client.post(path,json={'released':True})).status_code==405
            assert (await client.get('/v1/actions/'+'b'*64+'/quarantine')).status_code==404
            assert (await client.get('/v1/actions/invalid/quarantine')).status_code==422
    asyncio.run(run())


def test_missing_quarantine_feature_does_not_become_an_unlocked_healthy_success():
    async def run():
        reader=Reader();reader.corrupt=True
        app=create_app(Settings(api_token=TOKEN),action_reader=reader)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api',headers={'Authorization':'Bearer '+TOKEN}) as client:
            response=await client.get('/v1/actions/'+'a'*64+'/quarantine')
            assert response.status_code==503 and 'SECRET' not in response.text
            assert 'quarantine_state' not in response.json()
    asyncio.run(run())
