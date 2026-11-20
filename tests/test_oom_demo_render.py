"""Check deployment identity, credential handling and bounded demo resources."""
from copy import deepcopy
import json

import pytest

from scripts.aws_demo.oom_controller import POLICY, render as render_controller, render_rbac
from scripts.aws_demo.reset_oom_demo import render as render_reset
from scripts.render_oom_demo import render


IMAGE = 'registry.invalid/demo@sha256:' + '1' * 64
READER_IMAGE = 'registry.invalid/compatible-reader@sha256:' + '2' * 64
SIDECAR_IMAGE = 'registry.invalid/oom-controller@sha256:' + '3' * 64
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


@pytest.mark.parametrize('reader_first', [False, True])
def test_sidecar_preserves_live_ui_and_uses_only_existing_secret_refs(reader_first):
    captured = controller()
    if not reader_first:
        captured['spec']['template']['spec']['containers'].reverse()
    original = deepcopy(captured)
    docs, patch = render_controller(captured, TARGET, SIDECAR_IMAGE, enabled=True,
                                    reader_image=READER_IMAGE)
    assert captured == original
    assert patch[:2] == [{'op': 'test', 'path': '/metadata/uid', 'value': 'controller-id'},
                         {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '99'}]
    assert patch[2]['value'] == original['spec']['template']['spec']['containers']
    sidecar = patch[4]['value']
    assert sidecar['name'] == 'oom-demo-controller'
    assert sidecar['image'] == SIDECAR_IMAGE
    assert sidecar['command'] == ['python', 'scripts/run_pod_memory_demo.py']
    assert '--policy-enabled' in sidecar['args']
    containers = original['spec']['template']['spec']['containers']
    reader_index = next(i for i, value in enumerate(containers) if value['name'] == 'remediation-controller')
    ui = next(value for value in containers if value['name'] == 'operator-ui')
    assert sidecar['env'][:2] == ui['env']
    assert sidecar['volumeMounts'][3] == {'name': 'remediation-artifacts', 'mountPath': '/artifacts/remediation'}
    annotations = next(item['value'] for item in patch if item['path'] == '/spec/template/metadata/annotations')
    assert annotations['keep'] == 'existing'
    assert annotations['sentinelops.io/oom-reader-base-image'] == IMAGE
    assert annotations['sentinelops.io/oom-reader-image'] == READER_IMAGE
    # The captured full-container CAS protects the UI, and the only existing
    # container mutation is the explicit reader image, regardless of ordering.
    mutations = [item for item in patch if item['op'] != 'test']
    assert [(item['op'], item['path']) for item in mutations] == [
        ('add', '/spec/template/spec/containers/-'),
        ('add', '/spec/template/spec/volumes/-'),
        ('add', '/spec/template/metadata/annotations'),
        ('replace', f'/spec/template/spec/containers/{reader_index}/image')]
    assert mutations[-1]['value'] == READER_IMAGE
    assert json.loads(docs[0]['data']['target.json'])['deployment_uid'] == TARGET['deployment_uid']
    assert not any(d.get('kind') == 'ClusterRole' for d in docs)


def test_enabling_new_incident_source_requires_explicit_compatible_reader():
    with pytest.raises(ValueError, match='compatible reader image'):
        render_controller(controller(), TARGET, SIDECAR_IMAGE, enabled=True)


@pytest.mark.parametrize('reader_image', ['', 'registry.invalid/reader:latest'])
def test_mutable_or_empty_reader_image_is_rejected(reader_image):
    with pytest.raises(ValueError):
        render_controller(controller(), TARGET, SIDECAR_IMAGE, enabled=True, reader_image=reader_image)


def test_disabled_policy_keeps_existing_reader_image_without_implicit_upgrade():
    _, patch = render_controller(controller(), TARGET, SIDECAR_IMAGE)
    assert all(item['op'] != 'replace' for item in patch)
    assert '--policy-enabled' not in patch[4]['value']['args']


@pytest.mark.parametrize('reader_image', [None, READER_IMAGE])
def test_cli_requires_and_forwards_the_explicit_reader_image(tmp_path, monkeypatch, reader_image):
    import sys
    from scripts.aws_demo.oom_controller import main
    capture, target, output = tmp_path / 'controller.json', tmp_path / 'target.json', tmp_path / 'output'
    capture.write_text(json.dumps(controller()))
    target.write_text(json.dumps(TARGET))
    args = ['oom-controller', '--controller', str(capture), '--target', str(target),
            '--image', SIDECAR_IMAGE, '--policy-enabled', '--output-dir', str(output)]
    if reader_image:
        args += ['--reader-image', reader_image]
    monkeypatch.setattr(sys, 'argv', args)
    if reader_image is None:
        with pytest.raises(ValueError, match='compatible reader image'):
            main()
        assert not output.exists()
    else:
        main()
        patch = json.loads((output / 'oom-controller-patch.json').read_text())
        assert patch[-1] == {'op': 'replace', 'path': '/spec/template/spec/containers/0/image',
                             'value': READER_IMAGE}


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
