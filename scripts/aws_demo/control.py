"""Operator CLI. Offline render by default; mutations require --execute.

No secret files, shell interpolation, secret argv, or echoed subprocess output.
AWS CLI v2 and kubectl run only on the operator host, never inside the Job.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import quote
from uuid import uuid4

from control_job import EXPIRES, MIGRATIONS, parse_expiry

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNTIME_NAME = "sentinelops/dev/control-runtime"
LOGIN_ACTIONS = ("runtime", "action-runtime", "action-read")


def command(argv, payload=None):
    result = subprocess.run(argv, input=payload, text=True, capture_output=True, timeout=750)
    if result.returncode:
        # AWS/kubectl errors may contain request data. Do not echo them or a traceback.
        raise RuntimeError(f"{argv[0]} {argv[1]} failed (exit {result.returncode}); output suppressed")
    return result.stdout


def aws(args, *operation, data=None):
    return command(["aws", "--region", args.region, "--no-cli-pager", "--output", "json",
                    *operation], data)


def kubectl(args, *operation, data=None):
    return command(["kubectl", "--context", args.context, "--namespace", args.namespace,
                    *operation], data)


def apply(args, document, *, create=False):
    kubectl(args, "create" if create else "apply", "-f", "-", data=json.dumps(document))


def secret_document(args, name, values):
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": args.namespace},
            "type": "Opaque", "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}}


def render(args, name="control-bootstrap", master="control-bootstrap-master", config="control-bootstrap-sql"):
    document = json.loads((HERE / "control-job.yaml").read_text())
    document["metadata"].update(name=name, namespace=args.namespace)
    pod = document["spec"]["template"]["spec"]
    container = pod["containers"][0]
    container["image"] = args.image
    container["command"][-1] = args.action
    container["env"][0]["value"] = args.host
    if args.action in LOGIN_ACTIONS:
        container["env"].append({"name": "CONTROL_RUNTIME_EXPIRES_AT",
                                 "value": args.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")})
    pod["volumes"][0]["configMap"]["name"] = config
    pod["volumes"][1]["secret"]["secretName"] = master
    pod["volumes"][2]["configMap"]["name"] = args.ca_config
    return document


def run_job(args, master_name, config_name, *, action=None):
    selected = args.action
    args.action = action or selected
    name = "control-" + args.action + "-" + uuid4().hex[:12]
    try:
        apply(args, render(args, name, master_name, config_name), create=True)
        print(f"Job {args.namespace}/{name} created", flush=True)
        try:
            kubectl(args, "wait", "--for=condition=complete", f"job/{name}", "--timeout=620s")
        finally:
            # Our Job prints only fixed status/migration names, never exception details.
            print(kubectl(args, "logs", f"job/{name}", "--all-containers=true"), end="", flush=True)
    finally:
        args.action = selected
        # Remove pods before removing their temporary master Secret. TTL alone is insufficient.
        kubectl(args, "delete", f"job/{name}", "--ignore-not-found", "--wait=true", "--timeout=90s")


def read_runtime(args):
    response = json.loads(aws(args, "secretsmanager", "get-secret-value", "--secret-id", args.runtime_secret_arn))
    value = json.loads(response["SecretString"])
    endpoint = f"postgresql://{args.host}:5432/sentinelops_control?sslmode=verify-full"
    if (value.get("worker-database-url") != endpoint or value.get("username") != "control_writer"
        or not isinstance(value.get("expires-at"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,}", value.get("password", ""))):
        raise ValueError("runtime secret shape/endpoint differs")
    full = f"postgresql://control_writer:{quote(value['password'], safe='')}@{args.host}:5432/sentinelops_control?sslmode=verify-full"
    if value.get("database-url") != full:
        raise ValueError("runtime DSN differs")
    if datetime.now(timezone.utc) >= parse_expiry(value["expires-at"]):
        raise ValueError("runtime login expired")
    return value


def sync(args):
    value = read_runtime(args)
    dsn = value["worker-database-url" if args.consumer == "worker" else "database-url"]
    # replace/create avoids the kubectl last-applied annotation duplicating secret data.
    doc = secret_document(args, "control-runtime", {"database-url": dsn, "password": value["password"],
        "username": "control_writer", "api-database-url": value["database-url"]})
    existing = json.loads(kubectl(args, "get", "secret", "control-runtime", "--ignore-not-found", "-o", "json") or "null")
    if existing:
        doc["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
        kubectl(args, "replace", "-f", "-", data=json.dumps(doc))
    else:
        apply(args, doc, create=True)
    print(f"Synced control-runtime for {args.consumer} in {args.namespace}; restart consumers explicitly")


def execute(args):
    if args.action in LOGIN_ACTIONS and datetime.now(timezone.utc) >= args.expires_at:
        raise ValueError("demo exception expired")
    account = json.loads(aws(args, "sts", "get-caller-identity"))["Account"]
    if account != args.image.split(".", 1)[0]:
        raise ValueError("AWS caller account differs from the reviewed ECR image")
    for arn in (args.master_secret_arn, args.runtime_secret_arn):
        if arn and not arn.startswith(f"arn:aws:secretsmanager:{args.region}:{account}:secret:"):
            raise ValueError("secret ARN differs from the selected region/account")
    if args.action == "sync-runtime":
        description = json.loads(aws(args, "secretsmanager", "describe-secret", "--secret-id", args.runtime_secret_arn))
        if description.get("Name") != RUNTIME_NAME:
            raise ValueError("wrong runtime secret name")
        sync(args)
        return
    if args.action == "action-runtime":
        if args.namespace != "sentinelops-b-actions":
            raise ValueError("action-runtime requires sentinelops-b-actions namespace")
        if kubectl(args, "get", "secret", "remediation-runtime", "--ignore-not-found", "-o", "name").strip():
            raise ValueError("remediation-runtime already exists; review before replacing")
    if args.action == "action-read":
        if args.namespace != "sentinelops-b-actions":
            raise ValueError("action-read requires sentinelops-b-actions namespace")
        if kubectl(args, "get", "secret", "remediation-read", "--ignore-not-found", "-o", "name").strip():
            raise ValueError("remediation-read already exists; review before replacing")
    if args.action == "runtime":
        description = json.loads(aws(args, "secretsmanager", "describe-secret", "--secret-id", args.runtime_secret_arn))
        if description.get("Name") != RUNTIME_NAME:
            raise ValueError("wrong runtime secret name")
        # This is fresh bootstrap, not an implicit rotation of live worker credentials.
        if description.get("VersionIdsToStages"):
            raise ValueError("runtime already has versions; use sync-runtime or review explicit rotation")
        if kubectl(args, "get", "secret", "control-runtime", "--ignore-not-found", "-o", "name").strip():
            raise ValueError("control-runtime already exists; review before replacing")
    response = json.loads(aws(args, "secretsmanager", "get-secret-value", "--secret-id", args.master_secret_arn))
    master = json.loads(response["SecretString"])
    if master.get("username") != "control_bootstrap" or not master.get("password"):
        raise ValueError("expected RDS managed master identity")
    suffix = uuid4().hex[:12]
    master_name, config_name = "control-master-" + suffix, "control-sql-" + suffix
    values = {"master.json": json.dumps(master)}
    if args.action in LOGIN_ACTIONS:
        values["runtime-password"] = secrets.token_urlsafe(36)
    config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": config_name, "namespace": args.namespace},
              "data": {"control_job.py": (HERE / "control_job.py").read_text(),
                       **{name: (ROOT / "infra/control" / name).read_text() for name in MIGRATIONS}}}
    # Never use kubectl apply for a Secret; its last-applied annotation repeats the value.
    print(f"Cleanup targets: secret/{master_name}, configmap/{config_name}", flush=True)
    config_created = False
    runtime_attempted = False
    try:
        apply(args, secret_document(args, master_name, values), create=True)
        # Mark first so a lost API response still triggers cleanup of the unique name.
        config_created = True
        apply(args, config, create=True)
        runtime_attempted = args.action in LOGIN_ACTIONS
        run_job(args, master_name, config_name)
        if args.action == "action-runtime":
            password = values["runtime-password"]
            dsn = (f"postgresql://control_action_writer:{quote(password, safe='')}@{args.host}:5432/"
                   "sentinelops_control?sslmode=verify-full")
            # Fresh create only: never replace live action credentials or write Secrets Manager.
            apply(args, secret_document(args, "remediation-runtime", {"action-database-url": dsn}), create=True)
        if args.action == "action-read":
            password = values["runtime-password"]
            dsn = (f"postgresql://control_action_reader:{quote(password, safe='')}@{args.host}:5432/"
                   "sentinelops_control?sslmode=verify-full")
            apply(args, secret_document(args, "remediation-read", {"action-read-database-url": dsn}), create=True)
        if args.action == "runtime":
            password = values["runtime-password"]
            endpoint = f"postgresql://{args.host}:5432/sentinelops_control?sslmode=verify-full"
            full = f"postgresql://control_writer:{quote(password, safe='')}@{args.host}:5432/sentinelops_control?sslmode=verify-full"
            payload = {"database-url": full, "worker-database-url": endpoint, "username": "control_writer",
                       "password": password, "expires-at": args.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")}
            # AWS CLI v2 cannot parse --cli-input-json from a piped /dev/stdin under WSL.
            # Use a 0600 file on tmpfs (/dev/shm, RAM only), never argv, and unlink it at once.
            fd, request_path = tempfile.mkstemp(dir="/dev/shm", prefix="control-runtime-", suffix=".json")
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump({"SecretId": args.runtime_secret_arn, "ClientRequestToken": str(uuid4()),
                               "SecretString": json.dumps(payload)}, handle)
                aws(args, "secretsmanager", "put-secret-value", "--cli-input-json", "file://" + request_path)
            finally:
                os.unlink(request_path)
            sync(args)
    except Exception:
        if runtime_attempted:
            try:
                if args.action == "action-read":
                    run_job(args, master_name, config_name, action="disable-read")
                    print("Reader failure: reader login disabled; worker/controller logins untouched", file=sys.stderr)
                else:
                    run_job(args, master_name, config_name, action="disable")
                    print("Runtime failure: all demo logins disabled and their sessions terminated", file=sys.stderr)
            except Exception:
                if args.action == "action-read":
                    # Never fall back to the general disable: it revokes the live worker/controller logins.
                    print("URGENT: revoke the reader login with `control.py disable-read`; leave other logins", file=sys.stderr)
                else:
                    print("URGENT: revoke both demo logins manually using the disable command in the runbook", file=sys.stderr)
        raise
    finally:
        # If Job deletion failed, these may also fail: report the non-secret names for manual cleanup.
        print(f"Cleanup targets: secret/{master_name}, configmap/{config_name}", flush=True)
        kubectl(args, "delete", f"secret/{master_name}", "--ignore-not-found", "--wait=true")
        if config_created:
            kubectl(args, "delete", f"configmap/{config_name}", "--ignore-not-found", "--wait=true")
    print(f"{args.action}: PASS (cloud execution by operator)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "runtime", "action-runtime", "action-read", "sync-runtime", "disable", "disable-read"))
    parser.add_argument("--host", required=True, help="Terraform control_database_endpoint")
    parser.add_argument("--image", required=True, help="Terraform image_repository_urls[control-plane] + @sha256:...")
    parser.add_argument("--namespace", default="sentinelops-b-actions")
    parser.add_argument("--context", required=True, help="Explicit reviewed kube context")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument("--master-secret-arn", help="Terraform control_database_secret_arn")
    parser.add_argument("--runtime-secret-arn", help="Terraform control_runtime_secret_arn")
    parser.add_argument("--ca-config", default="control-rds-ca")
    parser.add_argument("--consumer", choices=("worker", "api"), default="worker")
    parser.add_argument("--expires-at", type=parse_expiry, help="Operator-approved UTC login expiry (YYYY-MM-DDTHH:MM:SSZ)")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.expires_at is not None and args.action not in LOGIN_ACTIONS:
        parser.error("--expires-at applies to runtime, action-runtime or action-read")
    args.expires_at = args.expires_at or EXPIRES
    if not re.fullmatch(r"[a-z0-9.-]+\.rds\.amazonaws\.com", args.host):
        parser.error("use the exact RDS DNS endpoint, without scheme or port")
    if not re.fullmatch(r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s@]+@sha256:[0-9a-f]{64}", args.image):
        parser.error("control-plane image must be pinned by ECR digest")
    if not args.image.split("@", 1)[0].endswith("/control-plane"):
        parser.error("select the control-plane repository")
    if args.action in ("runtime", "sync-runtime") and not args.runtime_secret_arn:
        parser.error("--runtime-secret-arn required")
    if args.action != "sync-runtime" and args.execute and not args.master_secret_arn:
        parser.error("--master-secret-arn required")
    if args.execute:
        # /dev/stdin is an AWS CLI file URI on the Linux operator host.
        if os.name != "posix":
            parser.error("execute on Linux/WSL with AWS CLI v2 and kubectl")
        execute(args)
    elif args.action == "sync-runtime":
        print("OFFLINE: would sync Secrets Manager to Kubernetes; no credentials read")
    else:
        print(json.dumps(render(args), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"control workflow FAILED ({type(exc).__name__}); secret-bearing tool output suppressed", file=sys.stderr)
        sys.exit(1)
