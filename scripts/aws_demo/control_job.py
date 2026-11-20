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
RESOLVER_GROUP = "control_action_resolver"
RESOLVER_LOGIN = "control_quarantine_resolver"
RELEASE_FUNCTION = "sentinel_remediation.release_quarantine(text,integer,text,text,text,jsonb)"
RUNTIME_ROLES = {"runtime": "control_writer", "action-runtime": "control_action_writer",
                 "action-read": "control_action_reader", "action-resolve": RESOLVER_LOGIN}
# The AWS demo driver reads incidents and the health ledger with a SELECT-only
# login; 005 granted the reader only sentinel_remediation. Idempotent, owner-scoped.
READER_GRANTS = (
    "GRANT USAGE ON SCHEMA sentinel_control TO control_action_reader",
    "GRANT SELECT ON sentinel_control.incidents, sentinel_control.incident_versions, "
    "sentinel_control.health_streams, sentinel_control.health_batches, sentinel_control.health_signals "
    "TO control_action_reader",
)
DEMO_LOGINS = tuple(RUNTIME_ROLES.values())


def role_attributes_sql(role):
    if role not in RUNTIME_ROLES.values():
        raise ValueError("unsupported runtime role")
    return ("SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) "
            f"FROM pg_roles WHERE rolname='{role}'")


def runtime_privileges_sql(role):
    if role == "control_writer":
        return """SELECT current_user='control_writer'
            AND has_table_privilege(current_user,'sentinel_control.incident_versions','INSERT')
            AND NOT has_table_privilege(current_user,'sentinel_control.incident_versions','UPDATE,DELETE,TRUNCATE')
            AND NOT has_schema_privilege(current_user,'sentinel_control','CREATE')
            AND NOT has_schema_privilege(current_user,'sentinel_remediation','USAGE')
            AND NOT pg_has_role(current_user,'control_owner','MEMBER')
            AND NOT pg_has_role(current_user,'control_action_writer','MEMBER')
            AND NOT pg_has_role(current_user,'control_action_resolver','MEMBER')
            AND NOT has_database_privilege(current_user,current_database(),'CREATE')"""
    if role == "control_action_reader":
        # Mirrors ActionReader.check_ready: no write, owner or quarantine-release capability.
        return """SELECT current_user='control_action_reader'
            AND NOT EXISTS (
                SELECT FROM (VALUES ('sentinel_control.incidents'),('sentinel_control.incident_versions'),
                    ('sentinel_control.health_streams'),('sentinel_control.health_batches'),
                    ('sentinel_control.health_signals'),('sentinel_remediation.intents'),
                    ('sentinel_remediation.states'),('sentinel_remediation.events'),
                    ('sentinel_remediation.targets'),('sentinel_remediation.dispatches')) AS t(name)
                WHERE NOT has_table_privilege(current_user,name,'SELECT'))
            AND NOT EXISTS (
                SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('sentinel_control','sentinel_remediation') AND c.relkind IN ('r','p','v')
                  AND (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                       OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')))
            AND NOT EXISTS (
                SELECT FROM pg_namespace WHERE has_schema_privilege(current_user,oid,'CREATE'))
            AND NOT pg_has_role(current_user,'control_owner','MEMBER')
            AND NOT pg_has_role(current_user,'control_writer','MEMBER')
            AND NOT pg_has_role(current_user,'control_action_writer','MEMBER')
            AND NOT pg_has_role(current_user,'control_action_resolver','MEMBER')
            AND NOT coalesce(has_function_privilege(current_user,
                to_regprocedure('sentinel_remediation.release_quarantine(text,integer,text,text,text,jsonb)'),
                'EXECUTE'),false)
            AND NOT has_database_privilege(current_user,current_database(),'CREATE')"""
    if role == RESOLVER_LOGIN:
        # Direct table DML is forbidden. The SECURITY DEFINER function is the one
        # reviewed mutation boundary, inherited through the NOLOGIN group only.
        return f"""SELECT current_user='{RESOLVER_LOGIN}'
            AND pg_has_role(current_user,'{RESOLVER_GROUP}','MEMBER')
            AND (SELECT count(*)=1 AND bool_and(parent.rolname='{RESOLVER_GROUP}'
                    AND membership.inherit_option AND NOT membership.set_option
                    AND NOT membership.admin_option)
                FROM pg_auth_members membership
                JOIN pg_roles child ON child.oid=membership.member
                JOIN pg_roles parent ON parent.oid=membership.roleid
                WHERE child.rolname=current_user)
            AND has_function_privilege(current_user,'{RELEASE_FUNCTION}','EXECUTE')
            AND NOT EXISTS (
                SELECT FROM pg_proc procedure JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
                WHERE namespace.nspname IN ('sentinel_control','sentinel_remediation')
                  AND procedure.oid<>to_regprocedure('{RELEASE_FUNCTION}')
                  AND has_function_privilege(current_user,procedure.oid,'EXECUTE'))
            AND NOT EXISTS (
                SELECT FROM pg_class relation JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
                WHERE namespace.nspname IN ('sentinel_control','sentinel_remediation')
                  AND relation.relkind IN ('r','p','v')
                  AND (has_table_privilege(current_user,relation.oid,
                          'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                       OR has_any_column_privilege(current_user,relation.oid,'INSERT,UPDATE,REFERENCES')))
            AND NOT EXISTS (
                SELECT FROM pg_namespace WHERE has_schema_privilege(current_user,oid,'CREATE'))
            AND NOT pg_has_role(current_user,'control_owner','MEMBER')
            AND NOT pg_has_role(current_user,'control_writer','MEMBER')
            AND NOT pg_has_role(current_user,'control_action_writer','MEMBER')
            AND NOT pg_has_role(current_user,'control_action_reader','MEMBER')
            AND NOT has_database_privilege(current_user,current_database(),'CREATE')"""
    if role != "control_action_writer":
        raise ValueError("unsupported runtime role")
    # Check each privilege separately: PostgreSQL's comma-list checks mean ANY,
    # so has_table_privilege(..., 'SELECT,INSERT') would miss a missing INSERT.
    return """SELECT current_user='control_action_writer'
        AND has_schema_privilege(current_user,'sentinel_control','USAGE')
        AND has_schema_privilege(current_user,'sentinel_remediation','USAGE')
        AND NOT EXISTS (
            SELECT FROM (VALUES ('intents'),('states'),('events'),('targets'),('dispatches')) AS t(name)
            WHERE NOT has_table_privilege(current_user,'sentinel_remediation.' || name,'SELECT')
               OR NOT has_table_privilege(current_user,'sentinel_remediation.' || name,'INSERT'))
        AND NOT EXISTS (
            SELECT FROM (VALUES ('states','revision'),('states','phase'),('states','facts'),
                ('states','updated_at'),('targets','active_action'),('targets','last_dispatch_at')) AS c(tbl,col)
            WHERE NOT has_column_privilege(current_user,'sentinel_remediation.' || tbl,col,'UPDATE'))
        AND NOT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_schema='sentinel_remediation'
              AND has_column_privilege(current_user,table_schema || '.' || table_name,column_name,'UPDATE')
              AND NOT (table_name='states' AND column_name IN ('revision','phase','facts','updated_at')
                    OR table_name='targets' AND column_name IN ('active_action','last_dispatch_at')))
        AND NOT EXISTS (
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='sentinel_remediation' AND c.relkind IN ('r','p','v')
              AND (has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE')
                   OR c.relname NOT IN ('intents','states','events','targets','dispatches')
                      AND has_any_column_privilege(current_user,c.oid,'INSERT')))
        AND NOT EXISTS (
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='sentinel_control' AND c.relkind IN ('r','p','v')
              AND (has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE')
                   OR has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE')))
        AND NOT EXISTS (
            SELECT FROM pg_namespace WHERE has_schema_privilege(current_user,oid,'CREATE'))
        AND NOT pg_has_role(current_user,'control_owner','MEMBER')
        AND NOT pg_has_role(current_user,'control_writer','MEMBER')
        AND NOT pg_has_role(current_user,'control_action_resolver','MEMBER')
        AND NOT has_database_privilege(current_user,current_database(),'CREATE')"""


def runtime_login_sql(role, password):
    role_attributes_sql(role)  # Restrict interpolation to the reviewed fixed role names.
    return (f"ALTER ROLE {role} LOGIN PASSWORD '{scram_verifier(password)}' "
            "VALID UNTIL '2026-11-25 00:00:00+00'")


def resolver_membership_sql():
    return f"""SELECT count(*)=1 AND bool_and(parent.rolname='{RESOLVER_GROUP}'
            AND membership.inherit_option AND NOT membership.set_option AND NOT membership.admin_option)
        FROM pg_auth_members membership
        JOIN pg_roles child ON child.oid=membership.member
        JOIN pg_roles parent ON parent.oid=membership.roleid
        WHERE child.rolname='{RESOLVER_LOGIN}'"""


def disable_statements(roles=DEMO_LOGINS):
    # Revoke every selected login before terminating sessions, including failure recovery.
    if not roles or not set(roles) <= set(DEMO_LOGINS):
        raise ValueError("unsupported runtime role")
    names = ",".join(f"'{role}'" for role in roles)
    statements = []
    for role in roles:
        if role == RESOLVER_LOGIN:
            # action-resolve creates this fixed login; general cleanup also works
            # safely when that optional action was never run.
            statements.append(f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN "
                              f"ALTER ROLE {role} NOLOGIN PASSWORD NULL; END IF; END $$")
        else:
            statements.append(f"ALTER ROLE {role} NOLOGIN PASSWORD NULL")
    return (*statements,
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE usename IN ({names}) AND pid<>pg_backend_pid()")


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
    if await conn.fetchval("SELECT rolcanlogin FROM pg_roles WHERE rolname='control_action_resolver'"):
        raise RuntimeError("resolver group must remain NOLOGIN")


async def run(action: str):
    import asyncpg

    if action not in ("bootstrap", *RUNTIME_ROLES, "disable", "disable-read", "disable-resolve"):
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
        elif action in RUNTIME_ROLES:
            role = RUNTIME_ROLES[action]
            await verify_schema(conn)
            if await conn.fetchval("SELECT clock_timestamp() >= $1", EXPIRES):
                raise RuntimeError("demo exception expired")
            password = Path("/run/control/runtime-password").read_text()
            if len(password) < 32 or not password.isascii() or not all(c.isalnum() or c in "-_" for c in password):
                raise ValueError("invalid generated password")
            if role == RESOLVER_LOGIN:
                exists = await conn.fetchval("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=$1)", role)
                if not exists:
                    await conn.execute(f"CREATE ROLE {RESOLVER_LOGIN} NOLOGIN INHERIT "
                                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
                membership_count = await conn.fetchval("""SELECT count(*) FROM pg_auth_members membership
                    JOIN pg_roles child ON child.oid=membership.member WHERE child.rolname=$1""", role)
                if membership_count:
                    if not await conn.fetchval(resolver_membership_sql()):
                        raise RuntimeError("resolver login has an unexpected membership")
                else:
                    await conn.execute(f"GRANT {RESOLVER_GROUP} TO {RESOLVER_LOGIN} "
                                       "WITH INHERIT TRUE, SET FALSE")
                if not await conn.fetchval(resolver_membership_sql()):
                    raise RuntimeError("resolver login membership differs")
            # RDS master is CREATEROLE, not superuser: PostgreSQL 16 rejects naming
            # SUPERUSER/REPLICATION/BYPASSRLS (even NO*) in ALTER ROLE. Migrations created the
            # role with those defaults off; verify them instead of re-asserting them.
            if not await conn.fetchval(role_attributes_sql(role)):
                raise RuntimeError("runtime role carries an elevated attribute")
            if role == "control_action_reader":
                # 001 leaves the master with SET-only owner membership; grants run as the owner.
                await conn.execute("SET ROLE control_owner")
                try:
                    for statement in READER_GRANTS:
                        await conn.execute(statement)
                finally:
                    await conn.execute("RESET ROLE")
            await conn.execute(runtime_login_sql(role, password))
            # Authenticate using the new password, verify the real TLS session and privilege boundary.
            runtime = await asyncpg.connect(user=role, password=password, **options)
            try:
                if (not await runtime.fetchval("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
                    or not await runtime.fetchval(runtime_privileges_sql(role))):
                    raise RuntimeError("runtime privilege verification failed")
            finally:
                await runtime.close()
        elif action in ("disable-read", "disable-resolve"):
            # Scoped failure recovery must not revoke live worker/controller logins.
            role = "control_action_reader" if action == "disable-read" else RESOLVER_LOGIN
            exists = await conn.fetchval("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=$1)", role)
            if exists:
                for statement in disable_statements((role,)):
                    await conn.execute(statement)
            if await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE usename=$1", role):
                raise RuntimeError("scoped runtime sessions remain; operator must verify termination")
        else:
            for statement in disable_statements():
                await conn.execute(statement)
            if await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE usename = ANY($1::text[])",
                                   list(DEMO_LOGINS)):
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
