"""Offline behavioral checks; optional disposable PostgreSQL software fixture.

Run in Python 3.11 on a copied repository. No AWS or kubectl invocation.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import control
import control_job
import upload_model


class OfflineChecks(unittest.TestCase):
    def args(self, **overrides):
        args = dict(action="bootstrap", host="control.example.ap-southeast-1.rds.amazonaws.com",
                    image="123456789012.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/control-plane@sha256:" + "a" * 64,
                    namespace="sentinelops-b-actions", context="offline", ca_config="control-rds-ca",
                    region="ap-southeast-1", master_secret_arn="arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:master",
                    runtime_secret_arn="arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:runtime", consumer="worker")
        return SimpleNamespace(**dict(args, **overrides))

    def test_offline_render_has_no_tools_or_credentials(self):
        args = self.args()
        with patch("subprocess.run", side_effect=AssertionError("external tool forbidden")):
            job = control.render(args)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(pod["containers"][0]["command"][-1], "bootstrap")
        self.assertNotIn("001-local-roles.sql", control_job.MIGRATIONS)

    def test_upload_mismatch_fails_before_any_external_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.tar.gz"
            path.write_bytes(b"test archive bytes")
            args = SimpleNamespace(bundle=path, release="de38fc6b", expected_sha256="0" * 64,
                                   bucket="test-artifacts", execute=True)
            with patch("subprocess.run", side_effect=AssertionError("external tool forbidden")):
                with self.assertRaisesRegex(ValueError, "checksum differs"):
                    upload_model.upload(args)
                args.expected_sha256 = upload_model.sha256(path)
                args.execute = False
                self.assertEqual(upload_model.upload(args)["status"], "LOCAL_CHECKSUM_PASS_NO_UPLOAD")

    def test_upload_requires_exact_version_and_downloaded_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.tar.gz"
            path.write_bytes(b"approved archive bytes")
            args = SimpleNamespace(bundle=path, release="de38fc6b", expected_sha256=upload_model.sha256(path),
                                   bucket="test-artifacts", account_id="123456789012", execute=True)
            checksum = control.base64.b64encode(bytes.fromhex(args.expected_sha256)).decode()
            def fake_aws(_args, *operation):
                if operation[0] == "sts":
                    return {"Account": args.account_id}
                verb = operation[1]
                if verb == "get-bucket-versioning":
                    return {"Status": "Enabled"}
                if verb == "get-bucket-encryption":
                    return {"ServerSideEncryptionConfiguration": {"Rules": [
                        {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}}]}}
                if verb == "put-object":
                    self.assertIn("--if-none-match", operation)
                    return {"VersionId": "v1", "ChecksumSHA256": checksum, "ServerSideEncryption": "aws:kms"}
                self.assertEqual(operation[operation.index("--version-id") + 1], "v1")
                if verb == "head-object":
                    return {"ChecksumSHA256": checksum, "ContentLength": path.stat().st_size}
                Path(operation[-1]).write_bytes(b"corrupted download")
                return {}
            with patch.object(upload_model, "aws", side_effect=fake_aws):
                with self.assertRaisesRegex(RuntimeError, "downloaded bytes differ"):
                    upload_model.upload(args)

    def test_sync_selects_credential_free_worker_endpoint(self):
        args = self.args(action="sync-runtime")
        value = {"worker-database-url": "postgresql://control:5432/sentinelops_control?sslmode=verify-full",
                 "database-url": "postgresql://control_writer:memory-only@control:5432/sentinelops_control?sslmode=verify-full",
                 "password": secrets.token_urlsafe(36)}
        documents = []
        with patch.object(control, "read_runtime", return_value=value), \
             patch.object(control, "kubectl", return_value=""), \
             patch.object(control, "apply", side_effect=lambda _, doc, **kw: documents.append(doc)), \
             contextlib.redirect_stdout(io.StringIO()):
            control.sync(args)
            args.consumer = "api"
            control.sync(args)
        decode = lambda doc: control.base64.b64decode(doc["data"]["database-url"]).decode()
        self.assertEqual(decode(documents[0]), value["worker-database-url"])
        self.assertEqual(decode(documents[1]), value["database-url"])

    def test_runtime_publication_failure_disables_login(self):
        args = self.args(action="runtime")
        actions, requests = [], []
        password = secrets.token_urlsafe(36)
        def fake_aws(_args, *operation, data=None):
            requests.append((operation, data))
            if operation[0] == "sts":
                return json.dumps({"Account": "123456789012"})
            if operation[1] == "describe-secret":
                return json.dumps({"Name": control.RUNTIME_NAME})
            if operation[1] == "get-secret-value":
                return json.dumps({"SecretString": json.dumps({"username": "control_bootstrap", "password": password})})
            raise RuntimeError("publication unavailable")
        with patch.object(control, "aws", side_effect=fake_aws), \
             patch.object(control, "kubectl", return_value=""), patch.object(control, "apply"), \
             patch.object(control, "run_job", side_effect=lambda *a, **kw: actions.append(kw.get("action", args.action))), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                control.execute(args)
        self.assertEqual(actions, ["runtime", "disable"])
        self.assertNotIn(password, repr([op for op, _ in requests]))

    def action_execution(self, *, publication_fails=False, job_fails=False, existing=False, **overrides):
        args = self.args(action="action-runtime", runtime_secret_arn=None, **overrides)
        actions, documents, requests = [], [], []
        password = "RAM-only:/@?#%+" + secrets.token_urlsafe(36)
        def fake_aws(_args, *operation, data=None):
            requests.append(operation)
            if operation == ("sts", "get-caller-identity"):
                return json.dumps({"Account": "123456789012"})
            self.assertEqual(operation, ("secretsmanager", "get-secret-value", "--secret-id", args.master_secret_arn))
            return json.dumps({"SecretString": json.dumps({"username": "control_bootstrap", "password": "master-in-RAM"})})
        def fake_apply(_args, doc, **kwargs):
            self.assertTrue(kwargs.get("create"))
            documents.append(doc)
            if doc["metadata"]["name"] == "remediation-runtime":
                self.assertEqual(actions, ["action-runtime"])
                if publication_fails:
                    raise RuntimeError("publication unavailable")
        def fake_job(*args, **kwargs):
            action = kwargs.get("action", "action-runtime")
            actions.append(action)
            if job_fails and action == "action-runtime":
                raise RuntimeError("job verification failed")
        output = io.StringIO()
        with patch.object(control, "aws", side_effect=fake_aws), \
             patch.object(control, "kubectl", return_value="secret/remediation-runtime" if existing else ""), \
             patch.object(control, "apply", side_effect=fake_apply), \
             patch.object(control.secrets, "token_urlsafe", return_value=password), \
             patch.object(control, "run_job", side_effect=fake_job), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if publication_fails or job_fails or existing or args.namespace != "sentinelops-b-actions":
                with self.assertRaises((RuntimeError, ValueError)):
                    control.execute(args)
            else:
                control.execute(args)
        self.assertNotIn(password, output.getvalue())
        self.assertNotIn(password, repr(requests))
        return args, actions, documents, password

    def test_action_runtime_publishes_only_k8s_encoded_dsn_after_job(self):
        args, actions, documents, password = self.action_execution()
        self.assertEqual(actions, ["action-runtime"])
        doc = documents[-1]
        self.assertEqual(doc["metadata"], {"name": "remediation-runtime", "namespace": "sentinelops-b-actions"})
        self.assertEqual(list(doc["data"]), ["action-database-url"])
        dsn = control.base64.b64decode(doc["data"]["action-database-url"]).decode()
        url = urlsplit(dsn)
        self.assertEqual(unquote(url.username), "control_action_writer")
        self.assertEqual(unquote(url.password), password)
        self.assertNotIn(password, dsn)
        self.assertEqual((url.hostname, url.port, url.path), (args.host, 5432, "/sentinelops_control"))
        self.assertEqual(parse_qs(url.query), {"sslmode": ["verify-full"]})

    def test_action_runtime_publication_failure_disables_both_logins(self):
        _, actions, _, _ = self.action_execution(publication_fails=True)
        self.assertEqual(actions, ["action-runtime", "disable"])

    def test_action_runtime_job_failure_revokes_without_publishing(self):
        _, actions, documents, _ = self.action_execution(job_fails=True)
        self.assertEqual(actions, ["action-runtime", "disable"])
        self.assertNotIn("remediation-runtime", [doc["metadata"]["name"] for doc in documents])

    def test_action_read_publishes_reader_dsn_and_failure_disables_only_reader(self):
        for publication_fails in (False, True):
            with self.subTest(publication_fails=publication_fails):
                args = self.args(action="action-read", runtime_secret_arn=None)
                actions, documents = [], []
                def fake_aws(_args, *operation, data=None):
                    if operation == ("sts", "get-caller-identity"):
                        return json.dumps({"Account": "123456789012"})
                    return json.dumps({"SecretString": json.dumps({"username": "control_bootstrap", "password": "m"})})
                def fake_apply(_args, doc, **kwargs):
                    documents.append(doc)
                    if doc["metadata"]["name"] == "remediation-read" and publication_fails:
                        raise RuntimeError("publication unavailable")
                def fake_job(*_a, **kwargs):
                    actions.append(kwargs.get("action", "action-read"))
                with patch.object(control, "aws", side_effect=fake_aws), patch.object(control, "kubectl", return_value=""), \
                     patch.object(control, "apply", side_effect=fake_apply), patch.object(control, "run_job", side_effect=fake_job), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if publication_fails:
                        with self.assertRaises(RuntimeError):
                            control.execute(args)
                    else:
                        control.execute(args)
                self.assertEqual(actions, ["action-read", "disable-read"] if publication_fails else ["action-read"])
                if not publication_fails:
                    doc = documents[-1]
                    self.assertEqual(doc["metadata"]["name"], "remediation-read")
                    dsn = control.base64.b64decode(doc["data"]["action-read-database-url"]).decode()
                    self.assertEqual(unquote(urlsplit(dsn).username), "control_action_reader")
                    self.assertEqual(parse_qs(urlsplit(dsn).query), {"sslmode": ["verify-full"]})

    def test_failed_reader_recovery_points_only_to_disable_read(self):
        args = self.args(action="action-read", runtime_secret_arn=None)
        def fake_aws(_args, *operation, data=None):
            if operation == ("sts", "get-caller-identity"):
                return json.dumps({"Account": "123456789012"})
            return json.dumps({"SecretString": json.dumps({"username": "control_bootstrap", "password": "m"})})
        actions = []
        def fake_job(*_a, **kwargs):
            actions.append(kwargs.get("action", "action-read"))
            raise RuntimeError("job failed")
        err = io.StringIO()
        with patch.object(control, "aws", side_effect=fake_aws), patch.object(control, "kubectl", return_value=""), \
             patch.object(control, "apply"), patch.object(control, "run_job", side_effect=fake_job), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            with self.assertRaises(RuntimeError):
                control.execute(args)
        self.assertEqual(actions, ["action-read", "disable-read"])
        self.assertIn("control.py disable-read", err.getvalue())
        self.assertNotIn("disable command in the runbook", err.getvalue())

    def test_reader_disable_never_touches_live_logins(self):
        statements = control_job.disable_statements(("control_action_reader",))
        self.assertTrue(all("control_writer" not in s and "control_action_writer" not in s for s in statements))
        with self.assertRaises(ValueError):
            control_job.disable_statements(("control_owner",))

    def test_action_runtime_refuses_existing_secret_or_other_namespace(self):
        for overrides in ({"existing": True}, {"namespace": "sentinelops"}):
            with self.subTest(overrides=overrides):
                _, actions, documents, _ = self.action_execution(**overrides)
                self.assertEqual(actions, [])
                self.assertEqual(documents, [])

    def test_action_job_enforces_attributes_tls_and_privileges(self):
        for attributes, tls, privileges in ((False, True, True), (True, False, True),
                                           (True, True, False), (True, True, True)):
            with self.subTest(attributes=attributes, tls=tls, privileges=privileges):
                master_conn = SimpleNamespace(fetchval=AsyncMock(side_effect=[True, True, False, attributes]),
                                              execute=AsyncMock(), close=AsyncMock())
                runtime_conn = SimpleNamespace(fetchval=AsyncMock(side_effect=[tls, privileges]), close=AsyncMock())
                driver = SimpleNamespace(connect=AsyncMock(side_effect=[master_conn, runtime_conn]))
                password = secrets.token_urlsafe(36)
                master = json.dumps({"username": "control_bootstrap", "password": "master-in-RAM"})
                with patch.dict("sys.modules", {"asyncpg": driver}), \
                     patch.object(Path, "read_text", side_effect=[master, password]), \
                     patch.dict("os.environ", {"CONTROL_HOST": "control.example.rds.amazonaws.com"}), \
                     patch.object(control_job, "verify_schema", new=AsyncMock()), \
                     patch.object(control_job.ssl, "create_default_context", return_value=__import__("ssl").create_default_context()), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if attributes and tls and privileges:
                        asyncio.run(control_job.run("action-runtime"))
                    else:
                        with self.assertRaises(RuntimeError):
                            asyncio.run(control_job.run("action-runtime"))
                if attributes:
                    statement = master_conn.execute.call_args.args[0]
                    self.assertIn("ALTER ROLE control_action_writer LOGIN PASSWORD 'SCRAM-SHA-256$", statement)
                    self.assertNotIn(password, statement)
                    for forbidden in ("NOSUPERUSER", "NOREPLICATION", "NOBYPASSRLS"):
                        self.assertNotIn(forbidden, statement)
                    options = driver.connect.call_args.kwargs
                    self.assertEqual(options["user"], "control_action_writer")
                    self.assertTrue(options["ssl"].check_hostname)
                    self.assertEqual(options["ssl"].verify_mode, __import__("ssl").CERT_REQUIRED)
                    runtime_conn.close.assert_awaited_once()
                else:
                    master_conn.execute.assert_not_awaited()
                master_conn.close.assert_awaited_once()

    def test_job_rejects_non_tls_session(self):
        connection = SimpleNamespace(fetchval=AsyncMock(return_value=False), close=AsyncMock())
        driver = SimpleNamespace(connect=AsyncMock(return_value=connection))
        master = json.dumps({"username": "control_bootstrap", "password": secrets.token_urlsafe(36)})
        with patch.dict("sys.modules", {"asyncpg": driver}), \
             patch.object(Path, "read_text", return_value=master), \
             patch.dict("os.environ", {"CONTROL_HOST": "control.example.rds.amazonaws.com"}), \
             patch.object(control_job.ssl, "create_default_context", return_value=__import__("ssl").create_default_context()):
            with self.assertRaisesRegex(RuntimeError, "TLS required"):
                asyncio.run(control_job.run("bootstrap"))
        tls = driver.connect.call_args.kwargs["ssl"]
        self.assertTrue(tls.check_hostname)
        self.assertEqual(tls.verify_mode, __import__("ssl").CERT_REQUIRED)


def postgres_fixture(image):
    """Real SQL and SCRAM auth, restricted CREATEROLE migration principal; not RDS/TLS proof."""
    name = "sentinelops-control-check-" + uuid4().hex[:12]
    password = secrets.token_urlsafe(36)
    def docker(*args, data=None, success=True):
        result = subprocess.run(["docker", *args], input=data, text=True, capture_output=True, timeout=90)
        if success and result.returncode:
            raise RuntimeError("isolated PostgreSQL operation failed: " + result.stderr)
        if not success and result.returncode == 0:
            raise RuntimeError("unsafe SQL/login was unexpectedly accepted")
        return result.stdout.strip()
    def sql(statement, *, user="fixture_admin", database="sentinelops_control", success=True):
        return docker("exec", "-i", name, "psql", "-X", "-U", user, "-d", database,
                      "-v", "ON_ERROR_STOP=1", "-qAt", data=statement, success=success)
    docker("image", "inspect", image)
    docker("run", "--detach", "--rm", "--pull", "never", "--name", name, "--network", "none",
           "--read-only", "--user", "postgres", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
           "--tmpfs", "/var/lib/postgresql/data:rw,mode=1777", "--tmpfs", "/var/run/postgresql:rw,mode=1777",
           "--tmpfs", "/tmp:rw,mode=1777", "--env", "PGDATA=/var/lib/postgresql/data/pgdata",
           "--env", "POSTGRES_DB=sentinelops_control", "--env", "POSTGRES_USER=fixture_admin",
           "--env", "POSTGRES_HOST_AUTH_METHOD=trust", image)
    try:
        for _ in range(45):
            result = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1"], capture_output=True)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("fixture not ready")
        # Only this disposable software fixture changes pg_hba; no host files or ports.
        docker("exec", "-i", name, "sh", "-c",
               'cat > "$PGDATA/pg_hba.conf"',
               data="local all all trust\nhost all all 127.0.0.1/32 scram-sha-256\n")
        sql("SELECT pg_reload_conf(); CREATE ROLE control_bootstrap LOGIN CREATEROLE CREATEDB; "
            "GRANT pg_signal_backend TO control_bootstrap; "
            "ALTER DATABASE sentinelops_control OWNER TO control_bootstrap;")
        if sql("SELECT rolsuper FROM pg_roles WHERE rolname='control_bootstrap'") != "f":
            raise RuntimeError("fixture master must not be superuser")
        bootstrap = (control.ROOT / "infra/control" / control_job.MIGRATIONS[0]).read_text()
        sql(bootstrap, user="control_bootstrap", database="postgres", success=False)
        for filename in control_job.MIGRATIONS:
            print("Fixture migration: " + filename, flush=True)
            source = (control.ROOT / "infra/control" / filename).read_text()
            prepared = control_job.migration_sql(filename, source)
            if filename.startswith("005-"):
                # Inject failure before COMMIT: neither inherited owner rights nor action
                # schema/roles may leak from an interrupted optional migration.
                sql(prepared.replace("RESET ROLE;", "SELECT 1/0;\nRESET ROLE;"), user="control_bootstrap", success=False)
                if sql("SELECT bool_or(inherit_option) FROM pg_auth_members WHERE roleid='control_owner'::regrole "
                       "AND member='control_bootstrap'::regrole") != "f":
                    raise RuntimeError("failed migration leaked inherited owner rights")
            sql(prepared, user="control_bootstrap")
        self_check = sql("SET ROLE control_owner; "
                         "SELECT string_agg(version::text,',' ORDER BY version) FROM sentinel_control.schema_migrations; "
                         "SELECT string_agg(version::text,',' ORDER BY version) FROM sentinel_remediation.schema_migrations; "
                         "RESET ROLE;", user="control_bootstrap")
        if self_check != "1,2,3\n1,2":
            raise RuntimeError("unexpected ledgers")
        if sql("SELECT bool_or(inherit_option) FROM pg_auth_members WHERE roleid='control_owner'::regrole "
               "AND member='control_bootstrap'::regrole") != "f":
            raise RuntimeError("master inherited owner rights after migration")
        sql(bootstrap, user="control_bootstrap", success=False)
        for role in control_job.RUNTIME_ROLES.values():
            sql(f"ALTER ROLE {role} CREATEDB", user="control_bootstrap")
            if sql(control_job.role_attributes_sql(role), user="control_bootstrap") != "f":
                raise RuntimeError("elevated runtime attribute was accepted")
            sql(f"ALTER ROLE {role} NOCREATEDB", user="control_bootstrap")
            if sql(control_job.role_attributes_sql(role), user="control_bootstrap") != "t":
                raise RuntimeError("runtime role carries elevated attributes")
            sql(control_job.runtime_login_sql(role, password), user="control_bootstrap")
            if sql(f"SELECT rolvaliduntil = '2026-11-25 00:00:00+00'::timestamptz "
                   f"FROM pg_roles WHERE rolname='{role}'", user="control_bootstrap") != "t":
                raise RuntimeError("runtime expiry differs")
        def login(statement, success=True, role="control_writer"):
            return docker("exec", "-i", name, "sh", "-c",
                          f'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -X -h 127.0.0.1 -U {role} '
                          '-d sentinelops_control -qAt -v ON_ERROR_STOP=1', data=password + "\n" + statement, success=success)
        if login("SELECT current_user;") != "control_writer":
            raise RuntimeError("SCRAM authentication failed")
        login("SELECT * FROM sentinel_control.incidents;")
        for forbidden in ("SET ROLE control_owner", "CREATE TABLE sentinel_control.forbidden(id int)",
                          "TRUNCATE sentinel_control.incident_versions CASCADE",
                          "SELECT * FROM sentinel_remediation.states"):
            login(forbidden, success=False)
        action_role = "control_action_writer"
        if login("SELECT current_user", role=action_role) != action_role:
            raise RuntimeError("action SCRAM authentication failed")
        if login(control_job.runtime_privileges_sql(action_role), role=action_role) != "t":
            raise RuntimeError("action privilege boundary differs")
        # Seed an incident as its owner, then exercise all five 005 action tables
        # through the actual action login, including its column-scoped UPDATEs.
        sql("""SET ROLE control_owner; BEGIN;
            INSERT INTO sentinel_control.incidents(incident_id,current_revision,lifecycle_stage)
                VALUES('fixture-incident',1,'open');
            INSERT INTO sentinel_control.incident_versions(incident_id,revision,idempotency_key,
                request_hash,payload,context,actor,reason)
                VALUES('fixture-incident',1,'fixture-key',repeat('0',64),'{}','{}','fixture','fixture');
            COMMIT; RESET ROLE;""", user="control_bootstrap")
        login("""BEGIN;
            INSERT INTO sentinel_remediation.intents(action_id,idempotency_key,request_hash,intent_hash,
                target_key,incident_id,incident_revision,payload)
                VALUES(repeat('a',64),'fixture-action',repeat('b',64),repeat('c',64),repeat('d',64),
                    'fixture-incident',1,'{}');
            INSERT INTO sentinel_remediation.states(action_id,revision,phase,facts)
                VALUES(repeat('a',64),1,'prepared','{}');
            INSERT INTO sentinel_remediation.events(action_id,revision,phase,facts,event_hash)
                VALUES(repeat('a',64),1,'prepared','{}',repeat('e',64));
            INSERT INTO sentinel_remediation.targets(target_key,active_action)
                VALUES(repeat('d',64),repeat('a',64));
            INSERT INTO sentinel_remediation.dispatches(action_id,target_key,incident_id)
                VALUES(repeat('a',64),repeat('d',64),'fixture-incident');
            UPDATE sentinel_remediation.states SET revision=2,phase='blocked',facts='{}',updated_at=now();
            UPDATE sentinel_remediation.targets SET active_action=NULL,last_dispatch_at=now();
            COMMIT;""", role=action_role)
        for forbidden in ("SET ROLE control_owner", "SET ROLE control_writer", "SET ROLE control_action_resolver",
                          "CREATE SCHEMA forbidden", "CREATE TABLE sentinel_remediation.forbidden(id int)",
                          "CREATE TABLE sentinel_control.forbidden(id int)", "CREATE DATABASE forbidden",
                          "UPDATE sentinel_control.incidents SET lifecycle_stage='closed'",
                          "INSERT INTO sentinel_control.incidents(incident_id,current_revision,lifecycle_stage) "
                          "VALUES('forbidden',1,'open')",
                          "INSERT INTO sentinel_control.incident_versions SELECT * FROM sentinel_control.incident_versions",
                          "DELETE FROM sentinel_control.incidents", "TRUNCATE sentinel_control.incidents CASCADE",
                          "UPDATE sentinel_remediation.intents SET payload='{}'",
                          "UPDATE sentinel_remediation.states SET action_id=repeat('f',64)",
                          "DELETE FROM sentinel_remediation.events", "TRUNCATE sentinel_remediation.targets CASCADE"):
            login(forbidden, success=False, role=action_role)
        # A missing INSERT or an accidental incident/owner/schema/database grant
        # must fail the same production query, rather than just the example SQL.
        for grant, revoke in (
            ("REVOKE INSERT ON sentinel_remediation.events FROM control_action_writer",
             "GRANT INSERT ON sentinel_remediation.events TO control_action_writer"),
            ("GRANT UPDATE ON sentinel_control.incidents TO control_action_writer",
             "REVOKE UPDATE ON sentinel_control.incidents FROM control_action_writer"),
            ("GRANT CREATE ON DATABASE sentinelops_control TO control_action_writer",
             "REVOKE CREATE ON DATABASE sentinelops_control FROM control_action_writer"),
            ("GRANT CREATE ON SCHEMA public TO control_action_writer",
             "REVOKE CREATE ON SCHEMA public FROM control_action_writer")):
            as_owner = "ON DATABASE" not in grant and "ON SCHEMA public" not in grant
            sql("SET ROLE control_owner; " + grant if as_owner else grant,
                user="control_bootstrap")
            if login(control_job.runtime_privileges_sql(action_role), role=action_role) != "f":
                raise RuntimeError("unsafe action privileges were accepted")
            sql("SET ROLE control_owner; " + revoke if as_owner else revoke,
                user="control_bootstrap")
        sql("GRANT control_owner TO control_action_writer WITH INHERIT FALSE, SET TRUE", user="control_bootstrap")
        if login(control_job.runtime_privileges_sql(action_role), role=action_role) != "f":
            raise RuntimeError("SET-only owner membership was accepted")
        sql("REVOKE control_owner FROM control_action_writer", user="control_bootstrap")
        # The demo driver's SELECT-only login: grants run as the owner, then the production
        # privilege query and ActionReader's own write check must both accept it.
        reader = "control_action_reader"
        # Before the grants the reader cannot even resolve sentinel_control tables.
        login(control_job.runtime_privileges_sql(reader), success=False, role=reader)
        sql("SET ROLE control_owner; " + "; ".join(control_job.READER_GRANTS) + "; RESET ROLE;", user="control_bootstrap")
        if login(control_job.runtime_privileges_sql(reader), role=reader) != "t":
            raise RuntimeError("reader privilege boundary differs")
        if login("SELECT count(*) FROM sentinel_control.incidents", role=reader) != "1":
            raise RuntimeError("reader cannot read incidents")
        if login("""SELECT coalesce(bool_or(has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                OR has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES')),false)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('sentinel_control','sentinel_remediation') AND c.relkind IN ('r','p')""",
                 role=reader) != "f":
            raise RuntimeError("ActionReader write check would reject the reader")
        for forbidden in ("UPDATE sentinel_control.incidents SET lifecycle_stage='closed'",
                          "DELETE FROM sentinel_remediation.events", "SET ROLE control_owner",
                          "INSERT INTO sentinel_remediation.targets(target_key) VALUES(repeat('9',64))"):
            login(forbidden, success=False, role=reader)
        function = "sentinel_remediation.release_quarantine(TEXT,INTEGER,TEXT,TEXT,TEXT,JSONB)"
        sql(f"SET ROLE control_owner; GRANT EXECUTE ON FUNCTION {function} TO control_action_reader; RESET ROLE;",
            user="control_bootstrap")
        if login(control_job.runtime_privileges_sql(reader), role=reader) != "f":
            raise RuntimeError("reader able to release quarantine was accepted")
        sql(f"SET ROLE control_owner; REVOKE EXECUTE ON FUNCTION {function} FROM control_action_reader; RESET ROLE;",
            user="control_bootstrap")
        sql("SET ROLE control_owner; GRANT UPDATE ON sentinel_control.incidents TO control_action_reader; RESET ROLE;",
            user="control_bootstrap")
        if login(control_job.runtime_privileges_sql(reader), role=reader) != "f":
            raise RuntimeError("unsafe reader privileges were accepted")
        sql("SET ROLE control_owner; REVOKE UPDATE ON sentinel_control.incidents FROM control_action_reader; RESET ROLE;",
            user="control_bootstrap")
        for statement in control_job.disable_statements((reader,)):
            sql(statement, user="control_bootstrap")
        login("SELECT 1", success=False, role=reader)
        if login("SELECT current_user", role="control_action_writer") != "control_action_writer":
            raise RuntimeError("reader-only disable revoked the action writer")
        sql(control_job.runtime_login_sql(reader, password), user="control_bootstrap")
        # pg_signal_backend models the RDS master's non-superuser termination capability.
        sessions = []
        try:
            for role in control_job.RUNTIME_ROLES.values():
                session = subprocess.Popen(["docker", "exec", "-i", name, "sh", "-c",
                    f'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -X -h 127.0.0.1 -U {role} '
                    '-d sentinelops_control -qAt -v ON_ERROR_STOP=1'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                sessions.append(session)
                session.stdin.write(password + "\nSELECT pg_sleep(60);\n")
                session.stdin.close()
            for _ in range(50):
                if sql("SELECT count(*) FROM pg_stat_activity WHERE usename IN "
                       "('control_writer','control_action_writer','control_action_reader') "
                       "AND wait_event='PgSleep'") == "3":
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("persistent runtime sessions not ready")
            for statement in control_job.disable_statements():
                sql(statement, user="control_bootstrap")
            for session in sessions:
                if session.wait(timeout=10) == 0:
                    raise RuntimeError("disable failed to terminate runtime session")
        finally:
            for session in sessions:
                if session.poll() is None:
                    session.kill()
                session.wait(timeout=10)
        for role in control_job.RUNTIME_ROLES.values():
            login("SELECT 1", success=False, role=role)
        if sql("SELECT bool_and(NOT rolcanlogin AND rolpassword IS NULL) FROM pg_authid "
               "WHERE rolname IN ('control_writer','control_action_writer','control_action_reader')") != "t":
            raise RuntimeError("disable did not clear both credentials")
        if sql("SELECT count(*) FROM pg_stat_activity WHERE usename IN "
               "('control_writer','control_action_writer','control_action_reader')") != "0":
            raise RuntimeError("runtime sessions remain")
        print(json.dumps({"postgres_image": image, "status": "PASS", "core_versions": [1, 2, 3],
            "remediation_versions": [1, 2], "restricted_master": True, "scram_auth": True,
            "runtime_forbidden_operations_rejected": True, "wrong_database_and_replay_rejected": True,
            "action_runtime_privileges": True, "both_logins_and_sessions_revoked": True,
            "aws_calls": False, "host_ports": False, "network": "none", "rds_or_tls_proof": False}))
    finally:
        docker("rm", "--force", name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-image", help="Optional already-local postgres:16-alpine/17-alpine")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OfflineChecks)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    if args.postgres_image:
        postgres_fixture(args.postgres_image)
