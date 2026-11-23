"""WP7 acceptance (frozen): SNS alerts, publisher identity and the two sidecars.

Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP7 (R7.1-R7.3, A7.1-A7.3).
"""
from copy import deepcopy

import pytest

from scripts import render_worker_manifests as worker
from scripts.check_aws_iac import (assert_alerts_publisher_scoped, normalize, policy_documents, resources)

from test_worker_manifests import IMAGE, PARAMETERS, RELEASE

TOPIC = 'arn:aws:sns:ap-southeast-1:123456789012:sentinelops-dev-demo-alerts'
COLLECTOR = 'http://otel-collector.sentinelops-b-actions.svc.cluster.local:4317'
UI = 'https://demo.example.invalid/ui/'


def render(**changes):
    return worker.render(IMAGE, **{**PARAMETERS, 'model_claim': 'model-store', 'model_release_ref': RELEASE,
                                   'early_warning': True, 'alerts_topic_arn': TOPIC, 'ui_url': UI,
                                   'alerts_otlp_endpoint': COLLECTOR, **changes})


def pod(documents, name):
    deployment = next(d for d in documents if d['kind'] == 'Deployment' and d['metadata']['name'] == name)
    return deployment['spec']['template']['spec']


def container(spec, name):
    return next(c for c in spec['containers'] if c['name'] == name)


def mounts(spec, item):
    volumes = {v['name']: v for v in spec['volumes']}
    return [(m, volumes[m['name']]) for m in item['volumeMounts']]


def writable(mount, volume):
    if mount.get('readOnly'):
        return False
    claim = volume.get('persistentVolumeClaim')
    return not (claim and claim.get('readOnly'))


def test_risk_worker_sidecar_reads_health_and_writes_only_scratch():
    spec = pod(render(), 'sentinelops-incident-worker')
    risk = container(spec, 'risk-worker')
    assert risk['command'] == ['python', 'scripts/run_risk_worker.py']
    args = risk['args']
    for flag, value in (('--health-stream', '$(SENTINELOPS_HEALTH_STREAM)'), ('--model-store', '/models'),
                        ('--model-release-ref', RELEASE), ('--metrics-otlp-endpoint', COLLECTOR),
                        ('--source-store', '/artifacts/health'), ('--source-policy', 'in-cluster')):
        assert args[args.index(flag) + 1] == value
    for mount, volume in mounts(spec, risk):
        if writable(mount, volume):
            assert 'emptyDir' in volume, mount
    scratch = args[args.index('--scratch-store') + 1]
    assert any(m['mountPath'] == scratch and 'emptyDir' in v for m, v in mounts(spec, risk))
    assert any(m['mountPath'] == '/artifacts/health' and not writable(m, v) for m, v in mounts(spec, risk))
    assert risk['image'] == IMAGE and '@sha256:' in risk['image']


def test_notifier_sidecar_reads_controller_store_and_writes_only_its_subpath():
    spec = pod(render(), 'remediation-controller')
    notifier = container(spec, 'notifier')
    assert notifier['command'] == ['python', 'scripts/run_notifier.py']
    args = notifier['args']
    for flag, value in (('--topic-arn', TOPIC), ('--ui-url', UI), ('--metrics-otlp-endpoint', COLLECTOR),
                        ('--store', '/artifacts/remediation')):
        assert args[args.index(flag) + 1] == value
    store = args[args.index('--notifier-store') + 1]
    writable_mounts = [(m, v) for m, v in mounts(spec, notifier) if writable(m, v)]
    assert all('emptyDir' in v or (m.get('subPath') == 'notifier' and m['mountPath'] == store)
               for m, v in writable_mounts)
    assert any(m.get('subPath') == 'notifier' for m, _ in writable_mounts)
    assert any(m['mountPath'] == '/artifacts/remediation' and not writable(m, v) for m, v in mounts(spec, notifier))
    assert not any('secretKeyRef' in str(e) for e in notifier.get('env', []))


def test_sidecars_are_opt_in_and_validated():
    plain = worker.render(IMAGE, **PARAMETERS)
    for name, sidecar in (('sentinelops-incident-worker', 'risk-worker'), ('remediation-controller', 'notifier')):
        assert sidecar not in [c['name'] for c in pod(plain, name)['containers']]
    with pytest.raises(ValueError):
        worker.render(IMAGE, **{**PARAMETERS, 'early_warning': True})  # needs a model release
    with pytest.raises(ValueError):
        render(alerts_topic_arn='arn:aws:sns:ap-southeast-1:123:bad name')
    with pytest.raises(ValueError):
        render(alerts_otlp_endpoint='http://evil.invalid:4317')
    with pytest.raises(ValueError):
        render(ui_url='http://demo.example.invalid/ui/')


BLOCKS, DOCUMENTS = resources(), policy_documents()


def test_real_alerts_configuration_is_scoped():
    assert assert_alerts_publisher_scoped(BLOCKS, DOCUMENTS)
    topic = BLOCKS['aws_sns_topic.demo_alerts']['body']
    assert 'kms_master_key_id = "alias/aws/sns"' in topic
    subscription = BLOCKS['aws_sns_topic_subscription.demo_alerts']['body']
    assert 'protocol  = "email"' in subscription or 'protocol = "email"' in subscription
    association = BLOCKS['aws_eks_pod_identity_association.alerts_publisher']['body']
    assert 'service_account = "payment-rollback"' in association


def mutate_document(name, old, new):
    documents = deepcopy(DOCUMENTS)
    assert old in documents[name]['body']
    documents[name] = {**documents[name], 'body': documents[name]['body'].replace(old, new)}
    return documents


@pytest.mark.parametrize('old, new', [
    ('"sns:Publish"', '"sns:*"'),
    ('[aws_sns_topic.demo_alerts.arn]', '["*"]'),
    ('"sns:Publish"', '"sns:Publish", "sns:Subscribe"'),
])
def test_publisher_policy_escapes_are_rejected(old, new):
    with pytest.raises(ValueError):
        assert_alerts_publisher_scoped(BLOCKS, mutate_document('alerts_publisher', old, new))


def test_publisher_extra_binding_is_rejected():
    blocks = dict(BLOCKS)
    blocks['aws_iam_role_policy_attachment.x'] = {'kind': 'aws_iam_role_policy_attachment', 'body': normalize(
        'role = aws_iam_role.alerts_publisher.name\npolicy_arn = "arn:aws:iam::aws:policy/AmazonSNSFullAccess"')}
    with pytest.raises(ValueError):
        assert_alerts_publisher_scoped(blocks, DOCUMENTS)
