"""Offline configuration checks only: no registry, Kubernetes, cloud or source acceptance."""
import json
import sys

import pytest
import yaml

from scripts import render_business_manifests as manifests

IMAGE = 'registry.example.invalid/sentinelops@sha256:' + 'a' * 64
PARAMETERS = {'environment': 'aws-dev', 'otlp_endpoint': 'http://otel-collector:4317',
              'commerce_secret': 'commerce-runtime'}


def test_bundle_preserves_business_topology_without_databases_secrets_authority_or_public_exposure():
    documents = manifests.render(IMAGE, **PARAMETERS)
    assert len(documents) == 9
    assert {d['kind'] for d in documents} == {'Namespace','ServiceAccount','ConfigMap','Deployment','Service'}
    deployments = {d['metadata']['name']: d for d in documents if d['kind'] == 'Deployment'}
    assert set(deployments) == {'checkout-service','payment-service','inventory-service'}
    common = next(d for d in documents if d['kind'] == 'ConfigMap')['data']
    assert common == {'DEPLOYMENT_ENVIRONMENT':'aws-dev','OTEL_EXPORTER_OTLP_ENDPOINT':'http://otel-collector:4317',
                      'ENABLE_FAULT_INJECTION':'false'}
    for document in documents:
        if document['kind'] != 'Namespace':
            assert document['metadata']['namespace'] == 'sentinelops'
        if document['kind'] == 'Service':
            assert document['spec']['type'] == 'ClusterIP'
    for name, deployment in deployments.items():
        pod = deployment['spec']['template']['spec']
        container, = pod['containers']
        assert container['image'] == IMAGE and deployment['spec']['replicas'] == 1
        assert pod['automountServiceAccountToken'] is False
        assert pod['securityContext']['runAsNonRoot'] is True
        assert container['securityContext']['readOnlyRootFilesystem'] is True
        assert container['securityContext']['allowPrivilegeEscalation'] is False
        assert container['securityContext']['capabilities'] == {'drop':['ALL']}
        assert container['startupProbe']['httpGet']['path'] == container['livenessProbe']['httpGet']['path'] == '/health'
        assert container['readinessProbe']['httpGet']['path'] == '/ready'
        assert all('hostPort' not in port for port in container['ports']) and not pod.get('hostNetwork')
        env = {entry['name']:entry for entry in container['env']}
        assert env['SENTINELOPS_K8S_POD_UID']['valueFrom']['fieldRef']['fieldPath'] == 'metadata.uid'
        if name == 'checkout-service':
            assert env['PAYMENT_URL']['value'] == 'http://payment-service:8000'
            assert env['INVENTORY_URL']['value'] == 'http://inventory-service:8000'
            assert 'DATABASE_URL' not in env
        else:
            assert env['DATABASE_URL']['valueFrom']['secretKeyRef'] == {
                'name':'commerce-runtime','key':'database-url','optional':False}
    serialized = json.dumps(documents)
    assert not any(key in serialized for key in ('AWS_ACCESS_KEY_ID','SENTINELOPS_MODEL_STORE','SENTINELOPS_API_TOKEN',
                                                'SENTINELOPS_OPERATOR_TOKEN','eks.amazonaws.com/role-arn'))


@pytest.mark.parametrize('image', ['application:latest','application','https://registry/app',None,IMAGE[:-1]])
def test_mutable_or_invalid_images_are_rejected(image):
    with pytest.raises(ValueError):
        manifests.render(image, **PARAMETERS)


@pytest.mark.parametrize('key,value', [
    ('environment',''),('environment','Prod'),('environment','a'*64),('environment','prod\nsecret'),
    ('commerce_secret','postgresql://user:password@db/commerce'),('commerce_secret',''),
    ('commerce_secret','name/other'),('commerce_secret',True),
    ('otlp_endpoint','http://@collector:4317'),
    ('otlp_endpoint','http://user:password@collector:4317'),('otlp_endpoint','http://collector:4317?token=secret'),
    ('otlp_endpoint','http://collector:4317/path'),('otlp_endpoint','http://collector:4317#secret'),
    ('otlp_endpoint','file:///tmp/collector'),('otlp_endpoint','http://collector'),
    ('otlp_endpoint','http://collector:99999'),('otlp_endpoint','http://collector:0'),
    ('otlp_endpoint','http://collector:4317\n'),('otlp_endpoint',None),
])
def test_invalid_or_credential_bearing_inputs_never_render(key,value):
    parameters = {**PARAMETERS,key:value}
    with pytest.raises(ValueError):
        manifests.render(IMAGE, **parameters)


def test_cli_is_deterministic_exclusive_and_emits_only_secret_references(tmp_path,monkeypatch):
    outputs = [tmp_path/'one.yaml', tmp_path/'two.yaml']
    def run(path):
        monkeypatch.setattr(sys,'argv',['render_business_manifests','--application-image',IMAGE,
            '--environment','aws-dev','--otlp-endpoint','http://otel-collector:4317',
            '--commerce-secret','commerce-runtime','--output',str(path)])
        manifests.main()
    for path in outputs:
        run(path)
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert list(yaml.safe_load_all(outputs[0].read_text())) == manifests.render(IMAGE,**PARAMETERS)
    before = outputs[0].read_bytes()
    with pytest.raises(FileExistsError):
        run(outputs[0])
    assert outputs[0].read_bytes() == before


def test_invalid_cli_input_does_not_create_an_output(tmp_path,monkeypatch):
    target=tmp_path/'invalid.yaml'
    monkeypatch.setattr(sys,'argv',['render_business_manifests','--application-image','mutable:latest',
        '--environment','aws-dev','--otlp-endpoint','http://otel-collector:4317',
        '--commerce-secret','commerce-runtime','--output',str(target)])
    with pytest.raises(ValueError):
        manifests.main()
    assert not target.exists()
