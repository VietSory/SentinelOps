"""Offline deployment/auth boundaries for the EKS Grafana renderer."""
from __future__ import annotations

import configparser
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.aws_demo import render_grafana as grafana


def inputs():
    return {'cloudfront_domain': 'demo.cloudfront.net',
            'cognito_issuer': 'https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_Example',
            'authorize_url': 'https://operators.auth.ap-southeast-1.amazoncognito.com/oauth2/authorize',
            'token_url': 'https://operators.auth.ap-southeast-1.amazoncognito.com/oauth2/token',
            'userinfo_url': 'https://operators.auth.ap-southeast-1.amazoncognito.com/oauth2/userInfo',
            'client_id': 'grafanaclient123'}


def resource(documents, kind, name=None):
    return next(doc for doc in documents if doc['kind'] == kind
                and (name is None or doc['metadata']['name'] == name))


def ini(documents):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(resource(documents, 'ConfigMap', 'grafana-config')['data']['grafana.ini'])
    return config


def test_image_identity_namespace_and_small_single_writer():
    docs = grafana.render(**inputs())
    assert len(docs) == 6
    assert {doc['metadata']['namespace'] for doc in docs} == {'sentinelops-b-actions'}
    assert {doc['kind'] for doc in docs} == {'ConfigMap', 'Deployment', 'Service', 'PersistentVolumeClaim'}
    deployment = resource(docs, 'Deployment')
    assert deployment['metadata']['name'] == 'grafana'
    assert deployment['spec']['replicas'] == 1
    assert deployment['spec']['strategy'] == {'type': 'Recreate'}
    pod = deployment['spec']['template']['spec']
    container = pod['containers'][0]
    assert container['image'] == (
        'docker.io/grafana/grafana:12.2.0@sha256:'
        '74144189b38447facf737dfd0f3906e42e0776212bf575dc3334c3609183adf7')
    assert container['resources'] == {'requests': {'cpu': '100m', 'memory': '128Mi'},
                                      'limits': {'cpu': '500m', 'memory': '384Mi'}}
    assert not pod.get('hostNetwork') and not pod.get('hostPID')
    assert not any('hostPath' in volume for volume in pod['volumes'])


def test_subpath_cognito_only_login_and_least_privilege():
    config = ini(grafana.render(**inputs()))
    assert config['server']['root_url'] == 'https://demo.cloudfront.net/grafana/'
    assert config['server'].getboolean('serve_from_sub_path')
    assert config['server']['protocol'] == 'http'  # TLS terminates at the edge.
    assert config['security'].getboolean('cookie_secure')
    assert config['security']['cookie_samesite'] == 'lax'
    assert not config['auth.anonymous'].getboolean('enabled')
    assert not config['auth.basic'].getboolean('enabled')
    assert config['auth'].getboolean('disable_login_form')
    assert not config['users'].getboolean('allow_sign_up')
    assert not config['users'].getboolean('viewers_can_edit')
    oauth = config['auth.generic_oauth']
    assert oauth.getboolean('enabled') and oauth.getboolean('auto_login')
    assert oauth['client_id'] == inputs()['client_id']
    assert oauth['auth_url'] == inputs()['authorize_url']
    assert oauth['token_url'] == inputs()['token_url']
    assert oauth['api_url'] == inputs()['userinfo_url']
    assert set(oauth['scopes'].split()) == {'openid', 'email', 'profile'}
    assert oauth['auth_style'] == 'InHeader'
    assert oauth.getboolean('use_pkce') and oauth.getboolean('use_refresh_token')
    assert oauth['login_attribute_path'] == 'sub'
    assert oauth['role_attribute_path'] == f"iss == '{inputs()['cognito_issuer']}' && 'Viewer'"
    assert oauth.getboolean('role_attribute_strict')
    assert not oauth.getboolean('allow_assign_grafana_admin')
    assert not oauth.getboolean('skip_org_role_sync')
    assert not oauth.getboolean('tls_skip_verify_insecure')
    assert 'issuer_url' not in oauth  # Unsupported by Grafana 12.2.0.
    assert config['plugins'].getboolean('preinstall_disabled')
    assert config['live'].getint('max_connections') == 0


def test_credentials_are_required_secret_references_and_never_rendered():
    docs = grafana.render(**inputs(), oauth_secret='custom.oauth', oauth_secret_key='cognito.secret',
                          admin_secret='custom-admin', admin_password_key='password', encryption_key_key='key')
    pod = resource(docs, 'Deployment')['spec']['template']['spec']
    refs = {entry['name']: entry['valueFrom']['secretKeyRef'] for entry in pod['containers'][0]['env']}
    assert refs == {
        'GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET': {'name': 'custom.oauth', 'key': 'cognito.secret', 'optional': False},
        'GF_SECURITY_ADMIN_PASSWORD': {'name': 'custom-admin', 'key': 'password', 'optional': False},
        'GF_SECURITY_SECRET_KEY': {'name': 'custom-admin', 'key': 'key', 'optional': False},
    }
    assert not any(doc['kind'] == 'Secret' for doc in docs)
    config = ini(docs)
    assert 'client_secret' not in config['auth.generic_oauth']
    assert 'admin_password' not in config['security']
    assert 'secret_key' not in config['security']


def test_emergency_admin_is_explicit_and_does_not_enable_guests_or_self_registration():
    config = ini(grafana.render(**inputs(), emergency_admin_login=True))
    assert config['auth.basic'].getboolean('enabled')
    assert not config['auth'].getboolean('disable_login_form')
    assert not config['auth.generic_oauth'].getboolean('auto_login')
    assert not config['auth.anonymous'].getboolean('enabled')
    assert not config['users'].getboolean('allow_sign_up')
    assert config['security']['admin_user'] == 'admin'


def test_non_root_readonly_and_all_writable_directories_have_volumes():
    pod = resource(grafana.render(**inputs()), 'Deployment')['spec']['template']['spec']
    container = pod['containers'][0]
    assert pod['automountServiceAccountToken'] is False
    assert pod['securityContext']['runAsNonRoot'] is True
    assert pod['securityContext']['runAsUser'] == pod['securityContext']['fsGroup'] == 472
    assert pod['securityContext']['seccompProfile'] == {'type': 'RuntimeDefault'}
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert container['securityContext']['allowPrivilegeEscalation'] is False
    assert container['securityContext']['capabilities'] == {'drop': ['ALL']}
    mounts = {mount['mountPath']: mount for mount in container['volumeMounts']}
    for path in ('/etc/grafana/grafana.ini', '/etc/grafana/provisioning', '/var/lib/grafana/dashboards'):
        assert mounts[path]['readOnly'] is True
    assert {mount['name'] for mount in container['volumeMounts']} == {volume['name'] for volume in pod['volumes']}
    assert mounts['/var/lib/grafana']['name'] == 'data'
    assert mounts['/tmp']['name'] == 'tmp'
    for name in ('startupProbe', 'readinessProbe', 'livenessProbe'):
        assert container[name]['httpGet'] == {'path': '/grafana/api/health', 'port': 'http'}


def test_repository_provisioning_and_dashboards_are_mounted_without_rewriting_the_source():
    docs = grafana.render(**inputs())
    source = grafana.GRAFANA / 'provisioning/datasources/prometheus.yaml'
    original = yaml.safe_load(source.read_text(encoding='utf-8'))
    expected = deepcopy(original)
    expected['datasources'][0]['url'] = 'http://prometheus.sentinelops-b-actions.svc.cluster.local:9090'
    provisioning = resource(docs, 'ConfigMap', 'grafana-provisioning')['data']
    assert yaml.safe_load(provisioning['prometheus.yaml']) == expected
    assert yaml.safe_load(source.read_text(encoding='utf-8')) == original
    assert original['datasources'][0]['url'] == 'http://prometheus:9090'
    assert provisioning['sentinelops.yaml'] == (
        grafana.GRAFANA / 'provisioning/dashboards/sentinelops.yaml').read_text(encoding='utf-8')
    dashboards = resource(docs, 'ConfigMap', 'grafana-dashboards')['data']
    paths = sorted((grafana.GRAFANA / 'dashboards').glob('*.json'))
    assert set(dashboards) == {
        'sentinelops-workers.json', 'sentinelops-incidents.json', 'sentinelops-slo.json'}
    assert list(dashboards) == [path.name for path in paths]
    assert dashboards == {path.name: path.read_text(encoding='utf-8') for path in paths}
    assert all(json.loads(dashboard)['editable'] is False for dashboard in dashboards.values())
    pod = resource(docs, 'Deployment')['spec']['template']['spec']
    volume = next(volume for volume in pod['volumes'] if volume['name'] == 'provisioning')
    assert volume['configMap']['items'] == [
        {'key': 'prometheus.yaml', 'path': 'datasources/prometheus.yaml'},
        {'key': 'sentinelops.yaml', 'path': 'dashboards/sentinelops.yaml'}]
    assert yaml.safe_load(provisioning['sentinelops.yaml'])['providers'][0]['options']['path'] == '/var/lib/grafana/dashboards'


@pytest.mark.parametrize('name', [
    'sentinelops-workers.json', 'sentinelops-incidents.json', 'sentinelops-slo.json'])
def test_config_and_dashboard_changes_restart_grafana(tmp_path, monkeypatch, name):
    before = grafana.render(**inputs())
    changed = grafana.render(**{**inputs(), 'cloudfront_domain': 'new.cloudfront.net'})
    def checksum(docs):
        return resource(docs, 'Deployment')['spec']['template']['metadata']['annotations']['sentinelops.io/config-sha256']
    configs = [doc for doc in before if doc['kind'] == 'ConfigMap']
    assert checksum(before) == hashlib.sha256(json.dumps(configs, sort_keys=True).encode()).hexdigest()
    assert checksum(before) == checksum(grafana.render(**inputs()))
    assert checksum(before) != checksum(changed)
    for source in grafana.GRAFANA.rglob('*'):
        if source.is_file():
            dest = tmp_path / source.relative_to(grafana.GRAFANA)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
    path = tmp_path / 'dashboards' / name
    dashboard = json.loads(path.read_text(encoding='utf-8'))
    dashboard['title'] += ' updated'
    path.write_text(json.dumps(dashboard), encoding='utf-8')
    monkeypatch.setattr(grafana, 'GRAFANA', tmp_path)
    assert checksum(before) != checksum(grafana.render(**inputs()))


@pytest.mark.parametrize('node_port', [30000, 32030, 32767])
def test_nodeport_routes_from_both_private_nodes(node_port):
    docs = grafana.render(**inputs(), service_type='NodePort', node_port=node_port)
    service = resource(docs, 'Service')
    assert service['spec']['type'] == 'NodePort'
    assert service['spec']['externalTrafficPolicy'] == 'Cluster'
    assert service['spec']['ports'] == [
        {'name': 'http', 'port': 3000, 'targetPort': 'http', 'protocol': 'TCP', 'nodePort': node_port}]
    deployment = resource(docs, 'Deployment')
    assert service['spec']['selector'].items() <= deployment['spec']['template']['metadata']['labels'].items()


def test_clusterip_and_storage_choices():
    docs = grafana.render(**inputs())
    service = resource(docs, 'Service')
    assert service['spec']['type'] == 'ClusterIP'
    assert 'nodePort' not in service['spec']['ports'][0]
    pvc = resource(docs, 'PersistentVolumeClaim')
    assert pvc['spec'] == {'storageClassName': 'sentinelops-gp3-kms', 'accessModes': ['ReadWriteOnce'],
                           'resources': {'requests': {'storage': '1Gi'}}}
    ephemeral = grafana.render(**inputs(), storage='empty-dir')
    assert not any(doc['kind'] == 'PersistentVolumeClaim' for doc in ephemeral)
    pod = resource(ephemeral, 'Deployment')['spec']['template']['spec']
    assert next(volume for volume in pod['volumes'] if volume['name'] == 'data') == {
        'name': 'data', 'emptyDir': {'sizeLimit': '1Gi'}}


@pytest.mark.parametrize('change', [
    {'cloudfront_domain': 'https://demo.cloudfront.net'}, {'cloudfront_domain': 'demo.cloudfront.net/grafana/'},
    {'cloudfront_domain': 'demo.cloudfront.net:3000'}, {'cloudfront_domain': 'a..net'},
    {'cloudfront_domain': 'a' * 64 + '.net'}, {'cloudfront_domain': 'a.net\n[auth.anonymous]'},
    {'cognito_issuer': 'https://operators.auth.ap-southeast-1.amazoncognito.com'},
    {'cognito_issuer': 'https://cognito-idp.ap-southeast-1.amazonaws.com/us-east-1_Example'},
    {'cognito_issuer': 'http://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_Example'},
    {'cognito_issuer': "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_Evil'"},
    {'authorize_url': 'http://operators.example.com/oauth2/authorize'},
    {'authorize_url': 'https://user:password@operators.example.com/oauth2/authorize'},
    {'authorize_url': 'https://operators.example.com/oauth2/authorize?redirect_uri=evil'},
    {'token_url': 'https://other.example.com/oauth2/token'},
    {'token_url': 'https://operators.auth.ap-southeast-1.amazoncognito.com:invalid/oauth2/token'},
    {'token_url': 'https://operators.auth.ap-southeast-1.amazoncognito.com:8443/oauth2/token'},
    {'userinfo_url': 'https://operators.auth.ap-southeast-1.amazoncognito.com/oauth2/userinfo'},
    {'userinfo_url': 'https://operators.auth.ap-southeast-1.amazoncognito.com/oauth2/userInfo#fragment'},
    {'authorize_url': 'https://operators.example.com\\@evil.example.com/oauth2/authorize'},
    {'authorize_url': 'https://operators.example.com/$__env{HOME}/oauth2/authorize'},
    {'client_id': 'client\n[auth.anonymous]\nenabled = true'}, {'client_id': ''},
    {'oauth_secret': '../secret'}, {'admin_secret': ''}, {'oauth_secret_key': 'bad/key'},
    {'admin_password_key': ''}, {'encryption_key_key': 'admin-password'},
    {'service_type': 'LoadBalancer'}, {'service_type': 'NodePort'},
    {'service_type': 'NodePort', 'node_port': 29999}, {'service_type': 'NodePort', 'node_port': 32768},
    {'service_type': 'NodePort', 'node_port': '32030'}, {'service_type': 'NodePort', 'node_port': True},
    {'node_port': 32030}, {'storage': 'hostPath'}, {'emergency_admin_login': 'false'},
])
def test_invalid_inputs_fail_closed(change):
    with pytest.raises(ValueError):
        grafana.render(**{**inputs(), **change})


def test_cli_runs_directly_and_never_overwrites_output(tmp_path):
    command = [sys.executable, str(Path(grafana.__file__))]
    for key, value in inputs().items():
        command.extend(['--' + key.replace('_', '-'), value])
    command.extend(['--service-type', 'NodePort', '--node-port', '32030'])
    rendered = subprocess.run(command, capture_output=True, text=True, check=True)
    assert rendered.stderr == ''
    docs = list(yaml.safe_load_all(rendered.stdout))
    assert resource(docs, 'Service')['spec']['ports'][0]['nodePort'] == 32030
    output = tmp_path / 'rendered/grafana.yaml'
    subprocess.run([*command, '--output', str(output)], capture_output=True, text=True, check=True)
    assert list(yaml.safe_load_all(output.read_text(encoding='utf-8'))) == docs
    previous = output.read_bytes()
    failed = subprocess.run([*command, '--output', str(output)], capture_output=True, text=True)
    assert failed.returncode != 0
    assert output.read_bytes() == previous
