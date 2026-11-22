#!/usr/bin/env python3
"""Plan or create the Terraform foundation for a fresh SentinelOps AWS demo."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


SOURCE = Path(__file__).resolve().parents[2] / "infra/aws"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--inputs", required=True, type=Path, help="Reviewed JSON Terraform variables")
    parser.add_argument("--work-dir", required=True, type=Path, help="Dedicated private directory on Linux/WSL")
    parser.add_argument("--apply", action="store_true", help="Create resources and incur AWS charges")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("run this command in Linux/WSL")
    import fcntl

    inputs = json.loads(args.inputs.read_text())
    accounts = inputs.get("allowed_account_ids", [])
    if len(accounts) != 1 or not re.fullmatch(r"[0-9]{12}", accounts[0]):
        raise ValueError("inputs must pin exactly one AWS account")
    if inputs.get("environment") != "dev" or inputs.get("region") != "ap-southeast-1":
        raise ValueError("expected the dev demo in ap-southeast-1")
    prefix = inputs.get("name_prefix")
    if not isinstance(prefix, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", prefix):
        raise ValueError("inputs must set a valid name_prefix")
    cluster = f"{prefix}-dev"
    if inputs.get("edge_enabled"):
        # On a fresh VPC CloudFront must create the origin service SG first.
        inputs["edge_vpc_origin_bootstrap"] = True

    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("TF_CLI_ARGS", "TF_VAR_", "TF_LOG"))
           and key not in {"TF_DATA_DIR", "TF_WORKSPACE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID",
                           "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_WEB_IDENTITY_TOKEN_FILE",
                           "AWS_ROLE_ARN"}}
    env.update(AWS_PROFILE=args.profile, AWS_REGION=inputs["region"], AWS_DEFAULT_REGION=inputs["region"],
               AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off", TF_IN_AUTOMATION="1")
    aws = ["aws", "--region", inputs["region"], "--output", "json"]
    caller = subprocess.check_output(aws + ["sts", "get-caller-identity"], env=env, text=True)
    if json.loads(caller)["Account"] != accounts[0]:
        raise ValueError("active AWS profile does not match the reviewed input account")

    os.umask(0o077)
    directory = args.work_dir.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError("work-dir must be owned by you and private, e.g. a new directory under your Linux home")
    with (directory / ".rebuild.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args, inputs, cluster, env, aws, directory)


def run(args, inputs, cluster, env, aws, directory):
    rendered_inputs = directory / "demo.tfvars.json"
    state = directory / "terraform.tfstate"
    owned_files = [*SOURCE.glob("*.tf"), SOURCE / ".terraform.lock.hcl"]
    expected_inputs = json.dumps(inputs, indent=2) + "\n"
    if state.exists() and (not rendered_inputs.exists() or rendered_inputs.read_text() != expected_inputs):
        raise ValueError("work-dir already contains state with different inputs")
    extra_configs = set(directory.glob("*.tf*")) - {
        directory / item.name for item in owned_files
    } - {state, rendered_inputs, directory / "terraform.tfstate.backup", directory / "terraform.tfplan"}
    if extra_configs:
        raise ValueError("work-dir has unexpected Terraform files")

    existing = subprocess.run(aws + ["eks", "describe-cluster", "--name", cluster],
                              env=env, capture_output=True, text=True, check=False)
    if existing.returncode and "(ResourceNotFoundException)" not in existing.stderr:
        raise ValueError("unable to confirm cluster name availability in AWS")
    if args.apply and not existing.returncode:
        live = json.loads(existing.stdout)["cluster"]
        tracked = json.loads(state.read_text())["resources"] if state.exists() else []
        matching = any(instance["attributes"].get("endpoint") == live["endpoint"]
                       for resource in tracked if resource["type"] == "aws_eks_cluster"
                       for instance in resource["instances"])
        if not matching:
            raise ValueError("the AWS cluster already exists outside this rebuild directory")
    for item in owned_files:
        shutil.copyfile(item, directory / item.name)
    rendered_inputs.write_text(expected_inputs)

    def terraform(*command, capture=False):
        return subprocess.run(["terraform", *command], cwd=directory, env=env, check=True, text=True,
                              capture_output=capture).stdout

    terraform("init", "-backend=false", "-lockfile=readonly", "-input=false", "-no-color")
    terraform("plan", "-input=false", "-no-color", "-var-file=demo.tfvars.json", "-out=terraform.tfplan")
    plan = json.loads(terraform("show", "-json", "terraform.tfplan", capture=True))
    for item in plan.get("resource_changes", []):
        change = item["change"]
        if "delete" in change["actions"]:
            raise ValueError(f"plan would delete or replace {item['address']}")
        if (item["type"] == "aws_cloudfront_distribution" and (change.get("before") or {}).get("enabled")
                and not (change.get("after") or {}).get("enabled")):
            raise ValueError("edge has already been published; use its deployment runbook for changes")
    if args.apply:
        terraform("apply", "-input=false", "-no-color", "terraform.tfplan")
        outputs = terraform("output", "-json", capture=True)
        (directory / "outputs.json").write_text(outputs)
        print(f"Infrastructure outputs saved to {directory / 'outputs.json'}")
    else:
        print(f"Plan saved to {directory / 'terraform.tfplan'}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Infrastructure rebuild stopped: {exc}") from None
