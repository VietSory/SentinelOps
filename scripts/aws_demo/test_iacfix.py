"""Offline regression checks for the five encrypted replacement artifact claims."""
from pathlib import Path

import yaml


PROJECT = Path(__file__).resolve().parents[2]
CLAIMS = (
    'health-artifacts', 'incident-artifacts', 'remediation-artifacts',
    'review-artifacts', 'deployment-change-artifacts',
)


def test_all_artifact_stores_have_distinct_replacement_claims():
    claims = list(yaml.safe_load_all(
        (PROJECT / 'scripts/aws_demo/artifact-pvcs-kms.yaml').read_text()))
    assert len(claims) == len(CLAIMS)
    assert {claim['metadata']['name'] for claim in claims} == {
        name + '-kms' for name in CLAIMS}
    for claim in claims:
        assert claim['apiVersion'] == 'v1'
        assert claim['kind'] == 'PersistentVolumeClaim'
        assert claim['metadata']['namespace'] == 'sentinelops-b-actions'
        assert claim['spec'] == {
            'accessModes': ['ReadWriteOnce'],
            'volumeMode': 'Filesystem',
            'storageClassName': 'sentinelops-gp3-kms',
            'resources': {'requests': {'storage': '5Gi'}},
        }


def test_replacement_class_requires_customer_kms_and_scheduled_binding():
    storage_class = yaml.safe_load((PROJECT / 'infra/kubernetes/'
        'sentinelops-gp3-kms.storageclass.yaml').read_text())
    assert storage_class['metadata']['name'] == 'sentinelops-gp3-kms'
    assert storage_class['provisioner'] == 'ebs.csi.aws.com'
    assert storage_class['parameters'] == {
        'type': 'gp3', 'encrypted': 'true',
        'kmsKeyId': '<environment-kms-key-arn>',
    }
    assert storage_class['volumeBindingMode'] == 'WaitForFirstConsumer'


def test_worker_mounts_can_switch_all_five_stores_together():
    """The replacement claims must work with the actual worker/controller renderer."""
    from scripts.render_worker_manifests import render

    documents = render(
        'registry.example.invalid/control-plane@sha256:' + 'a' * 64,
        environment='aws-dev',
        control_database='postgresql://control.example.invalid:5432/sentinelops_control?sslmode=verify-full',
        database_ca_config='control-rds-ca',
        health_store_claim='health-artifacts-kms',
        incident_store_claim='incident-artifacts-kms',
        remediation_store_claim='remediation-artifacts-kms',
        known_good_store_claim='review-artifacts-kms',
        deployment_change_store_claim='deployment-change-artifacts-kms',
        health_stream='c' * 64,
        kubernetes_server='https://eks.example.invalid:443',
        cluster_uid='kube-system-uid', namespace_uid='namespace-uid',
        deployment_uids={name + '-service': name + '-uid'
                         for name in ('checkout', 'payment', 'inventory')},
        observer_secret='incident-observer-credentials',
    )
    mounted = set()
    for document in documents:
        if document['kind'] != 'Deployment':
            continue
        for volume in document['spec']['template']['spec']['volumes']:
            if 'persistentVolumeClaim' in volume:
                mounted.add(volume['persistentVolumeClaim']['claimName'])
    assert mounted == {name + '-kms' for name in CLAIMS}
