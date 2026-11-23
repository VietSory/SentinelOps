"""Authenticated, read-only data for the existing charts and trace visualizers."""
import asyncio
from datetime import datetime, timezone
import json
import math
import re
import time

from fastapi import Depends, HTTPException, Query
import httpx

PROM='http://prometheus.sentinelops-b-actions.svc.cluster.local:9090'
TRACES='http://jaeger-grafana-compat.sentinelops-b-actions.svc.cluster.local:16687'
SERVICES=('checkout-service','payment-service','inventory-service')
RISK_STATES=('normal','watch','degrading','unavailable')
RISK_QUERIES=('sentinelops_risk_state == 1','sentinelops_risk_score','sentinelops_risk_episode_active',
              'sentinelops_risk_episode_started_unixtime','sentinelops_risk_last_scored_unixtime','sentinelops_risk_threshold')
QUERIES={'p95_ms':'sentinelops:http_latency_p95_ms','error_pct':'100 * sentinelops:http_error_ratio:ratio5m',
         'success_pct':'100 * sentinelops:http_success_ratio:ratio5m','rps':'sentinelops:http_request_rate:rate1m'}

async def read(client,origin,path,params=None):
    try:
        async with asyncio.timeout(12),client.stream('GET',origin+path,params=params) as response:
            if response.status_code==404:raise HTTPException(404,'Trace data is no longer retained')
            if response.status_code!=200:raise HTTPException(503,'Telemetry source unavailable')
            chunks=[];size=0
            async for chunk in response.aiter_bytes():
                size+=len(chunk)
                if size>4*1024*1024:raise HTTPException(502,'Telemetry response too large')
                chunks.append(chunk)
            data=json.loads(b''.join(chunks))
            if not isinstance(data,dict):raise HTTPException(502,'Invalid telemetry response')
            return data
    except (httpx.HTTPError,TimeoutError,ValueError):
        raise HTTPException(503,'Telemetry source unavailable') from None

def finite(value):
    try:
        number=float(value)
        return number if math.isfinite(number) else None
    except (ValueError,TypeError):return None

def install_routes(app,authorize_operator,environment='unknown',transport=None):
    @app.get('/v1/observability/overview',dependencies=[Depends(authorize_operator)])
    async def overview():
        now=int(time.time())
        async with httpx.AsyncClient(timeout=8,trust_env=False,follow_redirects=False,transport=transport) as client:
            async def metric(key,query):
                try:
                    body=await read(client,PROM,'/api/v1/query',{'query':query})
                    if body.get('status')!='success' or body['data']['resultType']!='vector':raise ValueError('invalid vector')
                    return key,{item['metric']['service']:finite(item['value'][1]) for item in body['data']['result'] if item['metric'].get('service') in SERVICES},None
                except (HTTPException,ValueError,KeyError,TypeError,AttributeError):return key,{},'unavailable'
            values=await asyncio.gather(*(metric(key,query) for key,query in QUERIES.items()))
            metrics={key:val for key,val,_ in values}
            async def history(key):
                try:
                    body=await read(client,PROM,'/api/v1/query_range',{'query':QUERIES[key],'start':now-86400,'end':now,'step':300})
                    if body.get('status')!='success' or body['data']['resultType']!='matrix':raise ValueError('invalid matrix')
                    return key,{item['metric']['service']:[[float(at),finite(value)] for at,value in item['values']] for item in body['data']['result'] if item['metric'].get('service') in SERVICES}
                except (HTTPException,ValueError,KeyError,TypeError,AttributeError):return key,{}
            series=dict(await asyncio.gather(history('p95_ms'),history('error_pct')))
        return {'schema_version':'live-observability/v1','as_of':datetime.now(timezone.utc).isoformat(),
                'environment':environment,'window_seconds':86400,'step_seconds':300,
                'services':[{'service':service,**{key:metrics[key].get(service) for key in QUERIES}} for service in SERVICES],
                'series':series,'unavailable':[key for key,_,error in values if error],
                'source':'Prometheus recording rules; rate/error/success windows are 1m/5m/5m'}

    @app.get('/v1/observability/risk',dependencies=[Depends(authorize_operator)])
    async def risk():
        """Early-warning state from the risk worker metrics; missing values stay null."""
        async with httpx.AsyncClient(timeout=8,trust_env=False,follow_redirects=False,transport=transport) as client:
            async def vector(query):
                try:
                    body=await read(client,PROM,'/api/v1/query',{'query':query})
                    if body.get('status')!='success' or body['data']['resultType']!='vector':raise ValueError('invalid vector')
                    return [item for item in body['data']['result'] if item['metric'].get('service') in SERVICES],False
                except (HTTPException,ValueError,KeyError,TypeError,AttributeError):return [],True
            keys=('state','score','active','started','scored','threshold')
            replies=await asyncio.gather(*(vector(q) for q in RISK_QUERIES))
        data=dict(zip(keys,(rows for rows,_ in replies)))
        services={s:{'state':None,'score':None,'episode_active':None,'episode_started_at':None,'last_scored_at':None,
                     'thresholds':{'watch':None,'degrading':None}} for s in SERVICES}
        def stamp(value):
            number=finite(value)
            return None if number is None else datetime.fromtimestamp(number,timezone.utc).isoformat().replace('+00:00','Z')
        for item in data['state']:
            if item['metric'].get('state') in RISK_STATES:services[item['metric']['service']]['state']=item['metric']['state']
        for item in data['score']:services[item['metric']['service']]['score']=finite(item['value'][1])
        for item in data['active']:
            value=finite(item['value'][1])
            services[item['metric']['service']]['episode_active']=None if value is None else value==1
        for item in data['started']:services[item['metric']['service']]['episode_started_at']=stamp(item['value'][1])
        for item in data['scored']:services[item['metric']['service']]['last_scored_at']=stamp(item['value'][1])
        for item in data['threshold']:
            if item['metric'].get('level') in ('watch','degrading'):
                services[item['metric']['service']]['thresholds'][item['metric']['level']]=finite(item['value'][1])
        return {'schema_version':'live-risk/v1','generated_at':datetime.now(timezone.utc).isoformat(),
                'services':services,'unavailable':any(error for _,error in replies),
                'source':'risk worker metrics (XGBoost forecast of an SLO breach within about 5 minutes); a forecast is not an incident'}

    @app.get('/v1/observability/risk/episodes',dependencies=[Depends(authorize_operator)])
    async def risk_episodes(service:str,start:int=Query(...,ge=0),end:int=Query(...,ge=0)):
        if service not in SERVICES or end<start or end-start>7*86400:raise HTTPException(422,'Invalid service or range')
        async with httpx.AsyncClient(timeout=8,trust_env=False,follow_redirects=False,transport=transport) as client:
            body=await read(client,PROM,'/api/v1/query_range',{'query':f'sentinelops_risk_episode_started_unixtime{{service="{service}"}}',
                                                               'start':start,'end':end,'step':60})
        try:
            if body.get('status')!='success' or body['data']['resultType']!='matrix':raise ValueError('invalid matrix')
            values=sorted({finite(value) for item in body['data']['result'] for _,value in item['values']}-{None})
        except (ValueError,KeyError,TypeError,AttributeError):raise HTTPException(503,'Telemetry source unavailable') from None
        return {'service':service,'episodes':[datetime.fromtimestamp(v,timezone.utc).isoformat().replace('+00:00','Z') for v in values]}

    @app.get('/v1/observability/traces/{trace_id}',dependencies=[Depends(authorize_operator)])
    async def trace(trace_id:str):
        if not re.fullmatch(r'(?:[0-9a-f]{16}|[0-9a-f]{32})',trace_id) or not int(trace_id,16):raise HTTPException(422,'Invalid trace ID')
        async with httpx.AsyncClient(timeout=8,trust_env=False,follow_redirects=False,transport=transport) as client:
            body=await read(client,TRACES,'/api/traces/'+trace_id)
        if not isinstance(body.get('data'),list) or len(body['data'])!=1 or body['data'][0].get('traceID','').zfill(32)!=trace_id.zfill(32):raise HTTPException(502,'Invalid trace response')
        return {'schema_version':'live-traces/v1','association':'recorded','items':body['data']}

    @app.get('/v1/observability/trace-search',dependencies=[Depends(authorize_operator)])
    async def search(service:str='checkout-service',start:int|None=Query(None,ge=0),end:int|None=Query(None,ge=0)):
        if service not in SERVICES:raise HTTPException(422,'Unsupported service')
        now=int(time.time()*1_000_000);last=end if end is not None else now;first=start if start is not None else last-900_000_000
        if not 0<=first<last or last-first>86_400_000_000 or last>now+60_000_000:raise HTTPException(422,'Invalid trace window')
        async with httpx.AsyncClient(timeout=8,trust_env=False,follow_redirects=False,transport=transport) as client:
            body=await read(client,TRACES,'/api/traces',{'service':service,'start':first,'end':last,'limit':10})
        if not isinstance(body.get('data'),list):raise HTTPException(502,'Invalid trace response')
        return {'schema_version':'live-traces/v1','association':'window' if start is not None else 'recent',
                'start':first,'end':last,'items':body['data'][:10]}
