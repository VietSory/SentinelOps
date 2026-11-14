"""Read-only local PostgreSQL evidence, separate from HTTP model telemetry."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

import asyncpg

from sentinelops.contracts import validate_payload
from sentinelops.telemetry.core import stable_id, timestamp

S4_INJECTOR_APPLICATION_NAME = "sentinelops-s4-lock-injector"

SQL = """
SELECT clock_timestamp() AS measured_at,
       current_setting('max_connections')::integer AS max_connections,
       current_setting('superuser_reserved_connections')::integer
         + COALESCE(NULLIF(current_setting('reserved_connections', true), ''), '0')::integer AS reserved_connections,
       (SELECT count(*) FROM pg_stat_activity
        WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()) AS connections
"""

LOCK_WAITS_SQL = f"""
SELECT clock_timestamp() AS measured_at,
       pg_postmaster_start_time() AS postgres_started_at,
       (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()) AS database_oid,
       pg_has_role('pg_read_all_stats', 'USAGE') AND current_setting('track_activities') = 'on'
           AND count(*) FILTER (WHERE activity.state IS NULL OR activity.state = 'disabled') = 0
           AS stats_visible,
       count(locks.pid) AS waiting_clients,
       min(locks.waitstart) AS earliest_wait_start
FROM pg_catalog.pg_stat_activity AS activity
LEFT JOIN pg_catalog.pg_locks AS locks ON locks.pid = activity.pid AND NOT locks.granted
  AND activity.application_name IS DISTINCT FROM '{S4_INJECTOR_APPLICATION_NAME}'
WHERE activity.datname = current_database()
  AND activity.backend_type = 'client backend' AND activity.pid <> pg_backend_pid()
"""


class PostgresInfrastructureSource:
    def __init__(self, dsn: str):
        parsed = urlsplit(dsn)
        if parsed.scheme not in ("postgresql", "postgres"):
            raise ValueError("a PostgreSQL DSN is required")
        self._dsn = dsn
        host = parsed.hostname or "local-socket"
        if ":" in host:
            host = f"[{host}]"
        self.source_ref = f"postgres-stat://{host}:{parsed.port or 5432}"

    async def _fetch(self, query: str) -> asyncpg.Record:
        connection = await asyncpg.connect(self._dsn, timeout=5., server_settings={"default_transaction_read_only": "on"})
        try:
            return await connection.fetchrow(query, timeout=5.)
        finally:
            await connection.close()

    async def capture(self, *, environment: str = "local") -> dict:
        row = await self._fetch(SQL)
        usable = row["max_connections"] - row["reserved_connections"]
        if usable <= 0:
            raise ValueError("database has no measurable usable connection capacity")
        if row["measured_at"].tzinfo is None:
            raise ValueError("database observation timestamp must be timezone-aware")
        result = {
            "schema_version": "1.0", "component": "rds", "environment": environment,
            "observed_at": timestamp(row["measured_at"].astimezone(timezone.utc)),
            "available_at": timestamp(datetime.now(timezone.utc)),
            "measurement_scope": "cluster-client-connections-excluding-collector",
            "source_ref": self.source_ref,
            "metrics": {
                "db_connections_count": row["connections"], "max_connections": row["max_connections"],
                "reserved_connections": row["reserved_connections"],
                "db_connections_pct": row["connections"] / usable * 100,
            },
        }
        result["record_id"] = stable_id({key:value for key,value in result.items() if key != "available_at"})
        validate_payload("infrastructure-observation.schema.json", result)
        return result

    async def capture_lock_waits(self) -> dict:
        """Collect an operational raw snapshot scoped to the connected database."""
        row = await self._fetch(LOCK_WAITS_SQL)
        available = datetime.now(timezone.utc)
        if row["stats_visible"] is not True:
            raise ValueError("PostgreSQL lock wait observation requires complete activity visibility")
        observed, started, wait_start = row["measured_at"], row["postgres_started_at"], row["earliest_wait_start"]
        if (started.utcoffset() is None or observed.utcoffset() is None
                or not started <= observed <= available
                or (wait_start is not None and (wait_start.utcoffset() is None or not started <= wait_start <= observed))):
            raise ValueError("PostgreSQL lock wait observation has inconsistent source and receipt clocks")
        result = {
            "kind": "postgres-lock-wait-snapshot/v1", "source_ref": self.source_ref,
            "database_oid": row["database_oid"], "postgres_started_at": timestamp(started),
            "observed_at": timestamp(observed), "available_at": timestamp(available),
            "waiting_clients": row["waiting_clients"],
            "earliest_wait_start": timestamp(wait_start) if wait_start is not None else None,
        }
        result["record_id"] = stable_id({key: value for key, value in result.items() if key != "available_at"})
        return result
