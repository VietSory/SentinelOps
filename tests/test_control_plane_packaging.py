"""Packaging/configuration checks; no provider, cloud or trained-model claim."""
import json
from pathlib import Path
import shlex
import sys

import pytest
import yaml

from scripts import render_api_manifests as api
from scripts.check_control_plane_image import immutable_image
from scripts import check_control_plane_image, lane_b

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'registry.example.invalid/control-plane@sha256:' + 'a' * 64
PARAMETERS = {'environment': 'aws-dev', 'control_secret': 'control-runtime',
              'issuer': 'https://identity.example.invalid/realm', 'audience': 'sentinelops',
              'jwks_url': 'https://identity.example.invalid/realm/jwks.json'}


def test_api_manifest_has_oidc_secret_references_and_no_implicit_model_worker_or_action_authority():
    documents = api.render(IMAGE, **PARAMETERS)
    assert len(documents) == 5
    assert {d['kind'] for d in documents} == {'Namespace', 'ServiceAccount', 'ConfigMap', 'Deployment', 'Service'}
    config = next(d for d in documents if d['kind'] == 'ConfigMap')['data']
    assert config['SENTINELOPS_ENV'] == 'aws-dev' and config['SENTINELOPS_ENABLE_DEMO'] == 'false'
    assert config['SENTINELOPS_OIDC_ISSUER'] == PARAMETERS['issuer']
    assert not any(name in config for name in ('SENTINELOPS_MODEL_STORE', 'SENTINELOPS_MODEL_RELEASE_REF',
        'SENTINELOPS_API_TOKEN', 'SENTINELOPS_OPERATOR_TOKEN', 'CONTROL_DATABASE_URL'))
    deployment = next(d for d in documents if d['kind'] == 'Deployment')
    pod = deployment['spec']['template']['spec']
    container, = pod['containers']
    assert container['image'] == IMAGE and pod['automountServiceAccountToken'] is False
    assert pod['securityContext']['runAsUser'] == 10001
    assert container['securityContext'] == {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                             'capabilities': {'drop': ['ALL']}}
    assert container['env'] == [{'name': 'CONTROL_DATABASE_URL', 'valueFrom': {'secretKeyRef': {
        'name': 'control-runtime', 'key': 'database-url', 'optional': False}}}]
    assert container['readinessProbe']['httpGet']['path'] == '/health'
    assert 'HTTP server availability' in deployment['spec']['template']['metadata']['annotations']['sentinelops.io/readiness-scope']
    assert pod['volumes'] == [{'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]
    assert next(d for d in documents if d['kind'] == 'Service')['spec']['type'] == 'ClusterIP'
    assert not any(d['kind'] in ('Secret', 'PersistentVolumeClaim', 'Role', 'RoleBinding') for d in documents)


def test_optional_model_is_explicit_read_only_wiring_not_approval():
    ref = 'artifact://manifests/' + 'b' * 64 + '.json'
    documents = api.render(IMAGE, **PARAMETERS, model_claim='approved-models', model_release_ref=ref)
    data = next(d for d in documents if d['kind'] == 'ConfigMap')['data']
    assert data['SENTINELOPS_MODEL_STORE'] == '/models' and data['SENTINELOPS_MODEL_RELEASE_REF'] == ref
    pod = next(d for d in documents if d['kind'] == 'Deployment')['spec']['template']['spec']
    assert pod['volumes'][1]['persistentVolumeClaim'] == {'claimName': 'approved-models', 'readOnly': True}
    assert pod['containers'][0]['volumeMounts'][1]['readOnly'] is True


@pytest.mark.parametrize('values', [
    {'environment': 'local'}, {'environment': 'test'}, {'environment': 'PRODUCTION'},
    {'control_secret': 'postgresql://user:password@database/control'},
    {'issuer': 'http://identity.example.invalid'}, {'issuer': 'https://@identity.example.invalid'},
    {'issuer': 'https://identity.example.invalid?token=secret'}, {'issuer': 'https://identity.example.invalid#secret'},
    {'issuer': 'https://identity.example.invalid\n'}, {'issuer': None}, {'issuer': True},
    {'jwks_url': 'https://user:password@identity.example.invalid/keys'},
    {'jwks_url': 'https://identity.example.invalid:0/keys'},
    {'audience': ''}, {'audience_claim': 'unknown'},
    {'model_claim': 'models'}, {'model_release_ref': 'artifact://manifests/' + 'a' * 64 + '.json'},
    {'model_release_ref': ''}, {'model_claim': True, 'model_release_ref': 'invalid'},
    {'model_claim': 'models', 'model_release_ref': 'artifact://manifests/../model.json'},
    {'model_claim': 'models', 'model_release_ref': 'https://arbitrary/model'},
])
def test_invalid_or_credential_bearing_configuration_is_rejected(values):
    with pytest.raises(ValueError):
        api.render(IMAGE, **{**PARAMETERS, **values})


@pytest.mark.parametrize('image', ['registry/app:latest', 'registry/app', None])
def test_mutable_images_cannot_be_rendered_or_verified(image):
    with pytest.raises(ValueError):
        api.render(image, **PARAMETERS)
    with pytest.raises(ValueError):
        immutable_image(image)


def test_immutable_docker_id_can_be_verified_but_is_not_a_registry_manifest_reference():
    value = 'sha256:' + 'a' * 64
    assert immutable_image(value) == value
    assert immutable_image(IMAGE) == IMAGE
    with pytest.raises(ValueError):
        api.render(value, **PARAMETERS)


def test_cli_is_deterministic_and_refuses_overwrite(tmp_path, monkeypatch):
    def render(path):
        monkeypatch.setattr(sys, 'argv', ['render_api_manifests', '--image', IMAGE,
            '--environment', 'aws-dev', '--control-secret', 'control-runtime',
            '--issuer', PARAMETERS['issuer'], '--audience', PARAMETERS['audience'],
            '--jwks-url', PARAMETERS['jwks_url'], '--output', str(path)])
        api.main()
    first, second = tmp_path/'one.yaml', tmp_path/'two.yaml'
    render(first); render(second)
    assert first.read_bytes() == second.read_bytes()
    assert list(yaml.safe_load_all(first.read_text())) == api.render(IMAGE, **PARAMETERS)
    with pytest.raises(FileExistsError):
        render(first)


def test_control_plane_build_context_allowlist_cannot_include_user_data_or_credentials():
    ignore = (ROOT/'docker/Dockerfile.control-plane.dockerignore').read_text().splitlines()
    assert ignore[0] == '**'
    includes = {line[1:] for line in ignore if line.startswith('!')}
    assert includes == {'pyproject.toml', 'README.md', 'setup.py', 'MANIFEST.in', 'src/', 'src/**',
        'contracts/', 'contracts/*.schema.json', 'scripts/', 'scripts/run_health_worker.py',
        'scripts/run_incident_worker.py', 'configs/', 'configs/slo_health_policy.json',
        'configs/rca_policy.json', 'configs/topology.yaml', 'docker/',
        'docker/Dockerfile.control-plane', 'docker/Dockerfile.control-plane.dockerignore'}
    # In particular the separately delivered bundle under docs/ is excluded too.
    assert not any(path.startswith(('docs', 'artifacts', 'data', 'models', '.env', '.aws')) for path in includes)
    dockerfile = (ROOT/'docker/Dockerfile.control-plane').read_text()
    assert '@sha256:' in dockerfile and 'USER 10001:10001' in dockerfile
    assert 'SENTINELOPS_ENV=production' in dockerfile and 'SENTINELOPS_ENABLE_DEMO=false' in dockerfile
    assert 'COPY . ' not in dockerfile and 'COPY src' not in dockerfile.split('FROM base AS runtime')[1]


def test_ci_image_proof_does_not_appropriate_an_unregistered_lane_b_runtime(tmp_path, monkeypatch):
    workflow = yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())
    steps = workflow['jobs']['host-process-integration']['steps']
    arguments = shlex.split(next(step['run'] for step in steps
        if 'scripts.check_control_plane_image --build' in step.get('run', '')))
    output = tmp_path / arguments[arguments.index('--output') + 1]
    monkeypatch.setattr(check_control_plane_image, 'check', lambda image: {'status': 'PASS', 'image_id': image})
    monkeypatch.setattr(sys, 'argv', ['check_control_plane_image', '--image', IMAGE, '--output', str(output)])
    check_control_plane_image.main()

    # Exercise the actual registration guard, not just a path string comparison.
    monkeypatch.setattr(lane_b, 'PROJECT', tmp_path)
    monkeypatch.setattr(lane_b, 'ROOT', tmp_path/'artifacts/lane-b')
    lane_b.init_runtime()
    assert (lane_b.ROOT/'runtime.json').is_file()
    assert json.loads(output.read_text())['status'] == 'PASS'
