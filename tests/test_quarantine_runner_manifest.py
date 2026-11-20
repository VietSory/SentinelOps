"""Static guards for the bounded AWS dev quarantine review Job."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import review_payment_quarantine

MANIFEST = Path(__file__).resolve().parents[1] / 'scripts/aws_demo/quarantine-runner.yaml'
TEXT = MANIFEST.read_text()


def manifest():
    return yaml.safe_load(TEXT)


def test_quarantine_runner_is_bounded_and_pinned_to_the_controller_node():
    job = manifest()
    assert job['kind'] == 'Job'
    assert job['spec']['backoffLimit'] == 0
    assert job['spec']['activeDeadlineSeconds'] == 7200
    assert job['spec']['ttlSecondsAfterFinished'] == 0
    pod = job['spec']['template']['spec']
    assert pod['activeDeadlineSeconds'] == 7200
    assert pod['restartPolicy'] == 'Never'
    assert pod['serviceAccountName'] == 'payment-quarantine-observer'
    assert pod['automountServiceAccountToken'] is False
    term, = pod['affinity']['podAffinity']['requiredDuringSchedulingIgnoredDuringExecution']
    assert term == {'labelSelector': {'matchLabels': {'app': 'remediation-controller'}},
                    'topologyKey': 'kubernetes.io/hostname'}


def test_quarantine_runner_uses_projected_private_observer_credentials_and_hardening():
    pod = manifest()['spec']['template']['spec']
    projected = next(v for v in pod['volumes'] if v['name'] == 'observer-token')['projected']
    token, cluster_ca = projected['sources']
    assert projected['defaultMode'] == 0o440
    assert token == {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 7200}}
    assert cluster_ca['configMap']['name'] == 'kube-root-ca.crt'
    credentials = next(v for v in pod['volumes'] if v['name'] == 'credentials')['emptyDir']
    assert credentials == {'medium': 'Memory', 'sizeLimit': '128Ki'}
    init, = pod['initContainers']
    assert 'os.umask(0o077)' in init['command'][-1] and 'os.chmod(target/name, 0o600)' in init['command'][-1]
    for container in [init, *pod['containers']]:
        security = container['securityContext']
        assert security['allowPrivilegeEscalation'] is False
        assert security['readOnlyRootFilesystem'] is True
        assert security['capabilities']['drop'] == ['ALL']
    assert pod['securityContext']['runAsNonRoot'] is True
    assert pod['securityContext']['runAsUser'] == 10001


def test_quarantine_runner_follows_live_health_epoch_and_separates_stores():
    pod = manifest()['spec']['template']['spec']
    runner, = pod['containers']
    env = {item['name']: item for item in runner['env']}
    assert env['HEALTH_STREAM']['valueFrom'] == {'configMapKeyRef': {
        'name': 'sentinelops-workers-runtime', 'key': 'SENTINELOPS_HEALTH_STREAM'}}
    assert env['SENTINELOPS_ENV']['valueFrom'] == {'configMapKeyRef': {
        'name': 'sentinelops-workers-runtime', 'key': 'SENTINELOPS_ENV'}}
    assert env['HEALTH_ROOT']['value'] == '__HEALTH_ROOT__'
    assert env['QUARANTINE_REVIEW_ROOT']['value'] == '/artifacts/remediation/quarantine-review'
    assert env['SENTINELOPS_KUBERNETES_REMOTE_SERVER']['value'] == 'https://kubernetes.default.svc'
    assert env['SENTINELOPS_QUARANTINE_DATABASE_URL']['valueFrom']['secretKeyRef'] == {
        'name': 'remediation-resolve', 'key': 'quarantine-database-url'}
    assert env['PGSSLROOTCERT']['value'] == env['SSL_CERT_FILE']['value']
    mounts = {item['name']: item for item in runner['volumeMounts']}
    assert mounts['health-artifacts']['readOnly'] is True
    assert mounts['review-artifacts']['readOnly'] is True
    assert mounts['quarantine-review'].get('readOnly') is not True
    claims = {item['name']: item['persistentVolumeClaim'] for item in pod['volumes']
              if 'persistentVolumeClaim' in item}
    assert claims['health-artifacts'] == {'claimName': 'health-artifacts', 'readOnly': True}
    assert claims['review-artifacts'] == {'claimName': 'review-artifacts', 'readOnly': True}
    assert claims['quarantine-review'] == {'claimName': 'remediation-artifacts'}


def test_quarantine_runner_header_has_open_release_and_scoped_cleanup_commands():
    header = TEXT.split('apiVersion:', 1)[0]
    for required in ('action-resolve', 'review_payment_quarantine.py',
                     'open --action-id', 'release \\', '--review-ref artifact://manifests/',
                     '--idempotency-key', '--source-store "$HEALTH_ROOT"',
                     '--review-store "$QUARANTINE_REVIEW_ROOT"',
                     'control.py disable-resolve', 'delete secret/remediation-resolve'):
        assert required in header
    assert 'sed "s#__HEALTH_ROOT__#$ROOT#"' in header


def test_quarantine_cli_accepts_only_explicit_remote_tls_and_environment(tmp_path, monkeypatch):
    source = tmp_path / 'health'
    source.mkdir()
    target = tmp_path / 'target.json'
    target.write_text(json.dumps({'cluster_uid': 'cluster', 'namespace': 'sentinelops-b-actions',
        'namespace_uid': 'namespace', 'deployment': 'payment-service',
        'deployment_uid': 'payment-uid', 'container': 'payment'}))
    args = SimpleNamespace(target=target, health_stream='a' * 64, source_store=source,
                           server='https://kubernetes.default.svc')
    monkeypatch.setenv('SENTINELOPS_ENV', 'dev')
    monkeypatch.setenv('SENTINELOPS_KUBERNETES_REMOTE_SERVER', args.server)
    selected, environment = review_payment_quarantine.validate_configuration(args,
        'postgresql://control_quarantine_resolver:secret@db.example.rds.amazonaws.com:5432/'
        'sentinelops_control?sslmode=verify-full')
    assert selected.deployment == 'payment-service' and environment == 'dev'
    with pytest.raises(ValueError, match='sslmode=verify-full'):
        review_payment_quarantine.validate_configuration(args,
            'postgresql://control_quarantine_resolver:secret@db.example.rds.amazonaws.com:5432/'
            'sentinelops_control')
