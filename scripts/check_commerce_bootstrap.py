"""Check deployment commerce SQL in a new isolated PostgreSQL software fixture.

Never connects to an existing database, publishes ports, mounts host storage,
pulls an image, calls AWS or seeds a deployment inventory catalog.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]


def check() -> dict:
    image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}',
                                     'postgres:16-alpine'], text=True).strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise RuntimeError('PostgreSQL software-check image must already exist locally')
    name = 'sentinelops-commerce-check-' + uuid4().hex
    subprocess.run(['docker', 'run', '-d', '--rm', '--pull', 'never', '--name', name,
        '--label', 'sentinelops.scope=commerce-bootstrap-software-check',
        '--network', 'none', '--read-only', '--user', 'postgres', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--pids-limit', '128', '--memory', '512m', '--cpus', '1',
        '--tmpfs', '/var/lib/postgresql/data:rw,mode=1777',
        '--tmpfs', '/var/run/postgresql:rw,mode=1777', '--tmpfs', '/tmp:rw,mode=1777',
        '--env', 'PGDATA=/var/lib/postgresql/data/pgdata', '--env', 'POSTGRES_DB=sentinelops_commerce',
        '--env', 'POSTGRES_USER=bootstrap_fixture',
        '--env', 'POSTGRES_PASSWORD=disposable-software-fixture-only', image],
        check=True, capture_output=True, text=True, timeout=30)

    def sql(statement, *, database='sentinelops_commerce', denied=None, user='bootstrap_fixture'):
        result = subprocess.run(['docker', 'exec', '-i', name, 'psql', '-X', '-U', user,
            '-d', database, '-v', 'ON_ERROR_STOP=1', '-qAt'], input=statement,
            capture_output=True, text=True, timeout=30)
        if denied is not None:
            if result.returncode == 0 or denied not in result.stderr:
                raise RuntimeError('isolated commerce SQL did not reject the expected operation: ' + result.stderr)
        elif result.returncode:
            raise RuntimeError('isolated commerce SQL failed: ' + result.stderr)
        return result.stdout.strip()

    try:
        for _ in range(60):
            result = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1',
                '-U', 'bootstrap_fixture', '-d', 'sentinelops_commerce'], capture_output=True, timeout=5)
            if result.returncode == 0:  # Exclude the entrypoint's socket-only init server.
                break
            time.sleep(1)
        else:
            raise RuntimeError('isolated commerce PostgreSQL fixture did not become ready')
        # Exercise bootstrap as a database owner with CREATEROLE, not a real
        # PostgreSQL superuser. Actual RDS principal capabilities remain a
        # deployment check; this fixture does not impersonate AWS authorization.
        sql('CREATE ROLE commerce_migration_fixture LOGIN CREATEROLE NOSUPERUSER NOCREATEDB; '
            'ALTER DATABASE sentinelops_commerce OWNER TO commerce_migration_fixture')
        bootstrap = (PROJECT / 'infra/commerce/001-deployment.sql').read_text()
        def migrate(**kwargs):
            return sql(bootstrap, user='commerce_migration_fixture', **kwargs)

        migrate(database='postgres', denied='requires PostgreSQL 16 database sentinelops_commerce')
        assert sql("SELECT count(*) FROM pg_roles WHERE rolname IN ('commerce_owner','commerce_runtime')") == '0'
        # An unrelated existing object is preserved and blocks all bootstrap mutations.
        sql('CREATE TABLE public.keep_fixture (id integer); INSERT INTO public.keep_fixture VALUES (7)')
        migrate(denied='requires a fresh database and absent commerce roles')
        assert sql('SELECT id FROM public.keep_fixture') == '7'
        assert sql("SELECT count(*) FROM pg_roles WHERE rolname IN ('commerce_owner','commerce_runtime')") == '0'
        sql('DROP TABLE public.keep_fixture')
        # Even an empty target must not adopt a pre-existing cluster-wide group.
        sql('CREATE ROLE commerce_runtime NOLOGIN')
        migrate(denied='requires a fresh database and absent commerce roles')
        assert sql("SELECT count(*) FROM pg_roles WHERE rolname='commerce_owner'") == '0'
        sql('DROP ROLE commerce_runtime')
        migrate()
        assert sql("SELECT count(*) FROM pg_roles r JOIN pg_authid a USING(oid) "
                   "WHERE r.rolname IN ('commerce_owner','commerce_runtime') AND NOT r.rolcanlogin "
                   "AND NOT r.rolsuper AND NOT r.rolcreaterole AND NOT r.rolcreatedb "
                   "AND NOT r.rolreplication AND NOT r.rolbypassrls AND a.rolpassword IS NULL") == '2'
        assert sql('SELECT (SELECT count(*) FROM public.inventory) + (SELECT count(*) FROM public.payments)') == '0'
        sql("CREATE ROLE commerce_login_fixture LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; "
            "GRANT commerce_runtime TO commerce_login_fixture WITH INHERIT TRUE, SET FALSE; "
            "INSERT INTO public.inventory VALUES ('software-fixture-sku','Software fixture',5)")
        runtime = 'SET SESSION AUTHORIZATION commerce_login_fixture; '
        assert sql(runtime + "SELECT has_database_privilege(current_database(),'CONNECT') AND "
            "has_schema_privilege('public','USAGE') AND "
            "NOT has_schema_privilege('public','CREATE') AND "
            "NOT has_database_privilege(current_database(),'CREATE,TEMP') AND "
            "NOT pg_has_role('commerce_owner','MEMBER')") == 't'
        assert sql(runtime + "SELECT sku || ':' || name || ':' || quantity::text "
                   "FROM inventory WHERE sku='software-fixture-sku'") == 'software-fixture-sku:Software fixture:5'
        sql(runtime + "INSERT INTO payments(payment_id,order_id,amount,currency,status,created_at) "
            "VALUES ('PAY-SOFTWARE-FIXTURE','software-order',12.50,'USD','approved',now())")
        assert sql("SELECT order_id || ':' || amount::text FROM public.payments "
                   "WHERE payment_id='PAY-SOFTWARE-FIXTURE'") == 'software-order:12.50'
        for operation, table in [
            ('SELECT * FROM payments', 'payments'),
            ("INSERT INTO inventory VALUES ('unwanted','Unwanted',1)", 'inventory'),
            ('UPDATE inventory SET quantity=0', 'inventory'),
            ('DELETE FROM inventory', 'inventory'), ('TRUNCATE inventory', 'inventory'),
            ("UPDATE payments SET status='changed'", 'payments'),
            ('DELETE FROM payments', 'payments'), ('TRUNCATE payments', 'payments'),
        ]:
            sql(runtime + operation, denied='permission denied for table ' + table)
        sql(runtime + 'CREATE TABLE public.unwanted (id integer)', denied='permission denied for schema public')
        sql(runtime + 'SET ROLE commerce_owner', denied='permission denied to set role')
        sql(runtime + 'ALTER TABLE public.payments ADD COLUMN unwanted integer', denied='must be owner of table payments')
        migrate(denied='requires a fresh database and absent commerce roles')
        assert sql('SELECT (SELECT count(*) FROM public.inventory) + (SELECT count(*) FROM public.payments)') == '2'
        assert sql("SELECT count(*) FROM pg_namespace WHERE nspname IN ('sentinel_control','sentinel_remediation')") == '0'
        return {'status': 'PASS', 'scope': 'isolated-commerce-bootstrap-software-only',
                'bootstrap_checked_as_non_superuser_database_owner': True,
                'password_free_nologin_groups': True, 'inventory_read_and_payment_insert': True,
                'runtime_ddl_and_unneeded_dml_rejected': True,
                'wrong_database_and_existing_objects_and_roles_rejected': True,
                'deployment_catalog_seeded': False, 'control_or_action_schema_created': False,
                'network': 'none', 'host_ports': False, 'aws_calls': False}
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True, timeout=30)


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
