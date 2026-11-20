"""Real chart/trace transport keeps existing operator auth and read-only limits."""
import importlib.util
from pathlib import Path
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

spec=importlib.util.spec_from_file_location('live_observability',Path(__file__).resolve().parents[1]/'deployment/observability_api.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
TRACE='a'*32

def client(upstream):
    app=FastAPI()
    async def authorize(authorization:str|None=Header(None)):
        if authorization!='Bearer fixture-read':raise HTTPException(401,'Operator required')
    api.install_routes(app,authorize,'dev',httpx.MockTransport(upstream))
    return TestClient(app)

def test_auth_cannot_be_bypassed_and_no_writes_exist():
    def upstream(request):pytest.fail('Unauthenticated request reached telemetry')
    with client(upstream) as c:
        for route in ['/v1/observability/overview','/v1/observability/traces/'+TRACE,'/v1/observability/trace-search']:
            assert c.get(route).status_code==401
            assert c.post(route,headers={'Authorization':'Bearer fixture-read'}).status_code==405

def test_fixed_prom_queries_partial_unknowns_and_raw_history():
    seen=[]
    def upstream(request):
        seen.append(request)
        assert request.url.host=='prometheus.sentinelops-b-actions.svc.cluster.local'
        assert request.method=='GET' and 'authorization' not in request.headers
        assert request.url.params['query'] in api.QUERIES.values()
        if request.url.params['query']==api.QUERIES['rps']:return httpx.Response(503)
        if request.url.path.endswith('query_range'):
            assert request.url.params['step']=='300'
            return httpx.Response(200,json={'status':'success','data':{'resultType':'matrix','result':[
                {'metric':{'service':'payment-service'},'values':[[100,'950'],[200,'NaN']]}]}})
        return httpx.Response(200,json={'status':'success','data':{'resultType':'vector','result':[
            {'metric':{'service':'payment-service'},'value':[100,'950']},
            {'metric':{'service':'unrelated'},'value':[100,'99999']}]}})
    with client(upstream) as c:
        result=c.get('/v1/observability/overview?url=http://169.254.169.254',headers={'Authorization':'Bearer fixture-read'}).json()
        assert result['environment']=='dev' and result['window_seconds']==86400
        assert result['services'][1]['p95_ms']==950 and result['services'][1]['rps'] is None
        assert result['services'][0]['p95_ms'] is None
        assert result['series']['p95_ms']['payment-service']==[[100,950],[200,None]]
        assert result['unavailable']==['rps'] and len(seen)==6

def test_trace_identity_and_search_are_fixed_and_bounded():
    seen=[]
    def upstream(request):
        seen.append(request)
        assert request.url.host=='jaeger-grafana-compat.sentinelops-b-actions.svc.cluster.local'
        assert 'authorization' not in request.headers
        return httpx.Response(200,json={'data':[{'traceID':TRACE,'spans':[],'processes':{}}]})
    with client(upstream) as c:
        headers={'Authorization':'Bearer fixture-read'}
        assert c.get('/v1/observability/traces/'+TRACE,headers=headers).json()['association']=='recorded'
        result=c.get('/v1/observability/trace-search',headers=headers)
        assert result.json()['association']=='recent'
        assert seen[-1].url.params['service']=='checkout-service' and seen[-1].url.params['limit']=='10'
        count=len(seen)
        for path in ['/v1/observability/traces/'+'0'*32,'/v1/observability/traces/not-an-id',
                     '/v1/observability/trace-search?service=http://evil',
                     '/v1/observability/trace-search?start=1&end=999999999999999999',
                     '/v1/observability/trace-search?start=5&end=1']:
            assert c.get(path,headers=headers).status_code==422
        assert len(seen)==count

def test_wrong_trace_and_unretained_trace_are_not_a_success():
    with client(lambda r:httpx.Response(200,json={'data':[{'traceID':'b'*32}]})) as c:
        assert c.get('/v1/observability/traces/'+TRACE,headers={'Authorization':'Bearer fixture-read'}).status_code==502
    with client(lambda r:httpx.Response(404)) as c:
        assert c.get('/v1/observability/traces/'+TRACE,headers={'Authorization':'Bearer fixture-read'}).status_code==404

def test_time_window_is_preserved_in_trace_search():
    now=int(time.time()*1_000_000)
    def upstream(request):
        assert request.url.params['start']==str(now-60000000)
        assert request.url.params['end']==str(now)
        return httpx.Response(200,json={'data':[]})
    with client(upstream) as c:
        result=c.get(f'/v1/observability/trace-search?start={now-60000000}&end={now}',headers={'Authorization':'Bearer fixture-read'})
        assert result.status_code==200 and result.json()['association']=='window' and result.json()['items']==[]
