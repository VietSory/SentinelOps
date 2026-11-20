"""Render the API/UI deployment offline; no worker/cloud/source adapter is implied.

Uses OIDC public configuration and existing control/action-reader DB Secret references.
Default inference is unavailable. An optional read-only model PVC/reference is
only wiring: the existing registry must validate its content at runtime.
LLM explanations are opt-in and use an existing gateway credential Secret.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
           action_read_secret: str | None = None,
           model_claim: str | None = None, model_release_ref: str | None = None,
           login_domain: str | None = None, approval_scope: str = 'sentinelops/remediation.approve',
           controller_sidecar: bool = False, service_type: str = 'ClusterIP', node_port: int | None = None,
           llm_model: str | None = None, llm_base_url: str = 'https://api.openai.com/v1') -> list[dict]:
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
                      jwks_url=public_url(jwks_url), audience_claim=audience_claim,
                      login_domain=login_domain, approval_scope=approval_scope)
    if type(controller_sidecar) is not bool or service_type not in ('ClusterIP', 'NodePort'):
        raise ValueError('explicit sidecar boolean and ClusterIP/NodePort Service required')
    enable_llm = llm_model is not None
    if enable_llm and (type(llm_model) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', llm_model)):
        raise ValueError('explicit LLM model ID required')
    if enable_llm and (type(llm_base_url) is not str
                       or not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~-]+)*', llm_base_url)):
        raise ValueError('LLM base URL must be credential-free HTTPS')
    if (service_type == 'NodePort' and (type(node_port) is not int or not 30000 <= node_port <= 32767)
        or service_type == 'ClusterIP' and node_port is not None):
        raise ValueError('NodePort requires an explicit port in 30000..32767; ClusterIP has no node port')
    if service_type == 'NodePort' and login_domain is None:
        raise ValueError('edge exposure requires Cognito hosted-login configuration')
    if controller_sidecar and (model_claim is not None or action_read_secret is not None or login_domain is None):
        raise ValueError('controller sidecar requires hosted login and owns its mounts/credentials in worker renderer')
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
            'SENTINELOPS_OIDC_OPERATOR_SCOPE': oidc.operator_scope,
            'SENTINELOPS_OIDC_APPROVAL_SCOPE': oidc.approval_scope}
    if login_domain is not None:
        data['SENTINELOPS_OIDC_LOGIN_DOMAIN'] = login_domain
    if enable_llm:
        data.update(SENTINELOPS_LLM_BASE_URL=llm_base_url, SENTINELOPS_LLM_MODEL=llm_model)
    service = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(NAME), 'spec': {
        'type': service_type, 'selector': {'app': 'remediation-controller' if controller_sidecar else NAME},
        'ports': [{'name': 'http', 'port': 8000, 'targetPort': 'http',
                   **({'nodePort': node_port} if node_port is not None else {})}]}}
    if controller_sidecar:
        # Do not attach the controller's RWO PVC to another Deployment. This
        # ConfigMap and Service select its OIDC sidecar on the same shared store.
        return [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(CONFIG), 'data': data}, service]
    volumes = [{'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]
    mounts = [{'name': 'tmp', 'mountPath': '/tmp'}]
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
    if enable_llm:
        environment_variables.append({'name': 'SENTINELOPS_LLM_API_KEY', 'valueFrom': {
            'secretKeyRef': {'name': 'sentinelops-llm', 'key': 'SENTINELOPS_LLM_API_KEY', 'optional': False}}})
    container = {'name': 'api', 'image': image, 'imagePullPolicy': 'IfNotPresent',
        'command': ['python', '-m', 'uvicorn', 'sentinelops.main:app', '--host', '0.0.0.0',
                    '--port', '8000', '--no-access-log'],
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
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE, 'labels': labels}},
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
                                        'seccompProfile': {'type': 'RuntimeDefault'}},
                    'containers': [container], 'volumes': volumes}}}},
        service,
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
    parser.add_argument('--login-domain')
    parser.add_argument('--approval-scope', default='sentinelops/remediation.approve')
    parser.add_argument('--controller-sidecar', action='store_true')
    parser.add_argument('--llm-model', help='Enable LLM explanations with this model ID and the sentinelops-llm Secret')
    parser.add_argument('--llm-base-url', default='https://api.openai.com/v1')
    parser.add_argument('--service-type', choices=('ClusterIP', 'NodePort'), default='ClusterIP')
    parser.add_argument('--node-port', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop('output')
    documents = render(**args)
    with output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__':
    main()
