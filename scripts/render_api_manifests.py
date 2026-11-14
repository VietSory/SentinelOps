"""Render the API/UI deployment offline; no worker/cloud/source adapter is implied.

Uses OIDC public configuration and an existing control DB Secret reference.
Default inference is unavailable. An optional read-only model PVC/reference is
only wiring: the existing registry must validate its content at runtime.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

from scripts.render_business_manifests import NAMESPACE, _label
from sentinelops.api_settings import LOCAL_ENVIRONMENTS
from sentinelops.oidc import OidcConfig
from sentinelops.remediation.domain import image_digest

NAME = 'sentinelops-api'
CONFIG = 'sentinelops-api-runtime'
MODEL_REF = re.compile(r'artifact://manifests/[0-9a-f]{64}\.json')


def public_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError('an explicit public OIDC HTTPS URL is required')
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or any(c.isspace() for c in value) or parts.port == 0):
        raise ValueError('OIDC URLs must be explicit credential-free HTTPS URLs without query or fragment')
    return value


def render(image: str, *, environment: str, control_secret: str, issuer: str,
           audience: str, jwks_url: str, audience_claim: str = 'aud',
           model_claim: str | None = None, model_release_ref: str | None = None) -> list[dict]:
    image_digest(image)
    _label(environment, 'environment')
    _label(control_secret, 'control secret reference')
    if environment in LOCAL_ENVIRONMENTS:
        raise ValueError('this deployment requires a non-local environment and OIDC')
    oidc = OidcConfig(issuer=public_url(issuer), audience=audience,
                      jwks_url=public_url(jwks_url), audience_claim=audience_claim)
    if model_claim is not None or model_release_ref is not None:
        _label(model_claim, 'model claim reference')
        if not isinstance(model_release_ref, str) or not MODEL_REF.fullmatch(model_release_ref):
            raise ValueError('model release must be a checksummed artifact manifest reference')
    labels = {'app.kubernetes.io/part-of': 'sentinelops'}
    metadata = lambda name: {'name': name, 'namespace': NAMESPACE, 'labels': dict(labels)}
    data = {'SENTINELOPS_ENV': environment, 'SENTINELOPS_ENABLE_DEMO': 'false',
            'SENTINELOPS_OIDC_ISSUER': oidc.issuer, 'SENTINELOPS_OIDC_AUDIENCE': oidc.audience,
            'SENTINELOPS_OIDC_JWKS_URL': oidc.jwks_url,
            'SENTINELOPS_OIDC_AUDIENCE_CLAIM': oidc.audience_claim,
            'SENTINELOPS_OIDC_PRODUCER_SCOPE': oidc.producer_scope,
            'SENTINELOPS_OIDC_OPERATOR_SCOPE': oidc.operator_scope}
    volumes = [{'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]
    mounts = [{'name': 'tmp', 'mountPath': '/tmp'}]
    if model_claim is not None:
        data.update(SENTINELOPS_MODEL_STORE='/models', SENTINELOPS_MODEL_RELEASE_REF=model_release_ref)
        volumes.append({'name': 'models', 'persistentVolumeClaim': {'claimName': model_claim, 'readOnly': True}})
        mounts.append({'name': 'models', 'mountPath': '/models', 'readOnly': True})
    def probe():
        return {'httpGet': {'path': '/health', 'port': 'http'}, 'periodSeconds': 5, 'timeoutSeconds': 2}
    container = {'name': 'api', 'image': image, 'imagePullPolicy': 'IfNotPresent',
        'ports': [{'name': 'http', 'containerPort': 8000}],
        'envFrom': [{'configMapRef': {'name': CONFIG}}],
        'env': [{'name': 'CONTROL_DATABASE_URL', 'valueFrom': {'secretKeyRef': {
            'name': control_secret, 'key': 'database-url', 'optional': False}}}],
        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                            'capabilities': {'drop': ['ALL']}},
        'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                      'limits': {'cpu': '1', 'memory': '1Gi'}},
        'volumeMounts': mounts, 'startupProbe': {**probe(), 'failureThreshold': 24},
        'livenessProbe': probe(), 'readinessProbe': probe()}
    return [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE, 'labels': labels}},
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata('api'),
         'automountServiceAccountToken': False},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(CONFIG), 'data': data},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata(NAME), 'spec': {
            'replicas': 1, 'selector': {'matchLabels': {'app': NAME}},
            'template': {'metadata': {'labels': {**labels, 'app': NAME}, 'annotations': {
                'sentinelops.io/readiness-scope': 'HTTP server availability; dependency readiness is exposed by the API'}},
                'spec': {'serviceAccountName': 'api', 'automountServiceAccountToken': False,
                    'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001,
                                        'seccompProfile': {'type': 'RuntimeDefault'}},
                    'containers': [container], 'volumes': volumes}}}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(NAME), 'spec': {
            'type': 'ClusterIP', 'selector': {'app': NAME},
            'ports': [{'name': 'http', 'port': 8000, 'targetPort': 'http'}]}},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--environment', required=True)
    parser.add_argument('--control-secret', required=True)
    parser.add_argument('--issuer', required=True)
    parser.add_argument('--audience', required=True)
    parser.add_argument('--jwks-url', required=True)
    parser.add_argument('--audience-claim', choices=('aud', 'client_id'), default='aud')
    parser.add_argument('--model-claim')
    parser.add_argument('--model-release-ref')
    parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop('output')
    documents = render(**args)
    with output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__':
    main()
