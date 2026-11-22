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
    executable = shutil.which('terraform') or shutil.which('terraform.exe')
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


def _strip_line_comments(line: str) -> str:
    """Remove HCL `#` and `//` comments without changing quoted string values."""
    in_string = escaped = False
    for index, character in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character == '#' or (character == '/' and line[index:index + 2] == '//'):
            return line[:index]
    return line


def _assignments(body: str) -> dict[str, list[str]]:
    """`name = value` lines with comments removed; every assignment of a name is kept, so a duplicate is visible."""
    found: dict[str, list[str]] = {}
    for line in body.splitlines():
        line = _strip_line_comments(line).strip()
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*) = (.+?),?', line)
        if match:
            found.setdefault(match.group(1), []).append(match.group(2).strip())
    return found


def _direct_assignments(body: str) -> dict[str, list[str]]:
    """Assignments at this block level, excluding assignments in child blocks."""
    found: dict[str, list[str]] = {}
    depth = 0
    for line in body.splitlines():
        line = _strip_line_comments(line).strip()
        if depth == 0:
            match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*) = (.+?),?', line)
            if match:
                found.setdefault(match.group(1), []).append(match.group(2).strip())
        depth += line.count('{') - line.count('}')
    return found


def _child_blocks(body: str, name: str) -> list[str]:
    """Return the bodies of named child HCL blocks without evaluating HCL."""
    found = []
    pattern = re.compile(r'^\s*'+re.escape(name)+r'\s*\{', re.MULTILINE)
    for match in pattern.finditer(body):
        found.append(normalize(body[match.end():_close(body, match.end())]))
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


def data_blocks() -> dict[str, dict]:
    """Extract all data blocks so role bindings cannot hide in another data source."""
    return _blocks(re.compile(r'^data\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE))


def _has_child_blocks(body: str) -> bool:
    """Whether a block body contains any direct child block, rather than an object assignment."""
    return bool(re.search(r'^\s*[A-Za-z_][A-Za-z0-9_]*(?:\s+"[^"\\]*")*\s*\{', body, re.MULTILINE))


def _references_ebs_csi_role(body: str) -> bool:
    """Identify a real EBS CSI role reference, including its literal generated name."""
    uncommented = '\n'.join(_strip_line_comments(line) for line in body.splitlines())
    return bool(re.search(r'\baws_iam_role\.ebs_csi(?:\.[A-Za-z_][A-Za-z0-9_]*)?\b', uncommented)
                or '-ebs-csi"' in uncommented)


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
        ('skip_final_snapshot = var.database_skip_final_snapshot', 'teardown must retain a final snapshot by default'),
    ):
        if requirement not in database:
            raise ValueError(message)
    variables = _blocks(re.compile(r'^variable\s+"([^"]+)"\s*\{', re.MULTILINE))
    for name, expected in (('database_skip_final_snapshot', 'false'), ('database_deletion_protection', 'true'),
                           ('operator_deletion_protection', 'true')):
        if _assignments(variables[name]['body']).get('default') != [expected]:
            raise ValueError(f'the teardown setting {name} must default to {expected}')
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

    # Checking the primary block alone would allow an extra, unguarded registry
    # (including a public registry) or an unrelated expiry policy beside it.
    for prefix, expected in (
        ('aws_ecr_repository.', {'aws_ecr_repository.images'}),
        ('aws_ecr_public_repository.', set()),
        ('aws_ecr_lifecycle_policy.', {'aws_ecr_lifecycle_policy.images'}),
    ):
        if {key for key in blocks if key.startswith(prefix)} != expected:
            raise ValueError('the image registry must contain only the declared private repositories and lifecycle policies')
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
    for name, value in (
        ('for_each', 'aws_ecr_repository.images'), ('repository', 'each.value.name'),
        ('tagStatus', '"untagged"'), ('countType', '"sinceImagePushed"'),
        ('countUnit', '"days"'), ('countNumber', '14'),
        ('action', '{ type = "expire" }'),
    ):
        if lifecycle.get(name) != [value]:
            raise ValueError('only untagged layers in the declared repositories may expire, after 14 days')
    if any(key.startswith('aws_ecr_repository_policy.') or key.startswith('aws_ecr_registry_policy.') for key in blocks):
        raise ValueError('the image registry must not carry a resource policy that could grant outside access')
    checks.append('image registry: two declared repositories, immutable, scanned, environment KMS key, untagged-only 14-day expiry, no resource policy')
    return checks


def assert_ebs_csi_scoped(blocks: dict[str, dict], documents: dict[str, dict]) -> list[str]:
    """Accept only the EBS CSI role, policy, trust, and add-on bindings described here."""
    expected_role_references = {
        'aws_iam_role.ebs_csi',
        'aws_iam_role_policy_attachment.ebs_csi',
        'aws_iam_role_policy.ebs_csi_kms',
        'aws_eks_addon.ebs_csi',
    }
    indirect = sorted(key for key, block in blocks.items()
                      if (block['kind'] or '').startswith(('aws_iam_', 'aws_eks_pod_identity'))
                      and any(re.search(r'\b(?:local|var)\.', value)
                              for field in ('role', 'role_name', 'roles', 'role_arn')
                              for value in _direct_assignments(block['body']).get(field, [])))
    if indirect:
        raise ValueError('IAM role bindings must name their role directly, not through local./var.: '
                         + ', '.join(indirect) + ' (keeps the EBS CSI role reference check sound)')
    all_blocks = {**data_blocks(), **blocks}
    all_blocks.update({f'aws_iam_policy_document.{name}': document
                       for name, document in documents.items()})
    role_references = {key for key, block in all_blocks.items()
                       if _references_ebs_csi_role(block['body'])}
    forbidden_bindings = {
        block['kind'] for key, block in blocks.items()
        if key in role_references and block['kind'] in (
            'aws_iam_policy_attachment', 'aws_iam_role_policy_attachments_exclusive',
            'aws_iam_role_policies_exclusive', 'aws_eks_pod_identity_association',
        )
    }
    if forbidden_bindings:
        raise ValueError('the EBS CSI role must not be bound by '+', '.join(sorted(forbidden_bindings)))
    if role_references != expected_role_references:
        raise ValueError('the EBS CSI role must have exactly one inline policy, one reviewed managed attachment, and one add-on association')

    role = blocks.get('aws_iam_role.ebs_csi')
    expected_role = {
        'name': ['"${var.name_prefix}-ebs-csi"'],
        'assume_role_policy': ['data.aws_iam_policy_document.ebs_csi_pod_identity.json'],
        'tags': ['{ Workload = "ebs-csi-driver" }'],
    }
    if (role is None or _direct_assignments(role['body']) != expected_role
            or _has_child_blocks(role['body'])):
        raise ValueError('the EBS CSI role definition may contain only its name, scoped trust policy, and tags')

    for name in ('ebs_csi_kms', 'ebs_csi_pod_identity'):
        document = documents.get(name)
        if document is None or _direct_assignments(document['body']) != {}:
            raise ValueError('the EBS CSI policy documents may not have top-level assignments')
        if re.search(r'\bdynamic\b', document['body']):
            raise ValueError('the EBS CSI policy documents may not use dynamic blocks')

    managed = [(key, block) for key, block in blocks.items()
               if block['kind'] == 'aws_iam_role_policy_attachment'
               and _direct_assignments(block['body']).get('role') == ['aws_iam_role.ebs_csi.name']]
    expected_managed = {
        'role': ['aws_iam_role.ebs_csi.name'],
        'policy_arn': ['"arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2"'],
    }
    if len(managed) != 1 or managed[0][0] != 'aws_iam_role_policy_attachment.ebs_csi':
        raise ValueError('the EBS CSI role must have exactly one managed policy attachment')
    if _direct_assignments(managed[0][1]['body']) != expected_managed:
        raise ValueError('the EBS CSI role must use AmazonEBSCSIDriverPolicyV2, not the legacy account-wide policy')

    inline = [(key, block) for key, block in blocks.items()
              if block['kind'] == 'aws_iam_role_policy'
              and _direct_assignments(block['body']).get('role') == ['aws_iam_role.ebs_csi.id']]
    expected_inline = {
        'name': ['"ebs-csi-kms"'],
        'role': ['aws_iam_role.ebs_csi.id'],
        'policy': ['data.aws_iam_policy_document.ebs_csi_kms.json'],
    }
    if len(inline) != 1 or inline[0][0] != 'aws_iam_role_policy.ebs_csi_kms':
        raise ValueError('the EBS CSI role must have exactly one inline policy')
    if _direct_assignments(inline[0][1]['body']) != expected_inline:
        raise ValueError('the EBS CSI inline policy must be ebs_csi_kms')

    trust = documents.get('ebs_csi_pod_identity')
    statements = [] if trust is None else _child_blocks(trust['body'], 'statement')
    expected_trust = {
        'effect': ['"Allow"'],
        'actions': ['["sts:AssumeRole", "sts:TagSession"]'],
    }
    if len(statements) != 1 or _direct_assignments(statements[0]) != expected_trust:
        raise ValueError('the EBS CSI pod identity trust must contain only the expected statement')
    principals = _child_blocks(statements[0], 'principals')
    if len(principals) != 1 or _direct_assignments(principals[0]) != {
            'type': ['"Service"'], 'identifiers': ['["pods.eks.amazonaws.com"]']}:
        raise ValueError('the EBS CSI pod identity trust must name only pods.eks.amazonaws.com')
    expected_conditions = {tuple(sorted(condition)) for condition in (
        (('test', ('"StringEquals"',)), ('variable', ('"aws:SourceAccount"',)),
         ('values', ('[data.aws_caller_identity.current.account_id]',))),
        (('test', ('"ArnEquals"',)), ('variable', ('"aws:SourceArn"',)),
         ('values', ('[aws_eks_cluster.main.arn]',))),
    )}
    conditions = _child_blocks(statements[0], 'condition')
    actual_conditions = {tuple(sorted((key, tuple(values))
                                      for key, values in _direct_assignments(condition).items()))
                         for condition in conditions}
    if len(conditions) != 2 or actual_conditions != expected_conditions:
        raise ValueError('the EBS CSI pod identity trust must be bound only to this cluster and account')

    kms = documents.get('ebs_csi_kms')
    statements = [] if kms is None else _child_blocks(kms['body'], 'statement')
    expected_kms = (
        ({
            'sid': ['"GrantsForEbsOnly"'], 'effect': ['"Allow"'],
            'actions': ['["kms:CreateGrant", "kms:ListGrants", "kms:RevokeGrant"]'],
            'resources': ['[aws_kms_key.main.arn]'],
        }, {
            'test': ['"Bool"'], 'variable': ['"kms:GrantIsForAWSResource"'], 'values': ['["true"]'],
        }),
        ({
            'sid': ['"UseKeyThroughEc2"'], 'effect': ['"Allow"'],
            'actions': ['["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]'],
            'resources': ['[aws_kms_key.main.arn]'],
        }, {
            'test': ['"StringEquals"'], 'variable': ['"kms:ViaService"'],
            'values': ['["ec2.${var.region}.amazonaws.com"]'],
        }),
    )
    if len(statements) != len(expected_kms):
        raise ValueError('the EBS CSI KMS permissions must contain exactly two statements')
    for statement, (expected_assignments, expected_condition) in zip(statements, expected_kms):
        conditions = _child_blocks(statement, 'condition')
        if (_direct_assignments(statement) != expected_assignments or len(conditions) != 1
                or _direct_assignments(conditions[0]) != expected_condition):
            raise ValueError('the EBS CSI KMS permissions must be limited to the environment key and EC2 grants')

    addon = blocks.get('aws_eks_addon.ebs_csi')
    if addon is None or _direct_assignments(addon['body']).get('cluster_name') != ['aws_eks_cluster.main.name']:
        raise ValueError('the EBS CSI add-on must be bound to this cluster')
    addon_assignments = _direct_assignments(addon['body'])
    if (addon_assignments.get('addon_name') != ['"aws-ebs-csi-driver"']
            or addon_assignments.get('addon_version') != ['var.ebs_csi_addon_version']):
        raise ValueError('the EBS CSI add-on must use its named, pinned version variable')
    associations = _child_blocks(addon['body'], 'pod_identity_association')
    if len(associations) != 1 or _direct_assignments(associations[0]) != {
            'service_account': ['"ebs-csi-controller-sa"'], 'role_arn': ['aws_iam_role.ebs_csi.arn']}:
        raise ValueError('the EBS CSI add-on must have exactly one controller pod identity association')
    return ['EBS CSI role: V2 managed policy only, pod identity bound to this cluster, KMS only via EC2 grants']


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


def assert_operator_identity(blocks: dict[str, dict]) -> list[str]:
    """Keep the optional provider operator-only and its outputs public-only."""
    expected = {
        'aws_cognito_user_pool.operators', 'aws_cognito_resource_server.operator_api',
        'aws_cognito_user_pool_client.operator', 'aws_cognito_user_pool_domain.operator',
        'aws_cognito_user_pool_client.grafana',
    }
    if {key for key in blocks if key.startswith('aws_cognito_')} != expected:
        raise ValueError('operator identity permits only the declared pool, read/proposal-approval scopes, clients and domain; no producer client or enrolled user')
    variables = _blocks(re.compile(r'^variable\s+"([^"]+)"\s*\{', re.MULTILINE))
    if _assignments(variables['operator_identity_enabled']['body']).get('default') != ['false']:
        raise ValueError('operator identity must be disabled by default')
    for key in expected:
        gate = ('var.operator_identity_enabled && var.edge_enabled ? 1 : 0'
                if key == 'aws_cognito_user_pool_client.grafana' else 'var.operator_identity_enabled ? 1 : 0')
        if _assignments(blocks[key]['body']).get('count') != [gate]:
            raise ValueError('every operator identity resource must be gated by operator_identity_enabled')
    pool = _assignments(blocks['aws_cognito_user_pool.operators']['body'])
    if (pool.get('mfa_configuration') != ['"ON"']
            or pool.get('allow_admin_create_user_only') != ['true']
            or pool.get('enabled') != ['true']):
        raise ValueError('operator identity must retain mandatory software-token MFA and admin-only enrollment')
    resource = blocks['aws_cognito_resource_server.operator_api']['body']
    expected_scopes = [
        {'scope_name': ['"incidents.read"'],
         'scope_description': ['"Read incident, explanation and action observations; no writes or dispatch"']},
        {'scope_name': ['"remediation.approve"'],
         'scope_description': ['"Approve an already proposed runbook proposal; no arbitrary dispatch; all executor guards still apply"']},
    ]
    scopes = [_assignments(scope) for scope in _child_blocks(resource, 'scope')]
    if (_direct_assignments(resource).get('identifier') != ['"sentinelops"']
            or len(scopes) != len(expected_scopes)
            or any(scope not in scopes for scope in expected_scopes)
            or re.search(r'\bdynamic\s+"scope"', resource)):
        raise ValueError('operator resource server must define exactly incidents.read and remediation.approve; no arbitrary dispatch, all executor guards still apply')
    client = _assignments(blocks['aws_cognito_user_pool_client.operator']['body'])
    for name, value in (
        ('generate_secret', 'false'),
        ('allowed_oauth_flows', '["code"]'),
        ('allowed_oauth_scopes', '["openid", "sentinelops/incidents.read", "sentinelops/remediation.approve"]'),
        ('explicit_auth_flows', '["ALLOW_REFRESH_TOKEN_AUTH"]'),
    ):
        if client.get(name) != [value]:
            raise ValueError('operator client must use code flow with exactly openid/read/proposal-approval scopes, with no client secret or producer flow')

    grafana = _assignments(blocks['aws_cognito_user_pool_client.grafana']['body'])
    for name, value in (
        ('generate_secret', 'true'), ('allowed_oauth_flows', '["code"]'),
        ('allowed_oauth_scopes', '["openid", "email", "profile"]'),
        ('explicit_auth_flows', '["ALLOW_REFRESH_TOKEN_AUTH"]'),
        ('callback_urls', '["https://${aws_cloudfront_distribution.edge[0].domain_name}/grafana/login/generic_oauth"]'),
    ):
        if grafana.get(name) != [value]:
            raise ValueError('Grafana must use a separate confidential code/PKCE client with only identity scopes and its exact edge callback')
    if client.get('callback_urls') != ['var.edge_enabled ? ["https://${aws_cloudfront_distribution.edge[0].domain_name}/ui/callback"] : var.operator_callback_urls']:
        raise ValueError('edge UI callback must bind exactly to its CloudFront domain')

    # Exact public expressions also reject exporting a whole resource object or
    # a newly added field under an innocuous name. No secret-value evaluation.
    public_values = {
        'operator_oidc_binding': '''var.operator_identity_enabled ? {
          issuer = "https://${aws_cognito_user_pool.operators[0].endpoint}"
          audience = aws_cognito_user_pool_client.operator[0].id
          jwks_url = "https://${aws_cognito_user_pool.operators[0].endpoint}/.well-known/jwks.json"
          audience_claim = "client_id"
          operator_scope = "sentinelops/incidents.read"
          approval_scope = "sentinelops/remediation.approve"
        } : null''',
        'operator_login_domain': 'var.operator_identity_enabled ? "https://${aws_cognito_user_pool_domain.operator[0].domain}.auth.${var.region}.amazoncognito.com" : null',
        'edge_grafana_client_id': 'var.operator_identity_enabled && var.edge_enabled ? aws_cognito_user_pool_client.grafana[0].id : null',
    }
    outputs = _blocks(re.compile(r'^output\s+"([^"]+)"\s*\{', re.MULTILINE))
    identity_outputs = {name: block for name, block in outputs.items()
                        if block['file'] == 'operator_identity.tf' or name.startswith('operator_')
                        or 'aws_cognito_' in block['body']}
    if set(identity_outputs) != set(public_values):
        raise ValueError('operator outputs must contain only the declared public OIDC binding and login domain; no credentials')
    for name, expected_value in public_values.items():
        value = re.search(r'^\s*value = (.+)\Z', identity_outputs[name]['body'], re.MULTILINE | re.DOTALL)
        if value is None or re.sub(r'\s+', '', value.group(1)) != re.sub(r'\s+', '', expected_value):
            raise ValueError('operator outputs must contain only public identifiers and URLs, null when disabled; no credentials')
    return ['operator identity default-off, gated, operator-only, no enrolled users; UI public with read/proposal-approval scopes, Grafana confidential with only identity scopes, public-only outputs']


def assert_edge(blocks: dict[str, dict]) -> list[str]:
    """Fail closed on the opt-in CloudFront -> private ALB -> two NodePorts edge."""
    def require(key: str, field: str, value: str) -> None:
        if _assignments(blocks[key]['body']).get(field) != [value]:
            raise ValueError(f'edge invariant: {key} requires {field} = {value}')

    variables = _blocks(re.compile(r'^variable\s+"([^"]+)"\s*\{', re.MULTILINE))
    for name in ('edge_enabled', 'edge_vpc_origin_bootstrap', 'edge_waf_enabled'):
        if _assignments(variables[name]['body']).get('default') != ['false']:
            raise ValueError('edge, bootstrap and WAF must be disabled by default')
    require('aws_lb.edge', 'internal', 'true')
    require('aws_lb.edge', 'subnets', 'aws_subnet.private[*].id')
    require('aws_lb.edge', 'security_groups', '[aws_security_group.edge_alb[0].id]')
    require('aws_lb.edge', 'count', 'var.edge_enabled ? 1 : 0')
    require('aws_lb.edge', 'condition', 'var.operator_identity_enabled')
    for kind, expected in (
        ('aws_cloudfront_distribution.', {'aws_cloudfront_distribution.edge'}),
        ('aws_cloudfront_vpc_origin.', {'aws_cloudfront_vpc_origin.edge'}),
        ('aws_cloudfront_function.', {'aws_cloudfront_function.edge_paths'}),
        ('aws_lb_target_group.', {'aws_lb_target_group.edge'}),
    ):
        if {key for key in blocks if key.startswith(kind)} != expected:
            raise ValueError('edge permits only its declared private origin and filtered distribution')
    require('aws_lb_target_group.edge', 'target_type', '"instance"')
    require('aws_lb_target_group.edge', 'for_each', 'var.edge_enabled ? local.edge_backends : {}')
    require('aws_lb_target_group.edge', 'port', 'each.value.port')
    require('aws_lb_target_group.edge', 'path', 'each.value.health')
    require('aws_autoscaling_attachment.edge', 'autoscaling_group_name',
            'one(aws_eks_node_group.main.resources[0].autoscaling_groups).name')
    require('aws_autoscaling_attachment.edge', 'lb_target_group_arn', 'aws_lb_target_group.edge[each.key].arn')
    for key, block in blocks.items():
        body = block['body']
        if key.startswith('aws_lb.') and key != 'aws_lb.edge':
            raise ValueError('edge permits only the declared internal ALB')
        if key.startswith(('aws_lb_listener.', 'aws_lb_listener_rule.')) and key not in {
                'aws_lb_listener.edge', 'aws_lb_listener_rule.edge_deny',
                'aws_lb_listener_rule.edge_allow_grafana_jaeger_module', 'aws_lb_listener_rule.edge_allow'}:
            raise ValueError('edge permits only the declared deny/default and allow path rules')
        if key.startswith(('aws_vpc_security_group_ingress_rule.', 'aws_security_group_rule.')):
            assignments = _assignments(body)
            target = assignments.get('security_group_id', [])
            if 'aws_security_group.edge_alb[0].id' in target:
                if key not in {'aws_vpc_security_group_ingress_rule.edge_from_cloudfront',
                               'aws_vpc_security_group_ingress_rule.edge_bootstrap'}:
                    raise ValueError('edge ALB ingress must come only from the CloudFront VPC origin')
                if any(name in assignments for name in ('cidr_ipv4', 'cidr_ipv6', 'cidr_blocks', 'ipv6_cidr_blocks')):
                    raise ValueError('edge ALB must not admit any CIDR (including 0.0.0.0/0)')
        if key == 'aws_security_group.edge_alb' and _child_blocks(body, 'ingress'):
            raise ValueError('edge ALB ingress must use only the reviewed standalone rules')
    for key in ('aws_vpc_security_group_ingress_rule.edge_from_cloudfront',
                'aws_vpc_security_group_ingress_rule.edge_bootstrap'):
        require(key, 'security_group_id', 'aws_security_group.edge_alb[0].id')
        for field, value in (('ip_protocol', '"tcp"'), ('from_port', '80'), ('to_port', '80')):
            require(key, field, value)
    require('aws_vpc_security_group_ingress_rule.edge_from_cloudfront', 'referenced_security_group_id',
            'data.aws_security_group.edge_cloudfront[0].id')
    require('aws_vpc_security_group_ingress_rule.edge_from_cloudfront', 'count',
            'var.edge_enabled && !var.edge_vpc_origin_bootstrap ? 1 : 0')
    require('aws_vpc_security_group_ingress_rule.edge_bootstrap', 'prefix_list_id',
            'data.aws_ec2_managed_prefix_list.edge_cloudfront[0].id')
    require('aws_vpc_security_group_ingress_rule.edge_bootstrap', 'count',
            'var.edge_enabled && var.edge_vpc_origin_bootstrap ? 1 : 0')
    data = data_blocks()
    if _assignments(data['aws_ec2_managed_prefix_list.edge_cloudfront']['body']).get('name') != ['"com.amazonaws.global.cloudfront.origin-facing"']:
        raise ValueError('edge bootstrap must use only the AWS managed CloudFront prefix list')
    filters = [_assignments(body) for body in _child_blocks(data['aws_security_group.edge_cloudfront']['body'], 'filter')]
    if filters != [{'name': ['"group-name"'], 'values': ['["CloudFront-VPCOrigins-Service-SG"]']},
                   {'name': ['"vpc-id"'], 'values': ['[aws_vpc.main.id]']}]:
        raise ValueError('edge ingress must name the service-managed VPC origin SG in this VPC')
    listener = blocks['aws_lb_listener.edge']['body']
    require('aws_lb_listener.edge', 'type', '"fixed-response"')
    require('aws_lb_listener.edge', 'status_code', '"403"')
    require('aws_lb_listener_rule.edge_deny', 'priority', '2')
    require('aws_lb_listener_rule.edge_deny', 'type', '"fixed-response"')
    require('aws_lb_listener_rule.edge_deny', 'status_code', '"403"')
    require('aws_lb_listener_rule.edge_deny', 'regex_values',
            '["^/(.*/)?(internal|docs|redoc|openapi[.]json|metrics|prometheus|jaeger)(/.*)?$"]')
    require('aws_lb_listener_rule.edge_allow_grafana_jaeger_module', 'priority', '1')
    require('aws_lb_listener_rule.edge_allow_grafana_jaeger_module', 'count', 'var.edge_enabled ? 1 : 0')
    require('aws_lb_listener_rule.edge_allow_grafana_jaeger_module', 'type', '"forward"')
    require('aws_lb_listener_rule.edge_allow_grafana_jaeger_module', 'target_group_arn',
            'aws_lb_target_group.edge["grafana"].arn')
    require('aws_lb_listener_rule.edge_allow_grafana_jaeger_module', 'regex_values',
            '["^/grafana/public/plugins/jaeger/(module[.]js|plugin[.]json|img/[^/]+[.](svg|png))$"]')
    jaeger_path = _child_blocks(blocks['aws_lb_listener_rule.edge_allow_grafana_jaeger_module']['body'], 'condition')
    if not any(_assignments(condition).get('values') == ['["GET", "HEAD"]']
               for condition in jaeger_path if 'http_request_method' in condition):
        raise ValueError('only GET/HEAD may load Grafana Jaeger plugin assets')
    require('aws_lb_listener_rule.edge_allow', 'for_each', 'var.edge_enabled ? local.edge_paths : {}')
    require('aws_lb_listener_rule.edge_allow', 'type', '"forward"')
    require('aws_lb_listener_rule.edge_allow', 'values', 'each.value')
    require('aws_lb_listener_rule.edge_allow', 'target_group_arn', 'aws_lb_target_group.edge[each.key].arn')
    edge = normalize((ROOT/'edge.tf').read_text())
    paths = re.search(r'edge_paths = \{([^}]+)\}', edge)
    if paths is None or _assignments(paths.group(1)) != {
            'api': ['["/ui", "/ui/*", "/v1/*"]'], 'grafana': ['["/grafana", "/grafana/*"]']}:
        raise ValueError('edge allow paths must be exactly UI, v1 API and Grafana')
    backends = re.search(r'edge_backends = \{(.*?)\n\s*\}', edge, re.DOTALL)
    if backends is None or _assignments(backends.group(1)) != {
            'api': ['{ port = var.edge_api_node_port, health = "/health" }'],
            'grafana': ['{ port = var.edge_grafana_node_port, health = "/grafana/api/health" }']}:
        raise ValueError('edge must bind only its dedicated API/UI and Grafana NodePorts')
    distribution = blocks['aws_cloudfront_distribution.edge']['body']
    require('aws_cloudfront_distribution.edge', 'enabled', '!var.edge_vpc_origin_bootstrap')
    origins = _child_blocks(distribution, 'origin')
    if len(origins) != 1 or any(kind in origins[0] for kind in ('custom_origin_config', 's3_origin_config', 'origin_path')):
        raise ValueError('edge CloudFront must use only its private VPC origin')
    origin_config = _child_blocks(origins[0], 'vpc_origin_config')
    if len(origin_config) != 1 or _assignments(origin_config[0]).get('vpc_origin_id') != ['aws_cloudfront_vpc_origin.edge[0].id']:
        raise ValueError('edge CloudFront must bind its internal ALB VPC origin')
    for field, expected in (
        ('viewer_protocol_policy', ['"https-only"', '"https-only"']),
        ('cache_policy_id', ['local.edge_cache_policy_id', 'local.edge_cache_policy_id']),
        ('origin_request_policy_id', ['local.edge_origin_request_policy_id', 'local.edge_origin_request_policy_id']),
        ('function_arn', ['aws_cloudfront_function.edge_paths[0].arn', 'aws_cloudfront_function.edge_paths[0].arn']),
        ('for_each', ['["/v1/*", "/ui/*", "/grafana/*"]', '[400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504]']),
    ):
        if _assignments(distribution).get(field) != expected:
            raise ValueError('edge CloudFront requires HTTPS-only, disabled caching and path filtering on every behavior')
    require('aws_cloudfront_distribution.edge', 'error_caching_min_ttl', '0')
    require('aws_cloudfront_distribution.edge', 'error_code', 'custom_error_response.value')
    require('aws_cloudfront_response_headers_policy.edge', 'header', '"Cache-Control"')
    require('aws_cloudfront_response_headers_policy.edge', 'value', '"no-store"')
    if _locals().get('edge_cache_policy_id') != ['"4135ea2d-6df8-44a3-9df3-4b5a84be39ad"']:
        raise ValueError('edge must use the AWS managed CachingDisabled policy')
    if _locals().get('edge_origin_request_policy_id') != ['"216adef6-5c7f-47e4-b989-5492eafa07d3"']:
        raise ValueError('edge must forward viewer Host, Authorization, cookies and OAuth query parameters')
    # Function source is tested with real JS too; keep its security boundary
    # exact so an inserted early return cannot bypass its rejection conditions.
    function = blocks['aws_cloudfront_function.edge_paths']['body']
    code = re.search(r'code = <<-JS\n(.*?)\n\s*JS', function, re.DOTALL)
    import hashlib
    if code is None or hashlib.sha256(''.join(
            _strip_line_comments(line).strip() for line in code.group(1).splitlines()).encode()).hexdigest() != EDGE_FUNCTION_SHA256:
        raise ValueError('edge path function must reject traversal, encoded paths and all non-allowlisted routes')
    if 'default_action' not in listener:
        raise ValueError('edge listener must deny by default')
    return ['edge default-off; private ALB, VPC-origin-only ingress, HTTPS-only uncached allowlisted routes; NodePort/ASG binding']


EDGE_FUNCTION_SHA256 = 'b62066fb57afd6aa0f9cc0f66d36be7d3f9037cef0b19780058675849f9efe42'


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
    checks += assert_ebs_csi_scoped(blocks, documents)
    checks += assert_no_static_credentials()
    checks += assert_operator_identity(blocks)
    checks += assert_edge(blocks)
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
