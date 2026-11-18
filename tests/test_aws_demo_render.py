"""Offline input guards, namespace composition and real traffic-driver behavior."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from scripts.aws_demo import render_all as pipeline


def inputs():
    folder = pipeline.ROOT / 'scripts/aws_demo'
    return (pipeline.read_json(folder / 'k8s-images.example.json'),
            pipeline.terraform_values(pipeline.read_json(folder / 'k8s-terraform.example.json')),
            pipeline.read_json(folder / 'k8s-runtime.example.json'))


def test_namespace_and_observed_uid_composition():
    images, tf, runtime = inputs()
    groups = pipeline.render(images, tf, runtime)
    docs = [doc for group in groups.values() for doc in group]
    identities = [(doc['apiVersion'], doc['kind'], doc['metadata'].get('namespace'), doc['metadata']['name']) for doc in docs]
    assert len(identities) == len(set(identities))
    assert {doc['metadata']['name'] for doc in docs if doc['kind'] == 'Namespace'} == {'sentinelops', 'sentinelops-b-actions'}
    kube = next(doc for doc in docs if doc['kind'] == 'ConfigMap' and doc['metadata']['name'] == pipeline.workers.KUBE_CONFIG)
    data = json.loads(kube['data']['kubernetes.json'])
    assert data['server'] == 'https://eks.example.invalid:443'
    assert data['deployment_uids'] == runtime['deployment_uids']
    assert data['target']['cluster_uid'] == runtime['cluster_uid']
    assert not any(doc['kind'] == 'Secret' for doc in docs)


@pytest.mark.parametrize('change', ['image-tag', 'reader-role', 'missing-uid', 'region', 'repository'])
def test_invalid_bindings_are_rejected(change):
    images, tf, runtime = deepcopy(inputs())
    if change == 'image-tag':
        images['postgres'] = 'postgres:17'
    elif change == 'reader-role':
        tf['collector_bindings']['exporter_role_arn'] = 'arn:aws:iam::123456789012:role/sentinelops-telemetry-reader'
    elif change == 'missing-uid':
        runtime['deployment_uids'].pop('payment-service')
    elif change == 'region':
        tf['region'] = 'eu-central-1'
    else:
        images['control-plane'] = images['control-plane'].replace('123456789012', '111111111111')
    with pytest.raises(ValueError):
        pipeline.render(images, tf, runtime)


def test_existing_driver_hits_all_business_routes_at_60_per_minute_without_network():
    docs = pipeline.traffic(inputs()[0]['application'])
    code = docs[0]['data']['traffic.py']
    scope = {'__name__': 'offline_traffic_test'}
    exec(compile(code, '<traffic ConfigMap>', 'exec'), scope)
    elapsed = [0.0]
    start = datetime(2026, 11, 18, tzinfo=timezone.utc)
    observed = []

    async def sleep(seconds):
        elapsed[0] += seconds

    def respond(request):
        observed.append((request.url.host, request.method, request.url.path))
        return httpx.Response(200, headers={
            'x-trace-id': request.headers['traceparent'].split('-')[1],
            'x-correlation-id': request.headers['x-correlation-id']})

    scope['clock'] = lambda: start + timedelta(seconds=elapsed[0])
    scope['asyncio'] = SimpleNamespace(get_running_loop=lambda: SimpleNamespace(time=lambda: elapsed[0]),
                                       sleep=sleep, gather=asyncio.gather)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            urls = {service: f'http://{service}:8000' for service in scope['BUSINESS_ROUTES']}
            counts = await scope['drive'](client, urls, start + timedelta(seconds=60), False)
            assert counts == {service: {'200': 60} for service in urls}
    asyncio.run(exercise())
    assert set(observed) == {
        ('checkout-service', 'POST', '/v1/checkout'),
        ('payment-service', 'POST', '/v1/payments/charge'),
        ('inventory-service', 'GET', '/v1/inventory/sku-001')}


def test_schema_failure_does_not_publish_output(tmp_path, monkeypatch):
    folder = pipeline.ROOT / 'scripts/aws_demo'
    output = tmp_path / 'artifacts' / 'k8s'
    monkeypatch.setattr(sys, 'argv', ['render_all',
        str(folder / 'k8s-images.example.json'), str(folder / 'k8s-terraform.example.json'),
        str(folder / 'k8s-runtime.example.json'), '--output', str(output)])
    def reject(command, **kwargs):
        assert command[:5] == ['kubeconform', '-strict', '-summary', '-kubernetes-version', '1.34.0']
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(pipeline.subprocess, 'run', reject)
    with pytest.raises(subprocess.CalledProcessError):
        pipeline.main()
    assert not output.exists()
    assert not list(output.parent.iterdir())
