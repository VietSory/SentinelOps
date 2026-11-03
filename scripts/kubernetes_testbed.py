"""B-owned local Kubernetes manifests; no cloud, controller or source approval.

All images must be registry digests. The caller provisions commerce-credentials
separately; render never accepts or emits a password/DSN/Secret object.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from sentinelops.remediation.domain import image_digest

PROJECT = Path(__file__).resolve().parents[1]
NAMESPACE = 'sentinelops-b-actions'
IMAGE_KEYS = {'application', 'postgres', 'otel-collector', 'prometheus', 'jaeger'}
SERVICES = ('checkout-service', 'payment-service', 'inventory-service')


def render(images):
    if set(images) != IMAGE_KEYS:
        raise ValueError('exact local Kubernetes image set required')
    for image in images.values(): image_digest(image)
    documents = [{'apiVersion': 'v1', 'kind': 'Namespace',
                  'metadata': {'name': NAMESPACE, 'labels': {'sentinelops.dev/lane': 'B'}}}]

    def config(name, data):
        documents.append({'apiVersion': 'v1', 'kind': 'ConfigMap',
                          'metadata': {'name': name, 'namespace': NAMESPACE}, 'data': data})

    config('commerce-init', {'001-init.sql': (PROJECT / 'infra/local/init.sql').read_text()})
    config('otel-config', {'config.yaml': (PROJECT / 'observability/otel-collector-config.yaml').read_text()})
    config('prometheus-config', {'prometheus.yml': (PROJECT / 'observability/prometheus.yml').read_text()})

    def deployment(name, image, ports, *, command=None, env=(), mounts=(), volumes=(), ready=None, container=None):
        spec = {'name': container or name, 'image': image, 'imagePullPolicy': 'IfNotPresent',
                'ports': [{'containerPort': port} for port in ports], 'env': list(env),
                'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'},
                              'limits': {'cpu': '500m', 'memory': '384Mi'}}}
        if command: spec['command'] = command
        if mounts: spec['volumeMounts'] = list(mounts)
        if ready: spec['readinessProbe'] = {'httpGet': {'path': ready, 'port': ports[0]},
                                           'periodSeconds': 2, 'timeoutSeconds': 2}
        pod = {'automountServiceAccountToken': False, 'containers': [spec], 'volumes': list(volumes)}
        if name in SERVICES:
            spec['securityContext'] = {'allowPrivilegeEscalation': False, 'runAsNonRoot': True,
                                       'runAsUser': 10001, 'capabilities': {'drop': ['ALL']}}
        if name == 'prometheus': pod['securityContext'] = {'fsGroup': 65534}
        documents.append({'apiVersion': 'apps/v1', 'kind': 'Deployment',
            'metadata': {'name': name, 'namespace': NAMESPACE},
            'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': name}},
                     'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}},
                     'template': {'metadata': {'labels': {'app': name}}, 'spec': pod}}})
        documents.append({'apiVersion': 'v1', 'kind': 'Service',
            'metadata': {'name': name, 'namespace': NAMESPACE},
            'spec': {'type': 'ClusterIP', 'selector': {'app': name},
                     'ports': [{'name': 'tcp-' + str(p), 'port': p, 'targetPort': p} for p in ports]}})

    deployment('postgres', images['postgres'], [5432],
        env=[{'name': 'POSTGRES_DB', 'value': 'sentinelops'}, {'name': 'POSTGRES_USER', 'value': 'sentinel'},
             {'name': 'POSTGRES_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'commerce-credentials', 'key': 'password'}}}],
        mounts=[{'name': 'data', 'mountPath': '/var/lib/postgresql/data'},
                {'name': 'init', 'mountPath': '/docker-entrypoint-initdb.d', 'readOnly': True}],
        volumes=[{'name': 'data', 'emptyDir': {}}, {'name': 'init', 'configMap': {'name': 'commerce-init'}}])
    deployment('jaeger', images['jaeger'], [16686, 4317])
    deployment('otel-collector', images['otel-collector'], [13133, 4317, 4318, 8889],
        command=['/otelcol', '--config=/etc/otelcol/config.yaml'], ready='/',
        mounts=[{'name': 'config', 'mountPath': '/etc/otelcol', 'readOnly': True}],
        volumes=[{'name': 'config', 'configMap': {'name': 'otel-config'}}])
    deployment('prometheus', images['prometheus'], [9090],
        command=['/bin/prometheus', '--config.file=/etc/prometheus/prometheus.yml',
                 '--storage.tsdb.path=/prometheus', '--storage.tsdb.retention.time=1d'], ready='/-/ready',
        mounts=[{'name': 'config', 'mountPath': '/etc/prometheus', 'readOnly': True},
                {'name': 'data', 'mountPath': '/prometheus'}],
        volumes=[{'name': 'config', 'configMap': {'name': 'prometheus-config'}}, {'name': 'data', 'emptyDir': {}}])
    for name in SERVICES:
        short = name.removesuffix('-service')
        env = [{'name': 'SERVICE_NAME', 'value': name}, {'name': 'DEPLOYMENT_ENVIRONMENT', 'value': 'local'},
               {'name': 'OTEL_EXPORTER_OTLP_ENDPOINT', 'value': 'http://otel-collector:4317'},
               {'name': 'ENABLE_FAULT_INJECTION', 'value': 'false'}]
        for key, field in [('UID', 'metadata.uid'), ('NAME', 'metadata.name'), ('NAMESPACE', 'metadata.namespace')]:
            env.append({'name': 'SENTINELOPS_K8S_' + ('POD_' + key if key != 'NAMESPACE' else key),
                        'valueFrom': {'fieldRef': {'fieldPath': field}}})
        if short in ('payment', 'inventory'):
            env.append({'name': 'DATABASE_URL', 'valueFrom': {'secretKeyRef': {
                'name': 'commerce-credentials', 'key': 'database-url'}}})
        else:
            env.extend([{'name': 'PAYMENT_URL', 'value': 'http://payment-service:8000'},
                        {'name': 'INVENTORY_URL', 'value': 'http://inventory-service:8000'}])
        deployment(name, images['application'], [8000], env=env, container=short, ready='/ready',
                   command=['uvicorn', 'services.' + short + '.app:app', '--host', '0.0.0.0', '--port', '8000'])
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True, help='JSON mapping of five exact registry digests')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    documents = render(json.loads(args.images.read_text()))
    with args.output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__': main()
