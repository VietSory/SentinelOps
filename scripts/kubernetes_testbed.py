"""Offline local/EKS demo manifests; no deployment or source approval.

All images must be registry digests. The caller provisions commerce-credentials
separately; render never accepts or emits a password/DSN/Secret object.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml

from sentinelops.remediation.domain import image_digest

PROJECT = Path(__file__).resolve().parents[1]
# Frozen Payment known-good contract: D-10 pins sentinelops-b-actions for both targets.
NAMESPACE = 'sentinelops-b-actions'
IMAGE_KEYS = {'application', 'postgres', 'otel-collector', 'prometheus', 'jaeger'}
SERVICES = ('checkout-service', 'payment-service', 'inventory-service')
SOURCE_PROFILE = 'prometheus-increase-60s-5s/v2'
EKS_APPLICATION = re.compile(
    r'[0-9]{12}\.dkr\.ecr\.ap-southeast-1\.amazonaws\.com/'
    r'sentinelops/application@sha256:[0-9a-f]{64}')
# CPU requests total 1200m; memory requests total 3840Mi (about 4 GB).
EKS_RESOURCES = {
    **{name: ('100m', '256Mi', '500m', '512Mi') for name in SERVICES},
    'postgres': ('250m', '1024Mi', '1000m', '2048Mi'),
    'otel-collector': ('200m', '512Mi', '500m', '768Mi'),
    'prometheus': ('250m', '1024Mi', '1000m', '2048Mi'),
    'jaeger': ('200m', '512Mi', '500m', '1024Mi'),
}


def render(images, *, target='local'):
    if target not in ('local', 'eks'):
        raise ValueError('target must be local or eks')
    if set(images) != IMAGE_KEYS:
        raise ValueError('exact Kubernetes image set required')
    for image in images.values(): image_digest(image)
    eks = target == 'eks'
    if eks and not EKS_APPLICATION.fullmatch(images['application']):
        raise ValueError('EKS requires ap-southeast-1 ECR sentinelops/application@sha256 digest')
    namespace = NAMESPACE
    documents = [{'apiVersion': 'v1', 'kind': 'Namespace',
                  'metadata': {'name': namespace, 'labels': {'sentinelops.dev/lane': 'B'}}}]

    def config(name, data):
        documents.append({'apiVersion': 'v1', 'kind': 'ConfigMap',
                          'metadata': {'name': name, 'namespace': namespace}, 'data': data})

    config('commerce-init', {'001-init.sql': (PROJECT / 'infra/local/init.sql').read_text()})
    config('otel-config', {'config.yaml': (PROJECT / 'observability/otel-collector-config.yaml').read_text()})
    config('prometheus-config', {'prometheus.yml': (PROJECT / 'observability/prometheus.yml').read_text()})
    # Every file listed in observability/prometheus.yml rule_files must be in this ConfigMap:
    # Prometheus treats rule_files as globs, so a missing file loads nothing and raises no error.
    config('prometheus-rules', {name: (PROJECT / 'observability/rules' / name).read_text()
                               for name in ('sentinelops-workers.yml', 'sentinelops-business.yml')})
    config_hash = hashlib.sha256(json.dumps(documents, sort_keys=True).encode()).hexdigest()

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
        if eks:
            cpu, memory, cpu_limit, memory_limit = EKS_RESOURCES[name]
            spec['resources'] = {'requests': {'cpu': cpu, 'memory': memory},
                                 'limits': {'cpu': cpu_limit, 'memory': memory_limit}}
        if name in SERVICES:
            spec['securityContext'] = {'allowPrivilegeEscalation': False, 'runAsNonRoot': True,
                                       'runAsUser': 10001, 'capabilities': {'drop': ['ALL']}}
        if name == 'prometheus': pod['securityContext'] = {'fsGroup': 65534}
        documents.append({'apiVersion': 'apps/v1', 'kind': 'Deployment',
            'metadata': {'name': name, 'namespace': namespace},
            'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': name}},
                     'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}},
                     'template': {'metadata': {'labels': {'app': name,
                         'app.kubernetes.io/part-of': 'sentinelops'}, 'annotations': {
                         'sentinelops.io/config-sha256': config_hash,
                         **({'sentinelops.io/source-profile': SOURCE_PROFILE} if eks else {})}}, 'spec': pod}}})
        documents.append({'apiVersion': 'v1', 'kind': 'Service',
            'metadata': {'name': name, 'namespace': namespace},
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
                {'name': 'rules', 'mountPath': '/etc/prometheus/rules', 'readOnly': True},
                {'name': 'data', 'mountPath': '/prometheus'}],
        volumes=[{'name': 'config', 'configMap': {'name': 'prometheus-config'}},
                 {'name': 'rules', 'configMap': {'name': 'prometheus-rules'}}, {'name': 'data', 'emptyDir': {}}])
    for name in SERVICES:
        short = name.removesuffix('-service')
        dev_faults = eks and short in ('payment', 'inventory')
        env = [{'name': 'SERVICE_NAME', 'value': name},
               {'name': 'DEPLOYMENT_ENVIRONMENT', 'value': 'dev' if eks else 'local'},
               {'name': 'OTEL_EXPORTER_OTLP_ENDPOINT', 'value': 'http://otel-collector:4317'},
               {'name': 'ENABLE_FAULT_INJECTION', 'value': 'true' if dev_faults else 'false'}]
        if dev_faults:
            env.append({'name': 'ALLOW_DEV_FAULT_INJECTION', 'value': 'true'})
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
    if eks:
        for document in documents:
            if document['kind'] != 'Deployment':
                continue
            name = document['metadata']['name']
            container = document['spec']['template']['spec']['containers'][0]
            if name == 'postgres':
                document['spec']['strategy'] = {'type': 'Recreate'}
                container['readinessProbe'] = {'exec': {'command': [
                    'pg_isready', '-U', 'sentinel', '-d', 'sentinelops']},
                    'periodSeconds': 5, 'timeoutSeconds': 2}
            elif name == 'jaeger':
                container['env'].append({'name': 'GOMEMLIMIT', 'value': '768MiB'})
                container['readinessProbe'] = {'tcpSocket': {'port': 16686}, 'periodSeconds': 5}
            elif name == 'otel-collector':
                # Respect the image entrypoint (core or contrib collector binary).
                container.pop('command')
                container['args'] = ['--config=/etc/otelcol/config.yaml']
                container['env'].append({'name': 'GOMEMLIMIT', 'value': '512MiB'})
            elif name == 'prometheus':
                container['command'].append('--storage.tsdb.retention.size=1GB')
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True, help='JSON mapping of five exact registry digests')
    parser.add_argument('--target', choices=('local', 'eks'), default='local',
                        help='EKS: sentinelops-b-actions namespace, ECR application digest and demo resource budget')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    documents = render(json.loads(args.images.read_text()), target=args.target)
    with args.output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__': main()
