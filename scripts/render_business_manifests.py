"""Offline business-service manifests for the declared sentinelops namespace.

No deployment, registry lookup, credentials, database provisioning, control-plane
workers, model activation or remediation authority. Commerce DB/Secret and the
OTLP collector must be provisioned separately. This is not source-parity evidence.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

from scripts.kubernetes_testbed import SERVICES
from sentinelops.remediation.domain import image_digest

NAMESPACE = 'sentinelops'  # Existing infra/aws/iam.tf workload namespace; no sandbox-policy change.
CONFIG = 'sentinelops-business-runtime'
ACCOUNT = 'business-services'
LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')


def _label(value, name):
    if not isinstance(value, str) or not LABEL.fullmatch(value):
        raise ValueError(name + ' must be a DNS label of at most 63 characters')
    return value


def _endpoint(value):
    if not isinstance(value, str):
        raise ValueError('an explicit credential-free HTTP(S) OTLP endpoint with a port is required')
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ('http', 'https') and parts.hostname and parts.port
                 and parts.username is None and parts.password is None and not parts.query and not parts.fragment
                 and parts.path in ('', '/') and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('an explicit credential-free HTTP(S) OTLP endpoint with a port is required')
    return value.rstrip('/')


def render(image: str, *, environment: str, otlp_endpoint: str, commerce_secret: str) -> list[dict]:
    image_digest(image)
    _label(environment, 'environment')
    _label(commerce_secret, 'commerce secret reference')
    endpoint = _endpoint(otlp_endpoint)
    labels = {'app.kubernetes.io/part-of': 'sentinelops'}

    def metadata(name):
        return {'name': name, 'namespace': NAMESPACE, 'labels': dict(labels)}

    documents = [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE, 'labels': dict(labels)}},
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata(ACCOUNT),
         'automountServiceAccountToken': False},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(CONFIG),
         'data': {'DEPLOYMENT_ENVIRONMENT': environment, 'OTEL_EXPORTER_OTLP_ENDPOINT': endpoint,
                  'ENABLE_FAULT_INJECTION': 'false'}},
    ]
    for name in SERVICES:
        short = name.removesuffix('-service')
        env = [{'name': 'SERVICE_NAME', 'value': name}]
        for variable, field in (('POD_UID', 'uid'), ('POD_NAME', 'name'), ('NAMESPACE', 'namespace')):
            env.append({'name': 'SENTINELOPS_K8S_' + variable,
                        'valueFrom': {'fieldRef': {'fieldPath': 'metadata.' + field}}})
        if name == 'checkout-service':
            env.extend([{'name': 'PAYMENT_URL', 'value': 'http://payment-service:8000'},
                        {'name': 'INVENTORY_URL', 'value': 'http://inventory-service:8000'}])
        else:
            env.append({'name': 'DATABASE_URL', 'valueFrom': {'secretKeyRef': {
                'name': commerce_secret, 'key': 'database-url', 'optional': False}}})
        container = {
            'name': short, 'image': image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['uvicorn', 'services.' + short + '.app:app', '--host', '0.0.0.0', '--port', '8000'],
            'ports': [{'name': 'http', 'containerPort': 8000}],
            'envFrom': [{'configMapRef': {'name': CONFIG}}], 'env': env,
            'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'},
                          'limits': {'cpu': '500m', 'memory': '384Mi'}},
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                'capabilities': {'drop': ['ALL']}},
            'volumeMounts': [{'name': 'tmp', 'mountPath': '/tmp'}],
            'startupProbe': {'httpGet': {'path': '/health', 'port': 'http'},
                             'periodSeconds': 2, 'timeoutSeconds': 2, 'failureThreshold': 30},
            'livenessProbe': {'httpGet': {'path': '/health', 'port': 'http'},
                              'periodSeconds': 10, 'timeoutSeconds': 2},
            'readinessProbe': {'httpGet': {'path': '/ready', 'port': 'http'},
                               'periodSeconds': 2, 'timeoutSeconds': 2},
        }
        documents.append({'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata(name),
            'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': name}},
                     'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}},
                     'template': {'metadata': {'labels': {**labels, 'app': name}}, 'spec': {
                         'serviceAccountName': ACCOUNT, 'automountServiceAccountToken': False,
                         'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001,
                                             'seccompProfile': {'type': 'RuntimeDefault'}},
                         'containers': [container], 'volumes': [{'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]}}}})
        documents.append({'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(name),
            'spec': {'type': 'ClusterIP', 'selector': {'app': name},
                     'ports': [{'name': 'http', 'port': 8000, 'targetPort': 'http'}]}})
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application-image', required=True, help='Exact registry image@sha256 digest; never a tag')
    parser.add_argument('--environment', required=True)
    parser.add_argument('--otlp-endpoint', required=True)
    parser.add_argument('--commerce-secret', required=True, help='Existing Secret name, database-url key; not its value')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    documents = render(args.application_image, environment=args.environment,
                       otlp_endpoint=args.otlp_endpoint, commerce_secret=args.commerce_secret)
    with args.output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__':
    main()
