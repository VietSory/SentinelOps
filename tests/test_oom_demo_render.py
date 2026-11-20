"""Check deployment identity, credential handling and bounded demo resources."""
from copy import deepcopy
import json

import pytest

from scripts.aws_demo.oom_controller import POLICY, render as render_controller, render_rbac
from scripts.aws_demo.reset_oom_demo import render as render_reset
from scripts.render_oom_demo import render


IMAGE = 'registry.invalid/demo@sha256:' + '1' * 64
TARGET = {'cluster_uid': 'cluster-id', 'namespace_uid': 'namespace-id',
          'deployment_uid': 'workload-id', 'image': IMAGE}


def controller():
    mounts = [{'name': 'tmp', 'mountPath': '/tmp'},
              {'name': 'credentials', 'mountPath': '/credentials', 'readOnly': True},
              {'name': 'database-ca', 'mountPath': '/etc/sentinelops/tls', 'readOnly': True},
              {'name': 'remediation-artifacts', 'mountPath': '/artifacts/remediation'}]
    return {'metadata': {'name': 'remediation-controller', 'namespace': 'sentinelops-b-actions',
                         'uid': 'controller-id', 'resourceVersion': '99'},
            'spec': {'template': {'metadata': {'annotations': {'keep': 'existing'}}, 'spec': {
                'serviceAccountName': 'payment-rollback',
                'containers': [{'name': 'remediation-controller', 'image': IMAGE, 'volumeMounts': mounts},
                               {'name': 'operator-ui', 'image': IMAGE, 'env': [
                                   {'name': key, 'valueFrom': {'secretKeyRef': {'name': 'existing-secret', 'key': key}}}
                                   for key in ('CONTROL_DATABASE_URL', 'UI_DATABASE_PASSWORD')]}],
                'volumes': [{'name': m['name'], 'emptyDir': {}} for m in mounts]}}}}


def workload(*, healthy=True):
    value = render(IMAGE, healthy=healthy)[1]
    value['metadata'].update(uid='workload-id', resourceVersion='7')
    return value


def test_real_oom_manifest_is_bounded_and_has_healthy_baseline():
    low, high = workload(healthy=False), workload()
    low_container = low['spec']['template']['spec']['containers'][0]
    high_container = high['spec']['template']['spec']['containers'][0]
    assert low_container['resources']['limits']['memory'] == '64Mi'
    assert high_container['resources']['limits']['memory'] == '256Mi'
    assert low_container['resources']['requests']['memory'] == '32Mi'
    assert low['spec']['replicas'] == 1
    assert low['spec']['template']['spec']['automountServiceAccountToken'] is False
    assert low_container['securityContext']['readOnlyRootFilesystem'] is True
    assert render(IMAGE)[2]['spec']['type'] == 'ClusterIP'
    with pytest.raises(ValueError):
        render('python:latest')


def test_sidecar_preserves_live_ui_and_uses_only_existing_secret_refs():
    captured = controller()
    original = deepcopy(captured)
    docs, patch = render_controller(captured, TARGET, IMAGE, enabled=True)
    assert captured == original
    assert patch[:2] == [{'op': 'test', 'path': '/metadata/uid', 'value': 'controller-id'},
                         {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '99'}]
    assert patch[2]['value'] == original['spec']['template']['spec']['containers']
    sidecar = patch[4]['value']
    assert sidecar['name'] == 'oom-demo-controller'
    assert sidecar['command'] == ['python', 'scripts/run_pod_memory_demo.py']
    assert '--policy-enabled' in sidecar['args']
    assert sidecar['env'][:2] == original['spec']['template']['spec']['containers'][1]['env']
    assert sidecar['volumeMounts'][3] == {'name': 'remediation-artifacts', 'mountPath': '/artifacts/remediation'}
    assert patch[-1]['value']['keep'] == 'existing'
    assert json.loads(docs[0]['data']['target.json'])['deployment_uid'] == TARGET['deployment_uid']
    assert not any(d.get('kind') == 'ClusterRole' for d in docs)


@pytest.mark.parametrize('change', ['wrong-controller', 'wrong-sa', 'literal-password', 'duplicate'])
def test_sidecar_rejects_unexpected_live_configuration(change):
    captured = controller()
    spec = captured['spec']['template']['spec']
    if change == 'wrong-controller':
        captured['metadata']['name'] = 'payment-service'
    elif change == 'wrong-sa':
        spec['serviceAccountName'] = 'default'
    elif change == 'literal-password':
        spec['containers'][1]['env'][1] = {'name': 'UI_DATABASE_PASSWORD', 'value': 'never-render-this'}
    else:
        spec['containers'].append({'name': 'oom-demo-controller'})
    with pytest.raises(ValueError):
        render_controller(captured, TARGET, IMAGE)


def test_supplemental_rbac_cannot_mutate_other_deployments_or_nodes():
    docs = render_rbac(node_names=['ip-10-0-1-2.internal'])
    assert docs[0]['rules'] == [{'apiGroups': ['apps'], 'resources': ['deployments'],
                                'resourceNames': ['sentinelops-oom-demo'], 'verbs': ['get', 'patch']}]
    assert docs[2]['rules'] == [{'apiGroups': [''], 'resources': ['nodes'],
                                'resourceNames': ['ip-10-0-1-2.internal'], 'verbs': ['get']}]
    assert all(d['metadata']['name'] == POLICY for d in docs)
    with pytest.raises(ValueError):
        render_rbac(node_names=['*'])


def test_reset_only_changes_exact_bounded_demo_limit():
    deployment = workload()
    fault = render_reset(deployment, TARGET, fault=True)
    assert fault[-1] == {'op': 'replace', 'path': '/spec/template/spec/containers/0/resources/limits/memory', 'value': '64Mi'}
    deployment['spec']['template']['spec']['containers'][0]['image'] = 'other@sha256:' + '2' * 64
    with pytest.raises(ValueError):
        render_reset(deployment, TARGET)
