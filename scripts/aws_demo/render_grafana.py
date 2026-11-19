"""Render Grafana/Cognito manifests offline; never read credentials or call a cluster."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = ROOT / 'observability/grafana'
NAME = 'grafana'
NAMESPACE = 'sentinelops-b-actions'
# Docker Hub, resolved 2026-11-18 with WSL Ubuntu:
# docker buildx imagetools inspect docker.io/grafana/grafana:12.2.0
# Pin the multi-platform manifest list (including linux/amd64 for m7i-flex).
IMAGE = ('docker.io/grafana/grafana:12.2.0@sha256:'
         '74144189b38447facf737dfd0f3906e42e0776212bf575dc3334c3609183adf7')
PROMETHEUS_URL = 'http://prometheus.sentinelops-b-actions.svc.cluster.local:9090'
STORAGE_CLASS = 'sentinelops-gp3-kms'
HEALTH_PATH = '/grafana/api/health'


def dns_name(value: str, field: str) -> str:
    if (not isinstance(value, str) or len(value) > 253
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                   for part in value.split('.'))):
        raise ValueError(f'{field} must be a lowercase DNS name without scheme, port or path')
    return value


def public_url(value: str, field: str) -> str:
    """Exclude INI/environment-expression injection as well as URL credentials."""
    if (not isinstance(value, str) or not value
            or re.search(r'''[\s\x00-\x1f\x7f\\'"$%{};]''', value)):
        raise ValueError(f'{field} must be an explicit credential-free HTTPS URL')
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or parts.port not in (None, 443)):
        raise ValueError(f'{field} must be HTTPS without credentials, query or fragment')
    dns_name(parts.hostname, field)
    return value


def secret_key(value: str, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,253}', value):
        raise ValueError(f'{field} must be a Kubernetes Secret data key')
    return value


def render(*, cloudfront_domain: str, cognito_issuer: str, authorize_url: str,
           token_url: str, userinfo_url: str, client_id: str,
           oauth_secret: str = 'grafana-oauth', oauth_secret_key: str = 'client-secret',
           admin_secret: str = 'grafana-admin', admin_password_key: str = 'admin-password',
           encryption_key_key: str = 'secret-key', service_type: str = 'ClusterIP',
           node_port: int | None = None, storage: str = 'pvc',
           emergency_admin_login: bool = False) -> list[dict]:
    domain = dns_name(cloudfront_domain, 'cloudfront_domain')
    issuer = public_url(cognito_issuer, 'cognito_issuer')
    # The issuer is the user-pool URL, NOT the hosted-login domain.
    if not re.fullmatch(r'https://cognito-idp\.[a-z]{2}(?:-[a-z]+)+-\d\.amazonaws\.com'
                        r'(?:\.cn)?/[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+', issuer):
        raise ValueError('cognito_issuer must be a Cognito user-pool issuer URL')
    issuer_parts = urlsplit(issuer)
    if issuer_parts.hostname.split('.')[1] != issuer_parts.path[1:].split('_')[0]:
        raise ValueError('Cognito issuer region and user-pool region must match')
    endpoints = [public_url(value, field) for value, field in (
        (authorize_url, 'authorize_url'), (token_url, 'token_url'), (userinfo_url, 'userinfo_url'))]
    if [urlsplit(url).path for url in endpoints] != ['/oauth2/authorize', '/oauth2/token', '/oauth2/userInfo']:
        raise ValueError('Cognito endpoints require /oauth2/authorize, /oauth2/token and /oauth2/userInfo')
    if len({urlsplit(url).netloc for url in endpoints}) != 1:
        raise ValueError('Cognito endpoints must use the same hosted-login origin')
    if not isinstance(client_id, str) or not re.fullmatch(r'[a-z0-9]{1,128}', client_id):
        raise ValueError('client_id must be a Cognito app client ID')
    for value, field in ((oauth_secret, 'oauth_secret'), (admin_secret, 'admin_secret')):
        dns_name(value, field)
    for value, field in ((oauth_secret_key, 'oauth_secret_key'),
                         (admin_password_key, 'admin_password_key'), (encryption_key_key, 'encryption_key_key')):
        secret_key(value, field)
    if admin_password_key == encryption_key_key:
        raise ValueError('admin password and encryption key require different Secret data keys')
    if service_type not in ('ClusterIP', 'NodePort'):
        raise ValueError('service_type must be ClusterIP or NodePort')
    if service_type == 'NodePort':
        if type(node_port) is not int or not 30000 <= node_port <= 32767:
            raise ValueError('NodePort requires an explicit node_port in 30000..32767 matching lane edge')
    elif node_port is not None:
        raise ValueError('node_port is only valid with service_type=NodePort')
    if storage not in ('pvc', 'empty-dir'):
        raise ValueError('storage must be pvc or empty-dir')
    if type(emergency_admin_login) is not bool:
        raise ValueError('emergency_admin_login must be boolean')

    root_url = f'https://{domain}/grafana/'
    local_login = str(emergency_admin_login).lower()
    config = f'''[server]
protocol = http
http_port = 3000
domain = {domain}
root_url = {root_url}
serve_from_sub_path = true

[paths]
data = /var/lib/grafana
logs = /var/lib/grafana/log
plugins = /var/lib/grafana/plugins
provisioning = /etc/grafana/provisioning

[log]
mode = console

[security]
admin_user = admin
cookie_secure = true
cookie_samesite = lax
disable_gravatar = true

[auth]
disable_login_form = {str(not emergency_admin_login).lower()}
oauth_allow_insecure_email_lookup = false

[auth.anonymous]
enabled = false

[auth.basic]
enabled = {local_login}

[users]
allow_sign_up = false
allow_org_create = false
auto_assign_org_role = Viewer
viewers_can_edit = false

[auth.generic_oauth]
enabled = true
name = Cognito
allow_sign_up = true
auto_login = {str(not emergency_admin_login).lower()}
client_id = {client_id}
scopes = openid email profile
auth_url = {endpoints[0]}
token_url = {endpoints[1]}
api_url = {endpoints[2]}
auth_style = InHeader
use_pkce = true
use_refresh_token = true
login_attribute_path = sub
email_attribute_path = email
# Generic OAuth has no issuer_url/discovery setting in Grafana 12.2.0.
# Require the configured Cognito issuer in the ID-token role mapping.
role_attribute_path = iss == '{issuer}' && 'Viewer'
role_attribute_strict = true
allow_assign_grafana_admin = false
skip_org_role_sync = false
tls_skip_verify_insecure = false

[snapshots]
# Snapshots (local or snapshots.raintank.io) are readable by anyone with the link,
# which would bypass Cognito; the demo shares dashboards only behind login.
enabled = false
external_enabled = false

[public_dashboards]
# Shared (public) dashboards are also readable without login.
enabled = false

[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false

[plugins]
preinstall_disabled = true
plugin_admin_enabled = false

[live]
max_connections = 0

[unified_alerting]
enabled = false
'''
    datasource = yaml.safe_load((GRAFANA / 'provisioning/datasources/prometheus.yaml').read_text(encoding='utf-8'))
    entry = next(item for item in datasource['datasources'] if item['uid'] == 'sentinelops-prometheus')
    entry['url'] = PROMETHEUS_URL
    provider = (GRAFANA / 'provisioning/dashboards/sentinelops.yaml').read_text(encoding='utf-8')
    dashboard = (GRAFANA / 'dashboards/sentinelops-workers.json').read_text(encoding='utf-8')
    labels = {'app': NAME, 'app.kubernetes.io/name': NAME, 'app.kubernetes.io/part-of': 'sentinelops'}

    def metadata(name):
        return {'name': name, 'namespace': NAMESPACE, 'labels': dict(labels)}

    configs = [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata('grafana-config'),
         'data': {'grafana.ini': config}},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata('grafana-provisioning'),
         'data': {'prometheus.yaml': yaml.safe_dump(datasource, sort_keys=False), 'sentinelops.yaml': provider}},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata('grafana-dashboards'),
         'data': {'sentinelops-workers.json': dashboard}},
    ]
    checksum = hashlib.sha256(json.dumps(configs, sort_keys=True).encode()).hexdigest()

    def secret_env(name, secret, key):
        return {'name': name, 'valueFrom': {'secretKeyRef': {'name': secret, 'key': key, 'optional': False}}}

    def probe():
        # Grafana strips /grafana before handling /api/health. No OAuth redirect.
        return {'httpGet': {'path': HEALTH_PATH, 'port': 'http'}, 'periodSeconds': 10, 'timeoutSeconds': 3}

    container = {
        'name': NAME, 'image': IMAGE, 'imagePullPolicy': 'IfNotPresent',
        'ports': [{'name': 'http', 'containerPort': 3000, 'protocol': 'TCP'}],
        'env': [
            secret_env('GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET', oauth_secret, oauth_secret_key),
            secret_env('GF_SECURITY_ADMIN_PASSWORD', admin_secret, admin_password_key),
            secret_env('GF_SECURITY_SECRET_KEY', admin_secret, encryption_key_key),
        ],
        'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                      'limits': {'cpu': '500m', 'memory': '384Mi'}},
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 472, 'runAsGroup': 472,
                            'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                            'capabilities': {'drop': ['ALL']}},
        'startupProbe': {**probe(), 'failureThreshold': 30},
        'readinessProbe': probe(), 'livenessProbe': {**probe(), 'failureThreshold': 6},
        'volumeMounts': [
            {'name': 'config', 'mountPath': '/etc/grafana/grafana.ini', 'subPath': 'grafana.ini', 'readOnly': True},
            {'name': 'provisioning', 'mountPath': '/etc/grafana/provisioning', 'readOnly': True},
            {'name': 'data', 'mountPath': '/var/lib/grafana'},
            {'name': 'dashboards', 'mountPath': '/var/lib/grafana/dashboards', 'readOnly': True},
            {'name': 'tmp', 'mountPath': '/tmp'},
        ],
    }
    service = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(NAME), 'spec': {
        'type': service_type, 'selector': {'app': NAME},
        'ports': [{'name': 'http', 'port': 3000, 'targetPort': 'http', 'protocol': 'TCP'}]}}
    if service_type == 'NodePort':
        service['spec']['ports'][0]['nodePort'] = node_port
        # One replica; both private nodes must forward to whichever node hosts it.
        service['spec']['externalTrafficPolicy'] = 'Cluster'
    deployment = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata(NAME), 'spec': {
        'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app': NAME}},
        'template': {'metadata': {'labels': dict(labels), 'annotations': {'sentinelops.io/config-sha256': checksum}},
                     'spec': {'automountServiceAccountToken': False,
                              'securityContext': {'runAsNonRoot': True, 'runAsUser': 472, 'runAsGroup': 472,
                                                  'fsGroup': 472, 'fsGroupChangePolicy': 'OnRootMismatch',
                                                  'seccompProfile': {'type': 'RuntimeDefault'}},
                              'containers': [container], 'volumes': [
                                  {'name': 'config', 'configMap': {'name': 'grafana-config'}},
                                  {'name': 'provisioning', 'configMap': {'name': 'grafana-provisioning', 'items': [
                                      {'key': 'prometheus.yaml', 'path': 'datasources/prometheus.yaml'},
                                      {'key': 'sentinelops.yaml', 'path': 'dashboards/sentinelops.yaml'}]}},
                                  {'name': 'dashboards', 'configMap': {'name': 'grafana-dashboards'}},
                                  {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}},
                                  {'name': 'data', **({'persistentVolumeClaim': {'claimName': 'grafana-data'}}
                                                    if storage == 'pvc' else {'emptyDir': {'sizeLimit': '1Gi'}})},
                              ]}}}}
    # Namespace is already owned by render_all/testbed; avoid resetting its labels.
    documents = list(configs)
    if storage == 'pvc':
        documents.append({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': metadata('grafana-data'),
                          'spec': {'accessModes': ['ReadWriteOnce'], 'storageClassName': STORAGE_CLASS,
                                   'resources': {'requests': {'storage': '1Gi'}}}})
    return [*documents, service, deployment]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('cloudfront-domain', 'cognito-issuer', 'authorize-url', 'token-url', 'userinfo-url', 'client-id'):
        parser.add_argument('--' + name, required=True)
    for name, default in (('oauth-secret', 'grafana-oauth'), ('oauth-secret-key', 'client-secret'),
                          ('admin-secret', 'grafana-admin'), ('admin-password-key', 'admin-password'),
                          ('encryption-key-key', 'secret-key')):
        parser.add_argument('--' + name, default=default, help='Existing Kubernetes Secret name/key reference')
    parser.add_argument('--service-type', choices=('ClusterIP', 'NodePort'), default='ClusterIP')
    parser.add_argument('--node-port', type=int, help='Explicit NodePort matching lane edge; 30000..32767')
    parser.add_argument('--storage', choices=('pvc', 'empty-dir'), default='pvc')
    parser.add_argument('--emergency-admin-login', action='store_true', help='Temporarily enable local password login')
    parser.add_argument('--output', type=Path, help='New YAML file; defaults to stdout')
    args = vars(parser.parse_args())
    output = args.pop('output')
    try:
        documents = render(**args)
        content = yaml.safe_dump_all(documents, sort_keys=False, allow_unicode=True)
        if output is None:
            sys.stdout.write(content)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(content)
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
