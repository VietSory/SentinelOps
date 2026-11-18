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
            "ALTER DATABASE sentinelops_control OWNER TO control_bootstrap;")
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
        sql("ALTER ROLE control_writer LOGIN PASSWORD '" + control_job.scram_verifier(password) +
            "' VALID UNTIL '2026-11-25 00:00:00+00'", user="control_bootstrap")
        def login(statement, success=True):
            return docker("exec", "-i", name, "sh", "-c",
                          'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -X -h 127.0.0.1 -U control_writer '
                          '-d sentinelops_control -qAt -v ON_ERROR_STOP=1', data=password + "\n" + statement, success=success)
        if login("SELECT current_user;") != "control_writer":
            raise RuntimeError("SCRAM authentication failed")
        login("SELECT * FROM sentinel_control.incidents;")
        for forbidden in ("SET ROLE control_owner", "CREATE TABLE sentinel_control.forbidden(id int)",
                          "TRUNCATE sentinel_control.incident_versions CASCADE",
                          "SELECT * FROM sentinel_remediation.states"):
            login(forbidden, success=False)
        sql("ALTER ROLE control_writer NOLOGIN PASSWORD NULL", user="control_bootstrap")
        login("SELECT 1", success=False)
        print(json.dumps({"postgres_image": image, "status": "PASS", "core_versions": [1, 2, 3],
            "remediation_versions": [1, 2], "restricted_master": True, "scram_auth": True,
            "runtime_forbidden_operations_rejected": True, "wrong_database_and_replay_rejected": True,
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
