import pytest
import json
import sys
from pathlib import PurePosixPath

import httpx
import yaml

from scripts import kubernetes_testbed as manifests
from scripts.kubernetes_testbed import IMAGE_KEYS, NAMESPACE, SERVICES, render
from sentinelops.telemetry.prometheus import PrometheusSource, SOURCE_PROFILE_V2, queries


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


def eks_images():
    return {**images(), 'application':
            '123456789012.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/application@sha256:' + 'b' * 64}


def test_eks_stack_budget_is_schedulable_on_two_small_nodes_without_credentials_or_public_services():
    documents = render(eks_images(), target='eks')
    deployments = {d['metadata']['name']: d for d in documents if d['kind'] == 'Deployment'}
    assert set(deployments) == {*SERVICES, 'postgres', 'prometheus', 'jaeger', 'otel-collector'}
    total_memory = total_cpu = 0
    for document in documents:
        metadata = document['metadata']
        assert metadata.get('namespace', metadata['name']) == 'sentinelops-b-actions'
        if document['kind'] == 'Namespace':
            assert metadata['labels']['sentinelops.dev/lane'] == 'B'
        if document['kind'] == 'Service':
            assert document['spec']['type'] == 'ClusterIP'
    for name, deployment in deployments.items():
        assert deployment['spec']['replicas'] == 1
        pod = deployment['spec']['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        container, = pod['containers']
        requests, limits = container['resources']['requests'], container['resources']['limits']
        total_memory += int(requests['memory'].removesuffix('Mi'))
        total_cpu += int(requests['cpu'].removesuffix('m'))
        assert int(requests['memory'][:-2]) <= int(limits['memory'][:-2]) <= 2048
        assert int(requests['cpu'][:-1]) <= int(limits['cpu'][:-1]) <= 1000
        assert not pod.get('hostNetwork') and not any('hostPort' in p for p in container['ports'])
        if name in SERVICES:
            assert container['image'] == eks_images()['application']
            env = {e['name']: e for e in container['env']}
            assert env['DEPLOYMENT_ENVIRONMENT']['value'] == 'dev'
            assert env['ENABLE_FAULT_INJECTION']['value'] == ('false' if name == 'checkout-service' else 'true')
            if name != 'checkout-service':
                assert env['DATABASE_URL']['valueFrom']['secretKeyRef'] == {
                    'name': 'commerce-credentials', 'key': 'database-url'}
    assert (total_memory, total_cpu) == (3840, 1200)
    assert total_memory < 2 * 6.9 * 1024 and total_cpu < 2 * 1930
    assert deployments['postgres']['spec']['strategy'] == {'type': 'Recreate'}
    payment = deployments['payment-service']
    assert payment['metadata']['namespace'] == 'sentinelops-b-actions'
    assert payment['spec']['template']['spec']['containers'][0]['name'] == 'payment'
    serialized = json.dumps(documents)
    assert not any(d['kind'] == 'Secret' for d in documents)
    assert all(forbidden not in serialized for forbidden in (
        'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_ROLE_ARN', 'hostPath',
        'postgresql://', 'LoadBalancer', 'NodePort', 'serviceAccountToken'))


@pytest.mark.parametrize('image', [
    'application:latest', images()['application'],
    '123456789012.dkr.ecr.eu-central-1.amazonaws.com/sentinelops/application@sha256:' + 'a' * 64,
    '123456789012.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/control-plane@sha256:' + 'a' * 64,
    '123456789012.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/application:latest@sha256:' + 'a' * 64,
])
def test_eks_requires_the_declared_ecr_application_repo_and_digest(image):
    with pytest.raises(ValueError):
        render({**eks_images(), 'application': image}, target='eks')


@pytest.mark.parametrize('target', ['local', 'eks'])
def test_fault_injection_is_enabled_only_for_eks_dev_payment_and_inventory(target):
    documents = render(eks_images(), target=target)
    for document in documents:
        if document['kind'] != 'Deployment' or document['metadata']['name'] not in SERVICES:
            continue
        name = document['metadata']['name']
        container, = document['spec']['template']['spec']['containers']
        env = {e['name']: e.get('value') for e in container['env']}
        enabled = target == 'eks' and name in ('payment-service', 'inventory-service')
        assert env['DEPLOYMENT_ENVIRONMENT'] == ('dev' if target == 'eks' else 'local')
        assert env['ENABLE_FAULT_INJECTION'] == ('true' if enabled else 'false')
        if enabled:
            assert env['ALLOW_DEV_FAULT_INJECTION'] == 'true'
        else:
            assert 'ALLOW_DEV_FAULT_INJECTION' not in env


def test_eks_metrics_path_preserves_five_second_scrapes_and_v2_query_catalog():
    documents = render(eks_images(), target='eks')
    configs = {d['metadata']['name']: d['data'] for d in documents if d['kind'] == 'ConfigMap'}
    collector = yaml.safe_load(configs['otel-config']['config.yaml'])
    prometheus = yaml.safe_load(configs['prometheus-config']['prometheus.yml'])
    assert collector['service']['pipelines']['metrics'] == {
        'receivers': ['otlp'], 'processors': [], 'exporters': ['prometheus', 'debug']}
    assert collector['exporters']['prometheus']['endpoint'] == '0.0.0.0:8889'
    assert collector['exporters']['otlp/jaeger']['endpoint'] == 'jaeger:4317'
    assert prometheus['global']['scrape_interval'] == '5s'
    assert prometheus['scrape_configs'] == [{'job_name': 'otel-collector', 'honor_labels': True,
                                          'static_configs': [{'targets': ['otel-collector:8889']}]}]
    assert manifests.SOURCE_PROFILE == SOURCE_PROFILE_V2 == 'prometheus-increase-60s-5s/v2'
    deployments = [d for d in documents if d['kind'] == 'Deployment']
    for deployment in deployments:
        assert deployment['spec']['template']['metadata']['annotations']['sentinelops.io/source-profile'] == SOURCE_PROFILE_V2
        name = deployment['metadata']['name']
        container, = deployment['spec']['template']['spec']['containers']
        if name in SERVICES:
            assert next(e['value'] for e in container['env'] if e['name'] == 'OTEL_EXPORTER_OTLP_ENDPOINT') == 'http://otel-collector:4317'
    # The worker's actual source verifier must accept the rendered scrape config.
    def respond(request):
        assert request.url.path == '/api/v1/status/config'
        return httpx.Response(200, json={'status': 'success', 'data': {
            'yaml': yaml.safe_dump(prometheus)}})
    source = PrometheusSource('http://prometheus:9090', transport=httpx.MockTransport(respond))
    try:
        assert source.verify_configuration()['scrape_interval'] == '5s'
    finally:
        source.close()
    for name in SERVICES:
        catalog = queries(name, SOURCE_PROFILE_V2)
        assert {'duration_sum', 'duration_count', 'requests', 'errors', 'buckets'} <= catalog.keys()
        assert '[60s]' in catalog['duration_sum'] and '[60s]' in catalog['requests']


@pytest.mark.parametrize('target', ['local', 'eks'])
def test_prometheus_rule_files_resolve_to_mounted_configmap_data(target):
    documents = render(eks_images(), target=target)
    configs = {d['metadata']['name']: d['data'] for d in documents if d['kind'] == 'ConfigMap'}
    prometheus = yaml.safe_load(configs['prometheus-config']['prometheus.yml'])
    deployment = next(d for d in documents if d['kind'] == 'Deployment' and d['metadata']['name'] == 'prometheus')
    pod = deployment['spec']['template']['spec']
    mounts = pod['containers'][0]['volumeMounts']
    volumes = {v['name']: v for v in pod['volumes']}
    for filename in prometheus['rule_files']:
        mount, = [m for m in mounts if str(PurePosixPath(filename).parent) == m['mountPath']]
        data = configs[volumes[mount['name']]['configMap']['name']]
        rules = yaml.safe_load(data[PurePosixPath(filename).name])
        assert rules['groups']


def test_eks_cli_is_deterministic_and_exclusive_and_fails_before_output_for_invalid_image(tmp_path, monkeypatch):
    image_file = tmp_path / 'images.json'
    image_file.write_text(json.dumps(eks_images()))
    first, second = tmp_path / 'first.yaml', tmp_path / 'second.yaml'
    def run(output):
        monkeypatch.setattr(sys, 'argv', ['kubernetes_testbed', '--target', 'eks', '--images',
                                        str(image_file), '--output', str(output)])
        manifests.main()
    run(first)
    run(second)
    assert first.read_bytes() == second.read_bytes()
    assert list(yaml.safe_load_all(first.read_text())) == render(eks_images(), target='eks')
    with pytest.raises(FileExistsError):
        run(first)
    image_file.write_text(json.dumps(images()))
    invalid = tmp_path / 'invalid.yaml'
    with pytest.raises(ValueError):
        run(invalid)
    assert not invalid.exists()
