"""Guards for the AWS baseline description. No terraform binary or AWS needed.

Each test mutates a copy of the real configuration and asserts the corresponding
guard rejects it. A guard that cannot fail would not be a guard.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from scripts.check_aws_iac import (
    ROOT, assert_bounded_retention, assert_grants_no_authority, assert_no_overbroad_identity,
    assert_no_static_credentials, assert_private_by_default, normalize, policy_documents, resources)

BLOCKS = resources()
DOCUMENTS = policy_documents()


@pytest.fixture
def copied(tmp_path):
    destination = tmp_path/'aws'
    destination.mkdir()
    for path in ROOT.glob('*.tf'):
        shutil.copy(path, destination/path.name)
    return destination


def mutate(key, old, new, blocks=None):
    """Return the real block set with one requirement broken."""
    changed = dict(blocks or BLOCKS)
    body = changed[key]['body']
    assert old in body, 'fixture drifted from the configuration: '+old
    changed[key] = {**changed[key], 'body': body.replace(old, new)}
    return changed


def test_real_configuration_satisfies_every_guard():
    checks = (assert_private_by_default(BLOCKS) + assert_bounded_retention(BLOCKS)
              + assert_no_overbroad_identity(BLOCKS, DOCUMENTS) + assert_no_static_credentials()
              + assert_grants_no_authority())
    assert len(checks) == 14


def test_expected_surface_is_described():
    """A silently shrinking configuration would pass vacuous guards."""
    for key in ('aws_eks_cluster.main', 'aws_eks_node_group.main', 'aws_db_instance.control',
                'aws_s3_bucket.artifacts', 'aws_kms_key.main', 'aws_iam_openid_connect_provider.cluster',
                'aws_budgets_budget.monthly', 'aws_flow_log.vpc', 'aws_vpc_endpoint.s3',
                'aws_ecr_repository.images', 'aws_ecr_lifecycle_policy.images'):
        assert key in BLOCKS, 'missing described resource: '+key
    for role in ('telemetry_reader', 'data_worker', 'explanation', 'controller', 'api'):
        assert 'aws_iam_role.'+role in BLOCKS
        assert role in DOCUMENTS


def test_public_database_is_rejected():
    with pytest.raises(ValueError, match='publicly accessible'):
        assert_private_by_default(mutate('aws_db_instance.control',
                                         'publicly_accessible = false', 'publicly_accessible = true'))


def test_unencrypted_database_is_rejected():
    with pytest.raises(ValueError, match='encrypted at rest'):
        assert_private_by_default(mutate('aws_db_instance.control',
                                         'storage_encrypted = true', 'storage_encrypted = false'))


def test_skipping_the_final_snapshot_is_rejected():
    """Teardown must not silently discard IER revisions and audit history."""
    with pytest.raises(ValueError, match='final snapshot'):
        assert_private_by_default(mutate('aws_db_instance.control',
                                         'skip_final_snapshot = false', 'skip_final_snapshot = true'))


def test_private_cluster_endpoint_cannot_be_disabled():
    with pytest.raises(ValueError, match='private access'):
        assert_private_by_default(mutate('aws_eks_cluster.main',
                                         'endpoint_private_access = true', 'endpoint_private_access = false'))


def test_hardcoded_public_cluster_endpoint_is_rejected():
    """Public access must stay driven by the validated variable, not pinned true."""
    with pytest.raises(ValueError, match='explicit variable'):
        assert_private_by_default(mutate('aws_eks_cluster.main',
                                         'endpoint_public_access = var.cluster_public_access',
                                         'endpoint_public_access = true'))


def test_public_bucket_access_is_rejected():
    with pytest.raises(ValueError, match='public access'):
        assert_private_by_default(mutate('aws_s3_bucket_public_access_block.artifacts',
                                         'block_public_policy = true', 'block_public_policy = false'))


def test_public_ip_on_launch_is_rejected():
    with pytest.raises(ValueError, match='public addresses'):
        assert_private_by_default(mutate('aws_subnet.private',
                                         'map_public_ip_on_launch = false', 'map_public_ip_on_launch = true'))


def test_unbounded_log_retention_is_rejected():
    with pytest.raises(ValueError, match='bounded retention'):
        assert_bounded_retention(mutate('aws_cloudwatch_log_group.workloads',
                                        'retention_in_days = var.log_retention_days', 'retention_in_days = 0'))


def test_administrator_policy_attachment_is_rejected():
    blocks = mutate('aws_iam_role_policy_attachment.node_worker',
                    'AmazonEKSWorkerNodePolicy', 'AdministratorAccess')
    with pytest.raises(ValueError, match='over-broad managed policy'):
        assert_no_overbroad_identity(blocks, DOCUMENTS)


@pytest.mark.parametrize('action', ['iam:CreateRole', 'eks:*', 's3:DeleteBucket'])
def test_forbidden_workload_action_is_rejected(action):
    documents = dict(DOCUMENTS)
    body = documents['data_worker']['body']
    documents['data_worker'] = {**documents['data_worker'],
                                'body': body.replace('"s3:GetObject"', '"'+action+'"')}
    with pytest.raises(ValueError, match='forbidden action'):
        assert_no_overbroad_identity(BLOCKS, documents)


def test_wildcard_action_list_is_rejected():
    documents = dict(DOCUMENTS)
    body = documents['api']['body']
    documents['api'] = {**documents['api'], 'body': body.replace('actions = ["kms:Decrypt"]', 'actions = ["*"]')}
    with pytest.raises(ValueError, match='wildcard action list'):
        assert_no_overbroad_identity(BLOCKS, documents)


def test_controller_gaining_aws_mutation_is_rejected():
    """Rollback authority is Kubernetes RBAC; an AWS credential must not grant it."""
    documents = dict(DOCUMENTS)
    body = documents['controller']['body']
    documents['controller'] = {**documents['controller'],
                               'body': body.replace('"s3:GetObject"', '"s3:PutObject"')}
    with pytest.raises(ValueError, match='no AWS mutation authority'):
        assert_no_overbroad_identity(BLOCKS, documents)


def test_unbounded_bedrock_grant_is_rejected():
    documents = dict(DOCUMENTS)
    body = documents['explanation']['body']
    documents['explanation'] = {**documents['explanation'],
                                'body': body.replace('foundation-model/${id}', 'foundation-model/*')}
    with pytest.raises(ValueError, match='explicitly named models'):
        assert_no_overbroad_identity(BLOCKS, documents)


def test_bedrock_invoke_is_absent_until_models_are_named():
    """Default configuration grants no invoke permission at all."""
    variables = normalize((ROOT/'variables.tf').read_text())
    assert 'variable "bedrock_model_ids"' in variables
    block = variables.split('variable "bedrock_model_ids"')[1].split('\nvariable ')[0]
    assert 'default = []' in block
    policy = BLOCKS['aws_iam_role_policy.explanation']['body']
    assert 'count = length(var.bedrock_model_ids) > 0 ? 1 : 0' in policy


def test_irsa_trust_is_scoped_to_one_service_account():
    trust = DOCUMENTS['irsa']['body']
    assert 'system:serviceaccount:${local.namespace}:${each.key}' in trust
    assert 'sts.amazonaws.com' in trust
    assert 'AssumeRoleWithWebIdentity' in trust
    # A wildcard subject would let any pod assume any workload role.
    assert '*' not in trust.split(':sub')[1].split(']')[0]


def test_static_access_key_is_rejected(copied):
    (copied/'leak.tf').write_text('# aws_secret_access_key = "nope"\n')
    with pytest.raises(ValueError, match='static credential'):
        assert_no_static_credentials(copied)


def test_inline_database_password_is_rejected(copied):
    text = (copied/'storage.tf').read_text()
    (copied/'storage.tf').write_text(text.replace('username = "control_bootstrap"',
                                                  'username = "control_bootstrap"\n  password = "hunter2"'))
    with pytest.raises(ValueError, match='static credential'):
        assert_no_static_credentials(copied)


def test_terraform_managed_password_is_required(copied):
    text = (copied/'storage.tf').read_text()
    (copied/'storage.tf').write_text(text.replace('manage_master_user_password   = true',
                                                  'manage_master_user_password   = false'))
    with pytest.raises(ValueError, match='held by AWS'):
        assert_no_static_credentials(copied)


def test_state_backend_block_is_rejected(copied):
    text = (copied/'versions.tf').read_text()
    (copied/'versions.tf').write_text(text.replace('terraform {',
                                                   'terraform {\n  backend "s3" {\n    bucket = "x"\n  }', 1))
    with pytest.raises(ValueError, match='state backend'):
        assert_grants_no_authority(copied)


def test_a_comment_mentioning_backend_is_allowed(copied):
    """The real versions.tf documents `-backend=false`; that must not trip the guard."""
    assert 'backend' in (copied/'versions.tf').read_text()
    assert assert_grants_no_authority(copied)


@pytest.mark.parametrize('name', ['model_activation_allowed', 'remediation_approval'])
def test_claiming_authority_in_outputs_is_rejected(copied, name):
    text = (copied/'outputs.tf').read_text()
    block = text.split('output "'+name+'"')[1].split('\n}')[0]
    (copied/'outputs.tf').write_text(text.replace(block, block.replace('false', 'true')))
    with pytest.raises(ValueError, match=name+' must be false'):
        assert_grants_no_authority(copied)


def test_tfvars_example_is_tracked_but_real_tfvars_are_not():
    assert (ROOT/'terraform.tfvars.example').is_file()
    ignore = (Path(ROOT).parents[1]/'.gitignore').read_text()
    assert '*.tfvars' in ignore and '!*.tfvars.example' in ignore
    assert '.terraform/' in ignore and '*.tfstate' in ignore


def test_provider_lock_is_committed():
    """The lock file pins provider hashes; it is a supply-chain control."""
    assert (ROOT/'.terraform.lock.hcl').is_file()
    assert 'hashicorp/aws' in (ROOT/'.terraform.lock.hcl').read_text()


@pytest.mark.parametrize(('old', 'new', 'message'), [
    ('image_tag_mutability = "IMMUTABLE"', 'image_tag_mutability = "MUTABLE"', 'immutable'),
    ('scan_on_push = true', 'scan_on_push = false', 'scanned'),
    ('encryption_type = "KMS"', 'encryption_type = "AES256"', 'KMS'),
    ('kms_key = aws_kms_key.main.arn', 'kms_key = var.other_key', 'environment KMS key'),
    ('for_each = local.image_repositories', 'for_each = toset([])', 'declared repositories'),
    # A comment carrying the required text must not hide a weakened setting.
    ('scan_on_push = true', 'scan_on_push = false # scan_on_push = true', 'scanned'),
    ('scan_on_push = true', 'scan_on_push = true\n scan_on_push = false', 'scanned'),
])
def test_weakened_image_registry_is_rejected(old, new, message):
    with pytest.raises(ValueError, match=message):
        assert_private_by_default(mutate('aws_ecr_repository.images', old, new))


@pytest.mark.parametrize(('old', 'new'), [
    ('countNumber = 14', 'countNumber = 1'),
    ('tagStatus = "untagged"', 'tagStatus = "any"'),
])
def test_image_lifecycle_cannot_expire_release_images(old, new):
    with pytest.raises(ValueError, match='untagged'):
        assert_private_by_default(mutate('aws_ecr_lifecycle_policy.images', old, new))


def test_changed_repository_set_is_rejected(copied, monkeypatch):
    import scripts.check_aws_iac as iac
    path = copied/'ecr.tf'
    path.write_text(path.read_text().replace('toset(["application", "control-plane"])', 'toset(["application"])'))
    monkeypatch.setattr(iac, 'ROOT', copied)
    with pytest.raises(ValueError, match='declared repositories'):
        assert_private_by_default(iac.resources())


def test_image_registry_resource_policy_is_rejected():
    blocks = {**BLOCKS, 'aws_ecr_repository_policy.images': {'body': 'policy = "{}"'}}
    with pytest.raises(ValueError, match='resource policy'):
        assert_private_by_default(blocks)
