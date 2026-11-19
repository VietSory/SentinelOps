"""Static guards for the AWS dev demo runner and its fault credential."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / 'scripts/aws_demo'
RUNNER = (ROOT / 'demo-runner.yaml').read_text()
FAULT = (ROOT / 'demo-fault-rbac.yaml').read_text()


def runner():
    return yaml.safe_load(RUNNER)


def test_runner_follows_the_current_health_epoch_not_a_pinned_one():
    # A pinned stream/root goes stale on every epoch migration: the driver would PATCH
    # the regression while the incident worker ignores its receipts.
    assert not re.search(r'\b[0-9a-f]{64}\b', RUNNER.split('apiVersion:', 1)[1].replace(
        'bb40c73ad3c17e585e36f6e49d99f491371e971b3f730ee9d0d341de949a4859', ''))
    env = {e['name']: e for e in runner()['spec']['template']['spec']['containers'][0]['env']}
    assert env['HEALTH_STREAM']['valueFrom'] == {'configMapKeyRef': {
        'name': 'sentinelops-workers-runtime', 'key': 'SENTINELOPS_HEALTH_STREAM'}}
    assert env['HEALTH_ROOT']['value'] == '__HEALTH_ROOT__'
    header = RUNNER.split('apiVersion:', 1)[0]
    assert '--source-store "$HEALTH_ROOT"' in header and '--source-store /artifacts/health ' not in header
    assert 's#__HEALTH_ROOT__#$ROOT#' in header and 'ROOT=$(arg --source-store)' in header
    assert 'case "$HEALTH_ROOT" in /artifacts/health*)' in header


def test_runner_is_a_bounded_job_pinned_to_the_controller_node():
    job = runner()
    assert job['kind'] == 'Job'
    assert job['spec']['backoffLimit'] == 0 and job['spec']['ttlSecondsAfterFinished'] == 0
    pod = job['spec']['template']['spec']
    assert job['spec']['activeDeadlineSeconds'] == pod['activeDeadlineSeconds'] == 14400
    assert pod['automountServiceAccountToken'] is False and pod['restartPolicy'] == 'Never'
    term, = pod['affinity']['podAffinity']['requiredDuringSchedulingIgnoredDuringExecution']
    assert term == {'labelSelector': {'matchLabels': {'app': 'remediation-controller'}},
                    'topologyKey': 'kubernetes.io/hostname'}
    env = {e['name']: e for e in pod['containers'][0]['env']}
    assert env['SENTINELOPS_ACTION_READ_DATABASE_URL']['valueFrom']['secretKeyRef']['name'] == 'remediation-read'
    assert env['PGSSLROOTCERT']['value'] == env['SSL_CERT_FILE']['value']


def test_cleanup_revokes_the_reader_login_and_its_secret():
    header = RUNNER.split('apiVersion:', 1)[0]
    assert 'control.py disable-read' in header and 'delete secret/remediation-read' in header


def test_fault_credential_is_minimal_and_owned_by_the_runner_job():
    documents = list(yaml.safe_load_all(FAULT))
    assert [d['kind'] for d in documents] == ['ServiceAccount', 'Role', 'RoleBinding']
    for document in documents:
        owner, = document['metadata']['ownerReferences']
        assert owner == {'apiVersion': 'batch/v1', 'kind': 'Job', 'name': 'demo-runner', 'uid': '__RUNNER_JOB_UID__'}
    role = documents[1]
    assert role['rules'] == [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['list']},
                             {'apiGroups': [''], 'resources': ['pods/proxy'], 'verbs': ['get', 'create']}]
    assert documents[0]['automountServiceAccountToken'] is False
