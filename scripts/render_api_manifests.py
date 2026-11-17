"""Render the API/UI deployment offline; no worker/cloud/source adapter is implied.

Uses OIDC public configuration and existing control/action-reader DB Secret references.
Default inference is unavailable. An optional read-only model PVC/reference is
only wiring: the existing registry must validate its content at runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

from scripts.kubernetes_testbed import NAMESPACE as DEMO_NAMESPACE
from scripts.render_business_manifests import NAMESPACE, _label
from sentinelops.api_settings import LOCAL_ENVIRONMENTS
from sentinelops.oidc import OidcConfig
from sentinelops.remediation.domain import image_digest

NAME = 'sentinelops-api'
CONFIG = 'sentinelops-api-runtime'
MODEL_REF = re.compile(r'artifact://manifests/[0-9a-f]{64}\.json')
# Same private-credential copy as scripts/render_worker_manifests.py (which imports MODEL_REF from here,
# so the constants are repeated rather than imported; a test keeps them identical).
CREDENTIAL_ROOT = '/credentials'
COPY_CREDENTIALS = (
    'import os, shutil, sys; from pathlib import Path; os.umask(0o077); '
    '[shutil.copyfile(p, Path(sys.argv[1]) / Path(p).name) for p in sys.argv[2:]]'
)


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
           action_read_secret: str | None = None,
           model_claim: str | None = None, model_release_ref: str | None = None,
           control_database_user: str | None = None, database_ca_config: str | None = None,
           namespace: str = NAMESPACE) -> list[dict]:
    image_digest(image)
    _label(environment, 'environment')
    _label(control_secret, 'control secret reference')
    if action_read_secret is not None:
        _label(action_read_secret, 'action read secret reference')
        if action_read_secret == control_secret:
            raise ValueError('action reader requires a separate Secret from the control runtime')
    if environment in LOCAL_ENVIRONMENTS:
        raise ValueError('this deployment requires a non-local environment and OIDC')
    oidc = OidcConfig(issuer=public_url(issuer), audience=audience,
                      jwks_url=public_url(jwks_url), audience_claim=audience_claim)
    if model_claim is not None or model_release_ref is not None:
        _label(model_claim, 'model claim reference')
        if not isinstance(model_release_ref, str) or not MODEL_REF.fullmatch(model_release_ref):
            raise ValueError('model release must be a checksummed artifact manifest reference')
    if namespace not in (NAMESPACE, DEMO_NAMESPACE):
        raise ValueError('namespace must be the workload namespace or the lane B demo namespace')
    if (control_database_user is None) != (database_ca_config is None):
        raise ValueError('a remote control database needs both the user and the CA ConfigMap')
    if control_database_user is not None:
        if control_database_user != 'control_writer':
            raise ValueError('the API reads the control store as control_writer, like the cloud workers')
        _label(database_ca_config, 'database CA ConfigMap reference')
    labels = {'app.kubernetes.io/part-of': 'sentinelops'}
    metadata = lambda name: {'name': name, 'namespace': namespace, 'labels': dict(labels)}
    data = {'SENTINELOPS_ENV': environment, 'SENTINELOPS_ENABLE_DEMO': 'false',
            'SENTINELOPS_OIDC_ISSUER': oidc.issuer, 'SENTINELOPS_OIDC_AUDIENCE': oidc.audience,
            'SENTINELOPS_OIDC_JWKS_URL': oidc.jwks_url,
            'SENTINELOPS_OIDC_AUDIENCE_CLAIM': oidc.audience_claim,
            'SENTINELOPS_OIDC_PRODUCER_SCOPE': oidc.producer_scope,
            'SENTINELOPS_OIDC_OPERATOR_SCOPE': oidc.operator_scope}
    volumes = [{'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]
    mounts = [{'name': 'tmp', 'mountPath': '/tmp'}]
    init_containers = []
    if control_database_user is not None:
        # Same contract as the cloud workers: credential-free database-url plus a private password file
        # copied by an init container (0600, owned by the API user) and the RDS CA for verify-full TLS.
        data.update(CONTROL_DATABASE_USER=control_database_user,
                    CONTROL_DATABASE_PASSWORD_FILE=CREDENTIAL_ROOT + '/database-password',
                    SSL_CERT_FILE='/etc/sentinelops/tls/ca-bundle.crt')
        volumes += [
            {'name': 'credentials', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Ki'}},
            {'name': 'database-password', 'secret': {'secretName': control_secret, 'optional': False,
                'defaultMode': 0o440, 'items': [{'key': 'password', 'path': 'database-password'}]}},
            {'name': 'database-ca', 'configMap': {'name': database_ca_config, 'optional': False,
                'items': [{'key': 'ca-bundle.crt', 'path': 'ca-bundle.crt'}]}}]
        mounts += [{'name': 'credentials', 'mountPath': CREDENTIAL_ROOT, 'readOnly': True},
                   {'name': 'database-ca', 'mountPath': '/etc/sentinelops/tls', 'readOnly': True}]
        init_containers.append({'name': 'private-credentials', 'image': image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python', '-c', COPY_CREDENTIALS],
            'args': [CREDENTIAL_ROOT, '/secret-database/database-password'],
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                'capabilities': {'drop': ['ALL']}},
            'volumeMounts': [{'name': 'credentials', 'mountPath': CREDENTIAL_ROOT},
                             {'name': 'database-password', 'mountPath': '/secret-database', 'readOnly': True}],
            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}}})
    if model_claim is not None:
        data.update(SENTINELOPS_MODEL_STORE='/models', SENTINELOPS_MODEL_RELEASE_REF=model_release_ref)
        volumes.append({'name': 'models', 'persistentVolumeClaim': {'claimName': model_claim, 'readOnly': True}})
        mounts.append({'name': 'models', 'mountPath': '/models', 'readOnly': True})
    # ConfigMap environment values are read only when a pod starts. Bind the
    # template to their bytes so changing OIDC or a release on the same PVC
    # replaces the process with the newly selected runtime configuration.
    config_sha256 = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    def probe():
        return {'httpGet': {'path': '/health', 'port': 'http'}, 'periodSeconds': 5, 'timeoutSeconds': 2}
    environment_variables = [{'name': 'CONTROL_DATABASE_URL', 'valueFrom': {'secretKeyRef': {
        'name': control_secret, 'key': 'database-url', 'optional': False}}}]
    if action_read_secret is not None:
        environment_variables.append({'name': 'SENTINELOPS_ACTION_READ_DATABASE_URL', 'valueFrom': {
            'secretKeyRef': {'name': action_read_secret, 'key': 'database-url', 'optional': False}}})
    container = {'name': 'api', 'image': image, 'imagePullPolicy': 'IfNotPresent',
        'ports': [{'name': 'http', 'containerPort': 8000}],
        'envFrom': [{'configMapRef': {'name': CONFIG}}],
        'env': environment_variables,
        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                            'capabilities': {'drop': ['ALL']}},
        'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                      'limits': {'cpu': '1', 'memory': '1Gi'}},
        'volumeMounts': mounts, 'startupProbe': {**probe(), 'failureThreshold': 24},
        'livenessProbe': probe(), 'readinessProbe': probe()}
    return [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace, 'labels': {
            **labels, **({'sentinelops.dev/lane': 'B'} if namespace == DEMO_NAMESPACE else {})}}},
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata('api'),
         'automountServiceAccountToken': False},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(CONFIG), 'data': data},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata(NAME), 'spec': {
            'replicas': 1, 'selector': {'matchLabels': {'app': NAME}},
            'template': {'metadata': {'labels': {**labels, 'app': NAME}, 'annotations': {
                'sentinelops.io/config-sha256': config_sha256,
                'sentinelops.io/readiness-scope': 'HTTP server availability; dependency readiness is exposed by the API'}},
                'spec': {'serviceAccountName': 'api', 'automountServiceAccountToken': False,
                    'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001,
                                        **({'runAsGroup': 10001, 'fsGroup': 10001} if init_containers else {}),
                                        'seccompProfile': {'type': 'RuntimeDefault'}},
                    **({'initContainers': init_containers} if init_containers else {}),
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
    parser.add_argument('--action-read-secret', help='Existing dedicated SELECT-only Secret name, database-url key')
    parser.add_argument('--issuer', required=True)
    parser.add_argument('--audience', required=True)
    parser.add_argument('--jwks-url', required=True)
    parser.add_argument('--audience-claim', choices=('aud', 'client_id'), default='aud')
    parser.add_argument('--model-claim')
    parser.add_argument('--model-release-ref')
    parser.add_argument('--control-database-user', choices=('control_writer',),
                        help='Remote control DB: credential-free database-url + password key in --control-secret')
    parser.add_argument('--database-ca-config', help='ConfigMap with key ca-bundle.crt (RDS CA) for verify-full TLS')
    parser.add_argument('--namespace', default=NAMESPACE, choices=(NAMESPACE, DEMO_NAMESPACE))
    parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop('output')
    documents = render(**args)
    with output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__':
    main()
