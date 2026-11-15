"""Real PostgreSQL software fixture; no host database, ports, mounts or AWS calls."""
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
    name = 'sentinelops-bootstrap-check-' + uuid4().hex
    subprocess.run(['docker', 'run', '-d', '--rm', '--pull', 'never', '--name', name,
        '--label', 'sentinelops.scope=deployment-bootstrap-software-check',
        '--network', 'none', '--read-only', '--user', 'postgres', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--pids-limit', '128', '--memory', '512m', '--cpus', '1',
        '--tmpfs', '/var/lib/postgresql/data:rw,mode=1777',
        '--tmpfs', '/var/run/postgresql:rw,mode=1777', '--tmpfs', '/tmp:rw,mode=1777',
        '--env', 'PGDATA=/var/lib/postgresql/data/pgdata', '--env', 'POSTGRES_DB=sentinelops_control',
        '--env', 'POSTGRES_USER=bootstrap_fixture',
        '--env', 'POSTGRES_PASSWORD=disposable-software-fixture-only', image],
        check=True, capture_output=True, text=True, timeout=30)

    def sql(statement, *, database='sentinelops_control', success=True):
        result = subprocess.run(['docker', 'exec', '-i', name, 'psql', '-X', '-U', 'bootstrap_fixture',
            '-d', database, '-v', 'ON_ERROR_STOP=1', '-qAt'], input=statement,
            capture_output=True, text=True, timeout=30)
        if success and result.returncode:
            raise RuntimeError('isolated bootstrap SQL failed: ' + result.stderr)
        if not success and result.returncode == 0:
            raise RuntimeError('isolated bootstrap failed to reject unsafe replay/target')
        return result.stdout.strip()

    try:
        for _ in range(60):
            result = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1',
                '-U', 'bootstrap_fixture', '-d', 'sentinelops_control'], capture_output=True, timeout=5)
            # TCP excludes the entrypoint's temporary socket-only init server.
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('isolated PostgreSQL fixture did not become ready')
        bootstrap = (PROJECT / 'infra/control/001-deployment-roles.sql').read_text()
        sql(bootstrap, database='postgres', success=False)
        assert sql("SELECT count(*) FROM pg_roles WHERE rolname IN ('control_owner','control_writer')") == '0'
        sql(bootstrap)
        for migration in ['002-incident-store.sql', '003-health-worker.sql', '004-incident-consumer.sql']:
            sql((PROJECT / 'infra/control' / migration).read_text())
        assert sql('SELECT max(version) FROM sentinel_control.schema_migrations') == '3'
        assert sql("SELECT count(*) FROM pg_roles r JOIN pg_authid a USING(oid) "
                   "WHERE r.rolname IN ('control_owner','control_writer') AND NOT r.rolcanlogin "
                   "AND NOT r.rolsuper AND NOT r.rolcreaterole AND NOT r.rolcreatedb "
                   "AND NOT r.rolreplication AND NOT r.rolbypassrls AND a.rolpassword IS NULL") == '2'
        # This synthetic login tests membership only, with no login password.
        sql('CREATE ROLE deployment_runtime_fixture LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; '
            'GRANT control_writer TO deployment_runtime_fixture WITH INHERIT TRUE, SET FALSE;')
        assert sql("SET SESSION AUTHORIZATION deployment_runtime_fixture; SELECT "
            "has_database_privilege(current_database(),'CONNECT') AND "
            "has_schema_privilege('sentinel_control','USAGE') AND "
            "has_table_privilege('sentinel_control.incident_versions','SELECT') AND "
            "has_table_privilege('sentinel_control.incident_versions','INSERT') AND "
            "NOT has_table_privilege('sentinel_control.incident_versions','UPDATE,DELETE,TRUNCATE') AND "
            "NOT has_table_privilege('sentinel_control.incident_events','UPDATE,DELETE,TRUNCATE') AND "
            "NOT has_schema_privilege('sentinel_control','CREATE') AND "
            "NOT pg_has_role('control_owner','MEMBER') AND "
            "NOT has_database_privilege(current_database(),'CREATE')") == 't'
        sql('SET SESSION AUTHORIZATION deployment_runtime_fixture; '
            'CREATE TABLE sentinel_control.unwanted_table (id integer)', success=False)
        sql('SET SESSION AUTHORIZATION deployment_runtime_fixture; SET ROLE control_owner', success=False)
        for operation in ['UPDATE sentinel_control.incident_versions SET actor=actor WHERE false',
                          'DELETE FROM sentinel_control.incident_events WHERE false',
                          'TRUNCATE sentinel_control.incident_events CASCADE']:
            sql('SET SESSION AUTHORIZATION deployment_runtime_fixture; ' + operation, success=False)
        sql(bootstrap, success=False)
        assert sql('SELECT max(version) FROM sentinel_control.schema_migrations') == '3'
        assert sql("SELECT count(*) FROM pg_namespace WHERE nspname='sentinel_remediation'") == '0'
        return {'status': 'PASS', 'scope': 'isolated-deployment-bootstrap-software-only',
                'core_migration_version': 3, 'runtime_groups_have_no_password_or_login': True,
                'wrong_database_rejected': True, 'existing_installation_replay_rejected': True,
                'nonowner_runtime_privileges_checked': True, 'action_schema_created': False,
                'network': 'none', 'host_ports': False, 'aws_calls': False}
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True, timeout=30)


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
