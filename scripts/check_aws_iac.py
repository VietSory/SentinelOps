"""Validate the AWS baseline configuration and its safety invariants.

Runs the real `terraform` toolchain (`validate`, `fmt -check`) against
`infra/aws`, then asserts the invariants the architecture depends on: nothing is
public by default, data at rest is encrypted, every log group has bounded
retention, no workload role is an administrator, and the configuration grants
neither model activation nor remediation authority.

This validates a description of infrastructure. It performs no AWS API call,
needs no credentials, creates nothing and is not authorization to apply.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT/'infra/aws'

# Managed policies that would hand a workload far more than its lane needs.
FORBIDDEN_POLICIES = (
    'AdministratorAccess', 'PowerUserAccess', 'IAMFullAccess',
    'AmazonS3FullAccess', 'AmazonRDSFullAccess', 'AmazonEC2FullAccess',
    'AWSBedrockFullAccess', 'AmazonEKSClusterAdminPolicy',
)

# Actions no workload role may hold: they would let an AWS credential escalate
# into cluster or identity authority, or mutate the commerce database.
FORBIDDEN_ACTIONS = (
    'iam:*', 'iam:CreateRole', 'iam:AttachRolePolicy', 'iam:PutRolePolicy',
    'iam:CreateUser', 'iam:CreateAccessKey', 'sts:AssumeRole*',
    'eks:*', 'eks:CreateCluster', 'eks:UpdateClusterConfig',
    'kms:ScheduleKeyDeletion', 'kms:PutKeyPolicy',
    'rds:DeleteDBInstance', 'rds:ModifyDBInstance',
    's3:DeleteBucket', 's3:PutBucketPolicy',
    'secretsmanager:PutSecretValue', 'secretsmanager:DeleteSecret',
)


def terraform(*arguments: str) -> str:
    executable = shutil.which('terraform')
    if executable is None:
        raise SystemExit('terraform is required to validate the AWS baseline')
    completed = subprocess.run([executable, *arguments], cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit('terraform '+' '.join(arguments)+' failed:\n'+completed.stdout+completed.stderr)
    return completed.stdout


def normalize(body: str) -> str:
    """Collapse HCL alignment padding so `a = b` matches regardless of spacing."""
    return re.sub(r'[ \t]*=[ \t]*', ' = ', re.sub(r'[ \t]+', ' ', body))


def _close(text: str, start: int) -> int:
    depth, index = 1, start
    while index < len(text) and depth:
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
        index += 1
    return index - 1


def _blocks(pattern: re.Pattern) -> dict[str, dict]:
    """Extract top-level HCL blocks by key, without needing state or an apply."""
    found: dict[str, dict] = {}
    for path in sorted(ROOT.glob('*.tf')):
        text = path.read_text()
        for match in pattern.finditer(text):
            end = _close(text, match.end())
            key = '.'.join(match.groups())
            found[key] = {'kind': match.group(1) if len(match.groups()) > 1 else None,
                          'body': normalize(text[match.end():end]), 'file': path.name}
    return found


def _assignments(body: str) -> dict[str, list[str]]:
    """`name = value` lines with comments removed; every assignment of a name is kept, so a duplicate is visible."""
    found: dict[str, list[str]] = {}
    for line in body.splitlines():
        line = line.split('#', 1)[0].split('//', 1)[0].strip()
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*) = (.+?),?', line)
        if match:
            found.setdefault(match.group(1), []).append(match.group(2).strip())
    return found


def _locals() -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for path in sorted(ROOT.glob('*.tf')):  # several files may each hold a `locals` block
        text = path.read_text()
        for match in re.finditer(r'^locals\s*\{', text, re.MULTILINE):
            for name, values in _assignments(normalize(text[match.end():_close(text, match.end())])).items():
                merged.setdefault(name, []).extend(values)
    return merged


def resources() -> dict[str, dict]:
    return _blocks(re.compile(r'^resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE))


def policy_documents() -> dict[str, dict]:
    return _blocks(re.compile(r'^data\s+"aws_iam_policy_document"\s+"([^"]+)"\s*\{', re.MULTILINE))


def assert_private_by_default(blocks: dict[str, dict]) -> list[str]:
    checks = []

    cluster = blocks['aws_eks_cluster.main']['body']
    if 'endpoint_private_access = true' not in cluster:
        raise ValueError('the EKS API server must keep private access enabled')
    if 'endpoint_public_access = var.cluster_public_access' not in cluster:
        raise ValueError('EKS public access must stay driven by the explicit variable')
    checks.append('EKS private access enabled; public access only via explicit variable')

    database = blocks['aws_db_instance.control']['body']
    for requirement, message in (
        ('publicly_accessible = false', 'the control database must not be publicly accessible'),
        ('storage_encrypted = true', 'the control database must be encrypted at rest'),
        ('skip_final_snapshot = false', 'teardown must retain a final snapshot of IER/audit history'),
    ):
        if requirement not in database:
            raise ValueError(message)
    checks.append('control database private, encrypted and retains a final snapshot')

    access_block = blocks['aws_s3_bucket_public_access_block.artifacts']['body']
    for setting in ('block_public_acls', 'block_public_policy', 'ignore_public_acls', 'restrict_public_buckets'):
        if setting not in access_block or 'false' in access_block.split(setting)[1].split('\n')[0]:
            raise ValueError('the artifact bucket must block all public access: '+setting)
    checks.append('artifact bucket blocks public ACLs and policies')

    for name in ('aws_subnet.private', 'aws_subnet.public'):
        if 'map_public_ip_on_launch = false' not in blocks[name]['body']:
            raise ValueError(name+' must not auto-assign public addresses')
    checks.append('no subnet auto-assigns public addresses')

    if 'versioning_configuration' not in blocks['aws_s3_bucket_versioning.artifacts']['body']:
        raise ValueError('the artifact bucket must be versioned so a release stays reproducible')
    checks.append('artifact bucket versioned')

    registry = _assignments(blocks['aws_ecr_repository.images']['body'])
    for name, value, message in (
        ('for_each', 'local.image_repositories', 'the image registry must create exactly the declared repositories'),
        ('image_tag_mutability', '"IMMUTABLE"', 'image tags must be immutable so a pinned digest keeps its meaning'),
        ('scan_on_push', 'true', 'images must be scanned on push'),
        ('encryption_type', '"KMS"', 'the image registry must be KMS-encrypted'),
        ('kms_key', 'aws_kms_key.main.arn', 'the image registry must use the environment KMS key'),
    ):
        if registry.get(name) != [value]:
            raise ValueError(message)
    if _locals().get('image_repositories') != ['toset(["application", "control-plane"])']:
        raise ValueError('the image registry must create exactly the declared repositories')
    lifecycle = _assignments(blocks['aws_ecr_lifecycle_policy.images']['body'])
    if lifecycle.get('tagStatus') != ['"untagged"'] or lifecycle.get('countNumber') != ['14']:
        raise ValueError('only untagged layers may expire, after 14 days')
    if any(key.startswith('aws_ecr_repository_policy.') or key.startswith('aws_ecr_registry_policy.') for key in blocks):
        raise ValueError('the image registry must not carry a resource policy that could grant outside access')
    checks.append('image registry: two declared repositories, immutable, scanned, environment KMS key, untagged-only 14-day expiry, no resource policy')
    return checks


def assert_bounded_retention(blocks: dict[str, dict]) -> list[str]:
    groups = [key for key in blocks if key.startswith('aws_cloudwatch_log_group.')]
    if not groups:
        raise ValueError('expected CloudWatch log groups for the control plane')
    for key in groups:
        body = blocks[key]['body']
        if 'retention_in_days = var.log_retention_days' not in body:
            raise ValueError(key+' must use the bounded retention variable, never unlimited retention')
        if 'kms_key_id' not in body:
            raise ValueError(key+' must be encrypted with the project key')
    return [f'all {len(groups)} log groups use bounded, encrypted retention']


def assert_no_overbroad_identity(blocks: dict[str, dict], documents: dict[str, dict]) -> list[str]:
    checks = []
    for key, block in blocks.items():
        if block['kind'] != 'aws_iam_role_policy_attachment':
            continue
        for policy in FORBIDDEN_POLICIES:
            if policy in block['body']:
                raise ValueError(key+' attaches an over-broad managed policy: '+policy)
    checks.append('no role attaches an administrator or service-wide managed policy')

    workload_documents = {name: document for name, document in documents.items()
                          if name in ('telemetry_reader', 'data_worker', 'explanation', 'controller', 'api')}
    if len(workload_documents) != 5:
        raise ValueError('expected one policy document per workload role')
    for name, document in workload_documents.items():
        body = document['body']
        for action in FORBIDDEN_ACTIONS:
            if '"'+action+'"' in body:
                raise ValueError(name+' grants a forbidden action: '+action)
        if '"*"' in body.replace('resources = ["*"]', ''):
            # A wildcard action list is never acceptable; wildcard *resources*
            # are tolerated only for APIs that are not resource-scoped.
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith('actions') and '"*"' in stripped:
                    raise ValueError(name+' uses a wildcard action list')
    checks.append('no workload policy grants IAM, EKS, cluster or destructive authority')

    controller = documents['controller']['body']
    for mutation in ('s3:PutObject', 'eks:', 'ecr:PutImage', 'secretsmanager:PutSecretValue'):
        if mutation in controller:
            raise ValueError('the controller role must hold no AWS mutation authority: '+mutation)
    checks.append('controller role is read-only in AWS; rollback authority is Kubernetes RBAC only')

    explanation = documents['explanation']['body']
    if 'bedrock:InvokeModel' not in explanation:
        raise ValueError('the explanation policy should describe a bounded invoke permission')
    if 'foundation-model/${id}' not in explanation:
        raise ValueError('Bedrock invoke must be bounded to explicitly named models')
    for action in ('bedrock:*', 'bedrock:CreateModelCustomizationJob'):
        if action in explanation:
            raise ValueError('the explanation role must not hold '+action)
    checks.append('Bedrock invoke bounded to named models in one region, disabled when none supplied')

    irsa = documents['irsa']['body']
    if 'system:serviceaccount:${local.namespace}:${each.key}' not in irsa:
        raise ValueError('each role must trust exactly one Kubernetes service account')
    if 'sts.amazonaws.com' not in irsa:
        raise ValueError('the IRSA trust policy must pin the sts audience')
    checks.append('each workload role trusts exactly one service account via IRSA')
    return checks


def assert_no_static_credentials(root: Path = ROOT) -> list[str]:
    secret = re.compile(r'(AKIA[0-9A-Z]{16})|(aws_secret_access_key\s*=)|(password\s*=\s*")', re.IGNORECASE)
    for path in sorted(root.glob('*')):
        if path.is_dir() or path.name == '.terraform.lock.hcl':
            continue
        if secret.search(path.read_text()):
            raise ValueError('a static credential or inline password must never be committed: '+path.name)
    if 'manage_master_user_password = true' not in normalize((root/'storage.tf').read_text()):
        raise ValueError('the control database password must be generated and held by AWS, not Terraform')
    return ['no static AWS key or inline password; database password managed by AWS']


def assert_grants_no_authority(root: Path = ROOT) -> list[str]:
    outputs = (root/'outputs.tf').read_text()
    for name in ('model_activation_allowed', 'remediation_approval'):
        if name not in outputs:
            raise ValueError('outputs must state that infrastructure grants no '+name)
        if 'false' not in outputs.split(name)[1].split('}')[0]:
            raise ValueError(name+' must be false')
    # Match a real backend block, not the word "backend" in a comment.
    for path in sorted(root.glob('*.tf')):
        if re.search(r'^\s*backend\s+"[^"]+"\s*\{', path.read_text(), re.MULTILINE):
            raise ValueError('a state backend is an owner-provisioned resource, not part of this description')
    return ['configuration grants neither model activation nor remediation authority']


def exercise(output: Path | None) -> dict:
    version = json.loads(terraform('version', '-json'))
    terraform('fmt', '-check', '-recursive')
    validate = json.loads(terraform('validate', '-json'))
    if not validate.get('valid'):
        raise SystemExit('terraform validate reported an invalid configuration:\n'+json.dumps(validate, indent=2))

    blocks, documents = resources(), policy_documents()
    checks: list[str] = []
    checks += assert_private_by_default(blocks)
    checks += assert_bounded_retention(blocks)
    checks += assert_no_overbroad_identity(blocks, documents)
    checks += assert_no_static_credentials()
    checks += assert_grants_no_authority()

    result = {'status': 'PASS', 'scope': 'aws-configuration-description-only',
              'terraform_version': version['terraform_version'],
              'provider_versions': version.get('provider_selections', {}),
              'resource_count': len(blocks), 'policy_document_count': len(documents),
              'checks': checks,
              'aws_api_calls': 0, 'resources_created': 0, 'apply_authorized': False,
              'model_activation_allowed': False, 'remediation_approval': False,
              'G7_accepted': False}
    if output is not None:
        if output.exists():
            raise ValueError('IaC validation output must be new')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    print(json.dumps(exercise(parser.parse_args().output), indent=2), flush=True)
    print('AWS IAC VALIDATION: PASS', flush=True)


if __name__ == '__main__':
    main()
