"""One serialized continuous accepted-health to durable-IER iteration."""
from __future__ import annotations

from contextlib import asynccontextmanager
import time

import asyncpg

from sentinelops.telemetry.core import stable_id
from sentinelops.workers.incident_journal import IncidentJournal

_UNUSABLE_SESSION = (asyncpg.InternalClientError, asyncpg.AdminShutdownError, asyncpg.CrashShutdownError)


class _Session:
    """All journal/repository writes use the connection owning the session lock.

    A lost lock connection cannot leave an old worker writing via another pool
    connection. Source reads use the original reader pool without holding a tx.
    """
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        if self.connection.is_closed():
            raise ConnectionError("incident consumer lock connection was lost")
        yield self.connection


class IncidentWorker:
    def __init__(self, journal: IncidentJournal, collector, *, connect):
        if not callable(connect):
            raise ValueError("incident worker requires an explicit dedicated nonpooled writer connection factory")
        self.journal, self.collector = journal, collector
        self.connect = connect
        key = int(stable_id({"component": "continuous-incident", "consumer_id": journal.consumer_id})[:16], 16)
        self.lock_id = key if key < 2**63 else key - 2**64

    async def tick(self) -> dict:
        started = time.monotonic()
        try:
            connection = await self.connect()
        except _UNUSABLE_SESSION as exc:
            raise ConnectionError("incident consumer PostgreSQL session was lost during connect") from exc
        # A pool may detach/reuse its holder immediately after server disconnect,
        # while an old iteration is still awaiting source I/O. Never carry that
        # proxy across external waits. Only an owned raw connection is supported.
        # ConnectionMeta.__instancecheck__ also accepts asyncpg pool proxies.
        # Check real inheritance so that compatibility behaviour cannot bypass
        # the nonpooled ownership requirement.
        if not issubclass(type(connection), asyncpg.Connection):
            raise ValueError("incident lock owner must be a dedicated nonpooled PostgreSQL connection")
        try:
            if not await connection.fetchval("SELECT pg_try_advisory_lock($1)", self.lock_id):
                return {"status": "BUSY", "consumer_id": self.journal.consumer_id}
            scoped = IncidentJournal(_Session(connection), self.journal.source, self.journal.store,
                self.journal.topology, self.journal.rca_policy, self.journal.identity["evidence_profile"],
                model_registry=self.journal.model_registry)
            events = []
            # Catch up the accepted source before considering recovery. A bounded
            # backlog iteration returns progress rather than closing on an old view.
            for _ in range(64):
                step = await scoped.ingest_next()
                if step["status"] == "WAIT":
                    break
                events.append(step)
            else:
                return {"status": "PROGRESS", "consumer_id": scoped.consumer_id, "events": events,
                        "processing_ms": round((time.monotonic()-started)*1000, 3)}
            published = await scoped.publish_one()
            if published: events.append(published)
            enriched = await scoped.enrich_one(self.collector)
            if enriched: events.append(enriched)
            projected = await scoped.project_one()
            if projected: events.append(projected)
            published = await scoped.publish_one()
            if published: events.append(published)
            return {"status": "PROGRESS" if events else "WAIT", "consumer_id": scoped.consumer_id,
                    "events": events, "processing_ms": round((time.monotonic()-started)*1000, 3)}
        except _UNUSABLE_SESSION as exc:
            if not connection.is_closed():
                connection.terminate()
            raise ConnectionError("incident consumer PostgreSQL session was lost") from exc
        except (asyncpg.InterfaceError, asyncpg.PostgresConnectionError) as exc:
            if connection.is_closed():
                raise ConnectionError("incident consumer lock connection was lost") from exc
            raise
        finally:
            # Closing the dedicated session releases its advisory lock without
            # a separate query on a connection that may already be failing.
            if not connection.is_closed():
                try:
                    await connection.close(timeout=5.)
                except _UNUSABLE_SESSION as exc:
                    if not connection.is_closed():
                        connection.terminate()
                    raise ConnectionError("incident consumer PostgreSQL session was lost during close") from exc
