import pytest

from scripts.kubernetes_testbed import IMAGE_KEYS, NAMESPACE, SERVICES, render


def images(): return {key: 'registry/' + key + '@sha256:' + 'a' * 64 for key in IMAGE_KEYS}


def test_manifests_isolate_real_apps_and_keep_passwords_tokens_and_host_ports_out():
    documents = render(images())
    assert not any(d['kind'] in ('Secret', 'PersistentVolumeClaim') for d in documents)
    deployments = {d['metadata']['name']: d for d in documents if d['kind'] == 'Deployment'}
    assert set(deployments) == {*SERVICES, 'postgres', 'prometheus', 'jaeger', 'otel-collector'}
    for document in documents:
        if document['kind'] != 'Namespace': assert document['metadata']['namespace'] == NAMESPACE
        if document['kind'] == 'Service': assert document['spec']['type'] == 'ClusterIP'
    for name in SERVICES:
        spec = deployments[name]['spec'];pod = spec['template']['spec'];container = pod['containers'][0]
        assert spec['replicas'] == 1 and spec['strategy']['rollingUpdate']['maxSurge'] == 0
        assert pod['automountServiceAccountToken'] is False
        assert all('hostPort' not in p for p in container['ports'])
        env = {e['name']: e for e in container['env']}
        assert env['SENTINELOPS_K8S_POD_UID']['valueFrom']['fieldRef']['fieldPath'] == 'metadata.uid'
        assert env['ENABLE_FAULT_INJECTION']['value'] == 'false'
        if name != 'checkout-service': assert 'secretKeyRef' in env['DATABASE_URL']['valueFrom']
    assert deployments['payment-service']['spec']['template']['spec']['containers'][0]['name'] == 'payment'


@pytest.mark.parametrize('problem', ['tag', 'missing', 'extra'])
def test_renderer_rejects_mutable_or_partial_image_inputs(problem):
    value = images()
    if problem == 'tag': value['application'] = 'payment:latest'
    if problem == 'missing': value.pop('postgres')
    if problem == 'extra': value['unreviewed'] = value['postgres']
    with pytest.raises(ValueError): render(value)
