"""Read-only local PostgreSQL evidence, separate from HTTP model telemetry."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

import asyncpg

from sentinelops.contracts import validate_payload
from sentinelops.telemetry.core import stable_id, timestamp

SQL = """
SELECT clock_timestamp() AS measured_at,
       current_setting('max_connections')::integer AS max_connections,
       current_setting('superuser_reserved_connections')::integer
         + COALESCE(NULLIF(current_setting('reserved_connections', true), ''), '0')::integer AS reserved_connections,
       (SELECT count(*) FROM pg_stat_activity
        WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()) AS connections
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

    async def capture(self, *, environment: str = "local") -> dict:
        connection = await asyncpg.connect(self._dsn, timeout=5., server_settings={"default_transaction_read_only": "on"})
        try:
            row = await connection.fetchrow(SQL, timeout=5.)
        finally:
            await connection.close()
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
