"""Mounted into the control-plane Job. Never print credentials or DB exceptions."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import ssl
import sys

MIGRATIONS = (
    "001-deployment-roles.sql", "002-incident-store.sql", "003-health-worker.sql",
    "004-incident-consumer.sql", "005-remediation.sql", "006-quarantine-review.sql",
)
EXPIRES = datetime(2026, 11, 25, tzinfo=timezone.utc)


def scram_verifier(password: str, salt: bytes | None = None) -> str:
    """Send a SCRAM verifier in ALTER ROLE, never its plaintext password."""
    salt = secrets.token_bytes(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, 4096)
    client = hmac.digest(salted, b"Client Key", "sha256")
    stored = hashlib.sha256(client).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")
    b64 = lambda value: base64.b64encode(value).decode("ascii")
    return f"SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}"


def migration_sql(filename: str, source: str) -> str:
    """Scope mixed admin/owner statements for a non-superuser RDS master.

    Keep the source migrations untouched; all additional role changes occur inside
    their original transaction and cannot persist if execution is interrupted.
    """
    if filename == "001-deployment-roles.sql":
        marker = "CREATE SCHEMA sentinel_control AUTHORIZATION control_owner;"
        if source.count(marker) != 1:
            raise ValueError("review changed 001 before bootstrap")
        return source.replace(marker, marker + "\nSET LOCAL ROLE control_owner;")
    if filename.startswith(("005-", "006-")):
        lines = source.splitlines()
        if lines.count("BEGIN;") != 1 or lines.count("COMMIT;") != 1 or lines[-1] != "COMMIT;":
            raise ValueError("review changed migration transaction boundaries")
        lines[lines.index("BEGIN;")] += "\nGRANT control_owner TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;"
        lines[-1] = ("RESET ROLE;\nGRANT control_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;\nCOMMIT;")
        return "\n".join(lines) + "\n"
    return source


async def verify_schema(conn):
    # 001 intentionally leaves the master with SET-only owner membership.
    await conn.execute("SET ROLE control_owner")
    try:
        core = [r[0] for r in await conn.fetch("SELECT version FROM sentinel_control.schema_migrations ORDER BY version")]
        actions = [r[0] for r in await conn.fetch("SELECT version FROM sentinel_remediation.schema_migrations ORDER BY version")]
    finally:
        await conn.execute("RESET ROLE")
    if core != [1, 2, 3] or actions != [1, 2]:
        raise RuntimeError("unexpected migration ledger")
    if await conn.fetchval("SELECT pg_has_role('control_writer','control_owner','MEMBER')"):
        raise RuntimeError("runtime is an owner member")


async def run(action: str):
    import asyncpg

    if action not in ("bootstrap", "runtime", "disable"):
        raise ValueError("unsupported action")
    master = json.loads(Path("/run/control/master.json").read_text())
    if master["username"] != "control_bootstrap" or not master["password"]:
        raise ValueError("wrong managed master identity")
    tls = ssl.create_default_context(cafile="/etc/sentinelops/tls/ca-bundle.crt")
    options = dict(host=os.environ["CONTROL_HOST"], port=5432, database="sentinelops_control",
                   ssl=tls, timeout=15, command_timeout=90,
                   server_settings={"application_name": "sentinelops-demo-control-bootstrap"})
    conn = await asyncpg.connect(user=master["username"], password=master["password"], **options)
    try:
        if not await conn.fetchval("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"):
            raise RuntimeError("TLS required")
        if not await conn.fetchval("SELECT pg_try_advisory_lock(1936028276, 20261118)"):
            raise RuntimeError("another control bootstrap is running")
        if action == "bootstrap":
            for filename in MIGRATIONS:
                # Each source migration owns its BEGIN/COMMIT. 001 rejects replay.
                await conn.execute(migration_sql(filename, (Path("/bootstrap") / filename).read_text()))
                print(json.dumps({"migration": filename, "status": "APPLIED"}), flush=True)
            await verify_schema(conn)
            if await conn.fetchval("SELECT rolcanlogin FROM pg_roles WHERE rolname='control_writer'"):
                raise RuntimeError("bootstrap must leave runtime NOLOGIN")
        elif action == "runtime":
            await verify_schema(conn)
            if await conn.fetchval("SELECT clock_timestamp() >= $1", EXPIRES):
                raise RuntimeError("demo exception expired")
            password = Path("/run/control/runtime-password").read_text()
            if len(password) < 32 or not password.isascii() or not all(c.isalnum() or c in "-_" for c in password):
                raise ValueError("invalid generated password")
            verifier = scram_verifier(password)
            # RDS master is CREATEROLE, not superuser: PostgreSQL 16 rejects naming
            # SUPERUSER/REPLICATION/BYPASSRLS (even NO*) in ALTER ROLE. 001 created the
            # role with those defaults off; verify them instead of re-asserting them.
            if not await conn.fetchval("""SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole
                    OR rolreplication OR rolbypassrls) FROM pg_roles WHERE rolname='control_writer'"""):
                raise RuntimeError("control_writer carries an elevated attribute")
            await conn.execute("ALTER ROLE control_writer LOGIN PASSWORD '" + verifier + "' "
                               "VALID UNTIL '2026-11-25 00:00:00+00'")
            # Authenticate using the new password, verify the real TLS session and privilege boundary.
            runtime = await asyncpg.connect(user="control_writer", password=password, **options)
            try:
                safe = await runtime.fetchval("""SELECT current_user='control_writer'
                    AND (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())
                    AND has_table_privilege(current_user,'sentinel_control.incident_versions','INSERT')
                    AND NOT has_table_privilege(current_user,'sentinel_control.incident_versions','UPDATE,DELETE,TRUNCATE')
                    AND NOT has_schema_privilege(current_user,'sentinel_control','CREATE')
                    AND NOT has_schema_privilege(current_user,'sentinel_remediation','USAGE')
                    AND NOT pg_has_role(current_user,'control_owner','MEMBER')
                    AND NOT pg_has_role(current_user,'control_action_writer','MEMBER')
                    AND NOT pg_has_role(current_user,'control_action_resolver','MEMBER')
                    AND NOT has_database_privilege(current_user,current_database(),'CREATE')""")
                if not safe:
                    raise RuntimeError("runtime privilege verification failed")
            finally:
                await runtime.close()
        else:
            await conn.execute("ALTER ROLE control_writer NOLOGIN PASSWORD NULL")
            await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                               "WHERE usename='control_writer' AND pid<>pg_backend_pid()")
            if await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE usename='control_writer'"):
                raise RuntimeError("runtime sessions remain; operator must verify termination")
        print(json.dumps({"action": action, "status": "PASS"}), flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(run(sys.argv[1]))
    except Exception:
        # PostgreSQL exception details may include SQL; keep cluster logs credential-free.
        print("control job FAILED; inspect migration progress and non-secret connectivity/privileges", file=sys.stderr)
        sys.exit(1)
