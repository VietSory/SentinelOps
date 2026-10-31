"""One real local B workload through the existing workers, DB and read API.

Integration evidence only: not training data, predictive labels, online RCA
accuracy or remediation acceptance. Never writes an IER through the HTTP API.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4

import asyncpg
import httpx

from scripts import lane_b
from sentinelops.contracts import validate_payload

URLS = {'checkout-service':'http://127.0.0.1:18001/v1/checkout',
        'payment-service':'http://127.0.0.1:18002/v1/payments/charge',
        'inventory-service':'http://127.0.0.1:18003/v1/inventory/sku-001'}
BODY = {'sku':'sku-001','quantity':1,'amount':10.0,'currency':'USD'}


def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


async def run():
    lane_b.validate_config(json.loads(subprocess.check_output(lane_b.compose('config','--format','json'),
        env=lane_b.docker_environment(),text=True)))
    lane_b.require_owned_containers()
    environment = lane_b.runtime_environment()
    root = lane_b.ROOT/('live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8])
    root.mkdir(parents=True,exist_ok=False)
    stop = asyncio.Event()
    for name in (signal.SIGINT,signal.SIGTERM): asyncio.get_running_loop().add_signal_handler(name,stop.set)
    metadata = {'verification_version':'lane-b-live/v1','started_at':now(),'status':'RUNNING',
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=lane_b.PROJECT,text=True).strip(),
        'loaded_workload_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'compose_project':lane_b.NAME,'protocol':{'healthy_seconds':180,'latency_seconds':360,'recovery_seconds':300,
        'entry_rps':1,'payment_delay_ms':1200},'phases':[],'training_evidence':False,'diagnostic_accuracy_claim':False}
    print(json.dumps({'status':'STARTING','output':str(root)}),flush=True)
    active = set()
    async with httpx.AsyncClient(timeout=8,trust_env=False) as client:
        protected = {'Authorization':'Bearer '+environment['SENTINELOPS_API_TOKEN']}
        response = await client.get('http://127.0.0.1:18000/ready/incidents',headers=protected); response.raise_for_status()
        connection = await asyncpg.connect(environment['CONTROL_DATABASE_URL'])
        try:
            consumers = await connection.fetch("SELECT consumer_id FROM sentinel_control.incident_consumers WHERE identity->'evidence_profile'->>'compose_project'=$1",lane_b.NAME)
            if len(consumers) != 1: raise ValueError('start exactly one native B consumer before this workload')
            consumer = consumers[0]['consumer_id']
            before = {row['incident_id'] for row in await connection.fetch(
                'SELECT incident_id FROM sentinel_control.incident_jobs WHERE consumer_id=$1',consumer)}
            original = (await client.get('http://127.0.0.1:18002/internal/faults')).json()
            if original.get('mode') != 'healthy': raise ValueError('B Payment is not healthy; preserve the existing fault for investigation')
            with (root/'requests.jsonl').open('x') as ledger:
                async def request(service, phase):
                    trace = uuid4().hex; correlation = 'b-ui-'+uuid4().hex
                    headers = {'traceparent':'00-'+trace+'-'+uuid4().hex[:16]+'-01','x-correlation-id':correlation}
                    started, clock = now(), time.monotonic()
                    try:
                        if service == 'inventory-service': result = await client.get(URLS[service],headers=headers)
                        else: result = await client.post(URLS[service],headers=headers,json=BODY)
                        status = result.status_code
                    except httpx.HTTPError: status = 'transport_error'
                    ledger.write(json.dumps({'entry_service':service,'phase':phase,'trace_id':trace,'correlation_id':correlation,
                        'started_at':started,'completed_at':now(),'duration_ms':round((time.monotonic()-clock)*1000,3),'status':status})+'\n')
                    ledger.flush()

                async def load(phase, seconds):
                    record = {'phase':phase,'started_at':now()}; metadata['phases'].append(record)
                    (root/'run.json').write_text(json.dumps(metadata,indent=2)+'\n')
                    print(json.dumps({'phase':phase,'started_at':record['started_at'],'seconds':seconds}),flush=True)
                    end = time.monotonic()+seconds
                    while time.monotonic()<end and not stop.is_set():
                        if len(active)>20: raise RuntimeError('B workload queue saturated')
                        for service in URLS:
                            task = asyncio.create_task(request(service,phase)); active.add(task); task.add_done_callback(active.discard)
                        try: await asyncio.wait_for(stop.wait(),timeout=1)
                        except asyncio.TimeoutError: pass
                    if active: await asyncio.gather(*tuple(active))
                    record['ended_at'] = now()
                    if stop.is_set(): raise RuntimeError('B workload interrupted; observations retained')

                try:
                    await load('healthy',180)
                    response = await client.post('http://127.0.0.1:18002/internal/faults',json={'mode':'latency','delay_ms':1200}); response.raise_for_status()
                    await load('payment-latency',360)
                finally:
                    if active: await asyncio.gather(*tuple(active),return_exceptions=True)
                    response = await client.post('http://127.0.0.1:18002/internal/faults',json=original); response.raise_for_status()
                    metadata['payment_fault_restored_at'] = now()
                await load('recovery',300)
            rows = await connection.fetch('SELECT j.incident_id, count(v.revision) AS revisions FROM sentinel_control.incident_jobs j '
                'JOIN sentinel_control.incident_versions v USING(incident_id) JOIN sentinel_control.incident_job_evidence e USING(job_id) '
                'WHERE j.consumer_id=$1 GROUP BY j.incident_id',consumer)
            measured = []
            for row in rows:
                if row['incident_id'] in before: continue
                response = await client.get('http://127.0.0.1:18000/v1/incidents/'+row['incident_id']+'/context',headers=protected)
                response.raise_for_status(); envelope = response.json(); validate_payload('incident-envelope.schema.json',envelope)
                if not any(t['source']=='slo' for t in envelope['context']['trigger_sources']): raise ValueError('native incident lacks its accepted SLO trigger')
                measured.append({'incident_id':row['incident_id'],'revisions':row['revisions'],'api_revision':envelope['revision'],
                    'availability':envelope['context']['availability'],'lifecycle':envelope['context']['lifecycle_stage']})
            if not measured or any(item['revisions']<2 for item in measured): raise ValueError('no new enriched source-backed B incident reached the API')
            metadata.update(status='PASS',ended_at=now(),consumer_id=consumer,incidents=measured,
                model_dependency=(await client.get('http://127.0.0.1:18000/ready')).json()['dependencies']['model'])
        except BaseException as exc:
            metadata.update(status='FAILED',ended_at=now(),error_type=type(exc).__name__)
            raise
        finally:
            (root/'run.json').write_text(json.dumps(metadata,indent=2)+'\n')
            await connection.close()
    print(json.dumps(metadata,indent=2),flush=True)


if __name__ == '__main__': asyncio.run(run())
