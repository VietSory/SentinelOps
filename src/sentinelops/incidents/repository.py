"""PostgreSQL incident revisions and at-least-once transactional outbox."""
from __future__ import annotations

import json
from uuid import UUID,uuid4

import asyncpg

from sentinelops.incidents.domain import IncidentConflict, IncidentNotFound, IncidentWrite, check_transition
from sentinelops.telemetry.core import timestamp


def snapshot(row) -> dict:
    return {"schema_version":"1.0","incident_id":row["incident_id"],"revision":row["revision"],
            "recorded_at":timestamp(row["recorded_at"]),"payload":json.loads(row["payload"]),
            "context":json.loads(row["context"]),"actor":row["actor"],"reason":row["reason"]}


class IncidentRepository:
    def __init__(self,pool: asyncpg.Pool):
        self.pool = pool

    async def check_ready(self) -> None:
        async with self.pool.acquire() as conn:
            if await conn.fetchval("SELECT max(version) FROM sentinel_control.schema_migrations") != 1:
                raise RuntimeError("unsupported incident database migration version")
            unsafe = await conn.fetchval("""SELECT rolsuper OR rolcreaterole OR rolcreatedb
                OR has_schema_privilege('sentinel_control','CREATE')
                OR has_table_privilege('sentinel_control.incident_versions','UPDATE,DELETE,TRUNCATE')
                OR has_table_privilege('sentinel_control.incident_events','UPDATE,DELETE,TRUNCATE')
                OR pg_has_role(current_user,(SELECT nspowner FROM pg_namespace WHERE nspname='sentinel_control'),'MEMBER')
                FROM pg_roles WHERE rolname=current_user""")
            if unsafe:
                raise RuntimeError("incident runtime must use a nonowner role without administrative/history-mutation privileges")

    async def write(self,command: IncidentWrite) -> dict:
        # Freeze a caller-owned mutable dict before waiting for locks/network I/O.
        command = IncidentWrite(**json.loads(json.dumps(command.__dict__,allow_nan=False)))
        fingerprint = command.fingerprint()
        identifier = command.payload["incident_id"]
        try:
            async with self.pool.acquire() as conn, conn.transaction():
                if command.expected_revision == 0:
                    check_transition(None,command)
                    await conn.execute("""INSERT INTO sentinel_control.incidents(incident_id,current_revision,lifecycle_stage)
                        VALUES ($1,1,'OPEN') ON CONFLICT (incident_id) DO NOTHING""",identifier)
                current = await conn.fetchrow("SELECT * FROM sentinel_control.incidents WHERE incident_id=$1 FOR UPDATE",identifier)
                if current is None:
                    raise IncidentNotFound(identifier)
                prior = await conn.fetchrow("SELECT * FROM sentinel_control.incident_versions WHERE idempotency_key=$1",command.idempotency_key)
                if prior:
                    if prior["request_hash"] != fingerprint:
                        raise IncidentConflict("idempotency key was already used for different contents")
                    return snapshot(prior)
                previous = await conn.fetchrow("SELECT * FROM sentinel_control.incident_versions WHERE incident_id=$1 AND revision=$2",
                                               identifier,current["current_revision"])
                if previous:
                    if current["current_revision"] != command.expected_revision:
                        raise IncidentConflict("expected incident revision is stale")
                    check_transition(current["lifecycle_stage"],command,json.loads(previous["payload"]))
                    revision = current["current_revision"]+1
                else:
                    if command.expected_revision != 0:
                        raise IncidentConflict("incident has no matching initial revision")
                    revision = 1
                context = {**command.context,"lifecycle_stage":command.lifecycle_stage}
                row = await conn.fetchrow("""INSERT INTO sentinel_control.incident_versions
                    (incident_id,revision,idempotency_key,request_hash,payload,context,actor,reason)
                    VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8) RETURNING *""",
                    identifier,revision,command.idempotency_key,fingerprint,json.dumps(command.payload),json.dumps(context),command.actor,command.reason)
                await conn.execute("""UPDATE sentinel_control.incidents SET current_revision=$2,lifecycle_stage=$3,
                    updated_at=clock_timestamp() WHERE incident_id=$1""",identifier,revision,command.lifecycle_stage)
                event = uuid4()
                await conn.execute("""INSERT INTO sentinel_control.incident_events
                    (event_id,incident_id,revision,event_type,lifecycle_stage,actor,reason)
                    VALUES ($1,$2,$3,'incident.revised',$4,$5,$6)""",event,identifier,revision,command.lifecycle_stage,command.actor,command.reason)
                await conn.execute("""INSERT INTO sentinel_control.outbox(event_id,topic,body)
                    VALUES ($1,'incident.revised',$2::jsonb)""",event,json.dumps({"incident_id":identifier,"revision":revision,
                        "lifecycle_stage":command.lifecycle_stage,"request_hash":fingerprint}))
                return snapshot(row)
        except asyncpg.UniqueViolationError as exc:
            raise IncidentConflict("incident/idempotency identity conflicts with a concurrent write") from exc

    async def get(self,identifier: str,revision: int | None = None) -> dict:
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError("revision must be a positive integer")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT v.* FROM sentinel_control.incident_versions v
                JOIN sentinel_control.incidents i USING (incident_id)
                WHERE v.incident_id=$1 AND v.revision=COALESCE($2::bigint,i.current_revision)""",identifier,revision)
        if row is None:
            raise IncidentNotFound(identifier)
        return snapshot(row)

    async def history(self,identifier: str) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM sentinel_control.incident_events WHERE incident_id=$1 ORDER BY revision",identifier)
        return [{**dict(row),"event_id":str(row["event_id"]),"recorded_at":timestamp(row["recorded_at"])} for row in rows]

    async def claim_events(self,*,limit: int = 10,lease_seconds: int = 60) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(lease_seconds) is not int or not 1 <= lease_seconds <= 600:
            raise ValueError("invalid outbox claim bounds")
        token = uuid4()
        async with self.pool.acquire() as conn,conn.transaction():
            rows = await conn.fetch("""WITH candidates AS (
                SELECT event_id FROM sentinel_control.outbox
                WHERE completed_at IS NULL AND available_at<=clock_timestamp()
                    AND (lease_until IS NULL OR lease_until<clock_timestamp())
                ORDER BY available_at,event_id FOR UPDATE SKIP LOCKED LIMIT $1
                ) UPDATE sentinel_control.outbox o SET lease_token=$2,
                    lease_until=clock_timestamp()+($3*interval '1 second'),attempts=attempts+1
                FROM candidates c WHERE o.event_id=c.event_id RETURNING o.*""",limit,token,lease_seconds)
        return [{"event_id":str(row["event_id"]),"topic":row["topic"],"body":json.loads(row["body"]),
                 "lease_token":str(row["lease_token"]),"attempts":row["attempts"],"lease_until":timestamp(row["lease_until"])} for row in rows]

    async def acknowledge(self,event_id: str,lease_token: str) -> None:
        async with self.pool.acquire() as conn:
            status = await conn.execute("""UPDATE sentinel_control.outbox SET completed_at=clock_timestamp()
                WHERE event_id=$1 AND lease_token=$2 AND completed_at IS NULL AND lease_until>=clock_timestamp()""",
                UUID(event_id),UUID(lease_token))
        if status != "UPDATE 1":
            raise IncidentConflict("outbox lease expired, was reclaimed or already acknowledged")
