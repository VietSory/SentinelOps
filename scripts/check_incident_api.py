"""Launch the real API and exercise HTTP persistence against local PostgreSQL."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx


async def exercise(client: httpx.AsyncClient,token: str,*,restart_database: bool):
    auth={"authorization":"Bearer "+token}
    assert (await client.get("/health")).status_code==200
    assert (await client.get("/ready/incidents")).status_code==401
    assert (await client.get("/ready/incidents",headers=auth)).status_code==200
    assert (await client.get("/ready")).status_code==503  # Real model is still unavailable.
    feature=json.loads(Path("samples/feature-window.sample.json").read_text())
    assert (await client.post("/v1/detect",json=feature)).status_code==503
    assert (await client.post("/v1/incidents/demo")).status_code==404
    payload=json.loads(Path("samples/incident-evidence-record.sample.json").read_text())
    prefix="INC-API-"+uuid4().hex
    component="fixture-component-"+uuid4().hex
    payload["incident_id"]=prefix
    payload["affected_services"].append(component)
    key=uuid4().hex
    headers={**auth,"if-match":'"0"',"idempotency-key":key}
    assert (await client.post("/v1/incidents",json=payload,headers=auth)).status_code==428
    created=await client.post("/v1/incidents",json=payload,headers=headers)
    assert created.status_code==201 and created.json()==payload and created.headers["etag"]=='"1"'
    assert (await client.post("/v1/incidents",json=payload,headers=headers)).json()==payload
    conflict=await client.post("/v1/incidents",json={**payload,"severity":"critical"},headers=headers)
    assert conflict.status_code==409
    assert (await client.get("/v1/incidents/"+prefix,headers={**auth,"if-none-match":'"1"'})).status_code==304
    envelope=(await client.get("/v1/incidents/"+prefix+"/context",headers=auth)).json()
    assert envelope["revision"]==1 and envelope["payload"]==payload
    assert envelope["context"]["availability"]=="UNKNOWN"
    assert envelope["context"]["trigger_sources"][0]["source"]=="manual"
    context={k:v for k,v in envelope["context"].items() if k!="lifecycle_stage"}
    update={"schema_version":"1.0","payload":payload,"context":context,"lifecycle_stage":"RCA_READY","reason":"synthetic API persistence fixture"}
    update_headers={**auth,"if-match":'"1"',"idempotency-key":uuid4().hex}
    updated=await client.post("/v1/incidents/"+prefix+"/revisions",json=update,headers=update_headers)
    assert updated.status_code==200 and updated.headers["etag"]=='"2"'
    assert (await client.post("/v1/incidents/"+prefix+"/revisions",json=update,headers=update_headers)).headers["etag"]=='"2"'
    stale=await client.post("/v1/incidents/"+prefix+"/revisions",json=update,headers={**update_headers,"idempotency-key":uuid4().hex})
    assert stale.status_code==409
    bad=await client.post("/v1/incidents/"+prefix+"/revisions",json={**update,"context":[]},headers=update_headers)
    assert bad.status_code==422
    future=await client.post("/v1/incidents/"+prefix+"/revisions",json={**update,"payload":{**payload,"state":"recovering"},"lifecycle_stage":"VERIFYING"},headers={**update_headers,"if-match":'"2"'})
    assert future.status_code==503
    historical=await client.get("/v1/incidents/"+prefix+"/context",params={"revision":1},headers=auth)
    assert historical.json()["revision"]==1 and len(historical.json()["timeline"]["events"])==1
    page=(await client.get("/v1/incidents/"+prefix+"/context",params={"limit":1},headers=auth)).json()
    assert page["timeline"]["next_after_revision"]==1
    page2=(await client.get("/v1/incidents/"+prefix+"/context",params={"after_revision":1},headers=auth)).json()
    assert [event["revision"] for event in page2["timeline"]["events"]]==[2]
    for index in range(2):
        item={**payload,"incident_id":prefix+"-"+str(index)}
        response=await client.post("/v1/incidents",json=item,headers={**headers,"idempotency-key":uuid4().hex})
        assert response.status_code==201
    listed=(await client.get("/v1/incidents",params={"service":component,"limit":2},headers=auth)).json()
    assert len(listed["items"])==2 and listed["next_cursor"]
    listed2=(await client.get("/v1/incidents",params={"service":component,"limit":2,"cursor":listed["next_cursor"]},headers=auth)).json()
    assert len(listed2["items"])==1 and listed2["next_cursor"] is None
    assert len({item["incident_id"] for item in listed["items"]+listed2["items"]})==3
    assert (await client.get("/v1/incidents",params={"service":"changed","cursor":listed["next_cursor"]},headers=auth)).status_code==422
    assert (await client.get("/v1/incidents",params={"cursor":"!invalid!"},headers=auth)).status_code==422
    assert (await client.get("/v1/incidents",params={"limit":101},headers=auth)).status_code==422
    missing=await client.get("/v1/incidents/does-not-exist",headers=auth)
    assert missing.status_code==404 and missing.json()["request_id"]==missing.headers["x-request-id"]
    async def chunks():
        yield b"x"*600_000
        yield b"x"*600_000
    oversized=await client.post("/v1/incidents",content=chunks(),headers=auth)
    assert oversized.status_code==413
    if restart_database:
        command=["docker","compose","-p","sentinelops-control","-f","docker-compose.control.yml"]
        try:
            await asyncio.to_thread(subprocess.run,[*command,"stop","-t","5","control-postgres"],check=True)
            unavailable=await client.get("/ready/incidents",headers=auth)
            assert unavailable.status_code==503 and "postgresql" not in unavailable.text
            assert (await client.get("/health")).status_code==200
        finally:
            await asyncio.to_thread(subprocess.run,[*command,"up","-d","--wait","--wait-timeout","60"],check=True)
        assert (await client.get("/ready/incidents",headers=auth)).status_code==200
        assert (await client.get("/v1/incidents/"+prefix,headers=auth)).json()==payload
    return {"status":"PASS","transport":"real-HTTP-uvicorn","database":"real-PostgreSQL-nonowner-role",
        "fixture_only":True,"incident_id":prefix,"current_revision":2,"created_incidents":3,
        "checks":["protected-read-write","no-model-503","no-default-demo","persisted-legacy-IER-body",
            "idempotent-import","changed-key-conflict","ETag-conditional-read","CAS-update","stale-write-conflict",
            "historical-context","bounded-timeline","filtered-keyset-list","cursor-filter-binding",
            "body-stream-limit","unintegrated-lifecycle-denied"]+(["DB-outage-503","liveness-during-outage","pool-reconnect-after-restart"] if restart_database else [])}


async def main(output: Path,restart_database: bool):
    dsn=os.getenv("CONTROL_DATABASE_URL","")
    parsed=urlparse(dsn)
    if parsed.hostname not in ("127.0.0.1","localhost") or parsed.path!="/sentinelops_control":
        raise ValueError("CONTROL_DATABASE_URL must select the dedicated local control sandbox")
    output.parent.mkdir(parents=True,exist_ok=True)
    token=secrets.token_urlsafe(32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1",0));port=listener.getsockname()[1]
    env={**os.environ,"SENTINELOPS_API_TOKEN":token,"SENTINELOPS_ENV":"test","SENTINELOPS_ENABLE_DEMO":"false"}
    with (output.parent/"api-server.log").open("w") as logs:
        process=subprocess.Popen([sys.executable,"-m","uvicorn","sentinelops.main:app","--host","127.0.0.1","--port",str(port)],env=env,stdout=logs,stderr=logs)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",timeout=15,trust_env=False) as client:
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if process.poll() is not None:
                        raise RuntimeError("API process exited; inspect the local validation log")
                    try:
                        if (await client.get("/health")).status_code==200: break
                    except httpx.HTTPError: pass
                    await asyncio.sleep(.2)
                else: raise RuntimeError("API startup timed out")
                report=await exercise(client,token,restart_database=restart_database)
                output.write_text(json.dumps(report,indent=2))
                print("INCIDENT API NETWORK ACCEPTANCE: PASS "+str(output),flush=True)
        finally:
            process.terminate()
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=Path("artifacts/incidents/api-acceptance.json"))
    parser.add_argument("--restart-database",action="store_true")
    args=parser.parse_args()
    asyncio.run(main(args.output,args.restart_database))
