"""Real PostgreSQL acceptance checks against an exclusive local control sandbox."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import UUID,uuid4

import asyncpg

from sentinelops.incidents.domain import IncidentConflict, IncidentWrite
from sentinelops.incidents.repository import IncidentRepository


async def verify_report(repository,report):
    for item in report["incidents"]:
        current = await repository.get(item["incident_id"])
        assert current["revision"] == item["revision"]
        assert current["payload"] == item["payload"]
        assert len(await repository.history(item["incident_id"])) == item["revision"]


async def check(dsn: str,output: Path,verify: Path | None):
    parsed = urlparse(dsn)
    if parsed.hostname not in ("127.0.0.1","localhost") or parsed.path != "/sentinelops_control":
        raise ValueError("acceptance script requires the dedicated local sentinelops_control database")
    pool = await asyncpg.create_pool(dsn,min_size=1,max_size=12,command_timeout=10)
    repository = IncidentRepository(pool)
    try:
        await repository.check_ready()
        async with pool.acquire() as conn:
            role = await conn.fetchrow("SELECT current_user AS name,rolsuper,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=current_user")
            assert role["name"]=="control_writer" and not any(role[key] for key in ("rolsuper","rolcreaterole","rolcreatedb"))
        if verify:
            report = json.loads(verify.read_text())
            await verify_report(repository,report)
            print("INCIDENT STORE AFTER RESTART: PASS",flush=True)
            return
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM sentinel_control.outbox WHERE completed_at IS NULL") == 0,"acceptance requires an exclusive database with no pending production work"
        payload = json.loads(Path("samples/incident-evidence-record.sample.json").read_text())
        prefix = "INC-STORE-"+uuid4().hex
        payload["incident_id"] = prefix
        context = {"availability":"ELIGIBLE","recovery_source":None,"source_refs":["fixture://postgres-acceptance/source"],
            "config_refs":["config://incident/local-v1"],"trigger_sources":[{"source":"manual","signal_id":uuid4().hex,
                "emitted_at":payload["detected_at"],"source_ref":"fixture://postgres-acceptance/trigger"}]}
        initial = IncidentWrite(payload,"OPEN",0,uuid4().hex,"local-store-validator","synthetic storage acceptance; not RCA evidence",context)
        retries = await asyncio.gather(*(repository.write(initial) for _ in range(8)))
        assert all(item==retries[0] for item in retries) and retries[0]["revision"]==1
        newer = replace(initial,expected_revision=1,lifecycle_stage="RCA_READY",idempotency_key=uuid4().hex)
        competing = replace(newer,idempotency_key=uuid4().hex,reason="competing revision")
        results = await asyncio.gather(repository.write(newer),repository.write(competing),return_exceptions=True)
        assert sum(isinstance(item,IncidentConflict) for item in results)==1
        assert (await repository.get(prefix))["revision"]==2
        assert await repository.write(initial)==retries[0]  # Retry returns its original revision, not current.
        try:
            await repository.write(replace(initial,reason="changed retry contents"))
            raise AssertionError("idempotency identity accepted different contents")
        except IncidentConflict:
            pass
        history = await repository.history(prefix)
        assert [item["revision"] for item in history]==[1,2]
        # Fail after version/pointer writes by colliding on a durable event UUID.
        with patch("sentinelops.incidents.repository.uuid4",return_value=UUID(history[0]["event_id"])):
            try:
                await repository.write(replace(newer,expected_revision=2,idempotency_key=uuid4().hex))
                raise AssertionError("injected event collision was accepted")
            except IncidentConflict:
                pass
        assert (await repository.get(prefix))["revision"]==2
        assert len(await repository.history(prefix))==2
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM sentinel_control.incident_versions WHERE incident_id=$1",prefix)==2
            for sql in ("UPDATE sentinel_control.incident_versions SET reason='tampered' WHERE incident_id=$1",
                        "DELETE FROM sentinel_control.incident_events WHERE incident_id=$1"):
                try:
                    await conn.execute(sql,prefix)
                    raise AssertionError("application role mutated append-only history")
                except asyncpg.InsufficientPrivilegeError:
                    pass
            try:
                await conn.execute("CREATE TABLE sentinel_control.application_ddl_forbidden(id int)")
                raise AssertionError("application role obtained schema CREATE")
            except asyncpg.InsufficientPrivilegeError:
                pass
        claims = await asyncio.gather(repository.claim_events(limit=1,lease_seconds=1),repository.claim_events(limit=1,lease_seconds=1))
        first,second = claims[0][0],claims[1][0]
        assert first["event_id"]!=second["event_id"]
        await repository.acknowledge(second["event_id"],second["lease_token"])
        await asyncio.sleep(1.1)
        reclaimed = (await repository.claim_events(limit=1))[0]
        assert reclaimed["event_id"]==first["event_id"] and reclaimed["attempts"]==2
        try:
            await repository.acknowledge(first["event_id"],first["lease_token"])
            raise AssertionError("expired claimant acknowledged another worker's lease")
        except IncidentConflict:
            pass
        await repository.acknowledge(reclaimed["event_id"],reclaimed["lease_token"])
        assert await repository.claim_events()==[]
        stored = await repository.get(prefix)
        report = {"status":"PASS","fixture_only":True,"checks":["concurrent-idempotent-create","revision-CAS",
            "changed-idempotency-conflict","atomic-outbox-rollback","append-only-role","schema-DDL-denied",
            "concurrent-disjoint-claims","lease-reclaim","expired-ack-rejected"],
            "incidents":[{"incident_id":prefix,"revision":stored["revision"],"payload":stored["payload"]}]}
        await verify_report(repository,report)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2))
        print("INCIDENT STORE ACCEPTANCE: PASS "+str(output),flush=True)
    finally:
        await pool.close()


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=Path("artifacts/incidents/store-acceptance.json"))
    parser.add_argument("--verify-report",type=Path)
    args = parser.parse_args()
    dsn = os.environ.get("CONTROL_DATABASE_URL")
    if not dsn:
        raise SystemExit("CONTROL_DATABASE_URL is required; never use commerce/bootstrap credentials")
    asyncio.run(check(dsn,args.output,args.verify_report))
