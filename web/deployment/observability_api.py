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
