"""Render offline EKS workers and the opt-in Payment remediation controller.

Operator prerequisites: control-runtime (credential-free database-url and password),
an observer token/CA Secret, a public RDS CA bundle ConfigMap (ca-bundle.crt),
durable artifact PVCs, and the selected accepted health stream/Deployment UIDs.
Model PVC/release selection is optional read-only wiring, never model approval.
Secret contents are neither read nor hashed. Rotate their bytes with an explicit
restart (or a new observer/Prometheus Secret reference).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

from scripts.render_api_manifests import MODEL_REF
from scripts.render_business_manifests import _label
from sentinelops.api_settings import LOCAL_ENVIRONMENTS
from sentinelops.remediation.domain import Target, image_digest
from sentinelops.telemetry.core import BUSINESS_ROUTES
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.workers.kubernetes_evidence import remote_jaeger
from sentinelops.workers.source_policy import SourcePolicy, _remote_url

NAMESPACE = 'sentinelops-b-actions'
PROMETHEUS = f'http://prometheus.{NAMESPACE}.svc.cluster.local:9090'
JAEGER = f'http://jaeger.{NAMESPACE}.svc.cluster.local:16686'
CONFIG = 'sentinelops-workers-runtime'
KUBE_CONFIG = 'sentinelops-worker-kubernetes'
OBSERVER = 'sentinelops-incident-observer'
HEALTH_ROOT = '/artifacts/health'
INCIDENT_ROOT = '/artifacts/incidents'
CREDENTIAL_ROOT = '/credentials'
COPY_CREDENTIALS = (
    'import os, shutil, sys; from pathlib import Path; os.umask(0o077); '
    '[shutil.copyfile(p, Path(sys.argv[1]) / Path(p).name) for p in sys.argv[2:]]'
)
# Exec probes do not expand Kubernetes $(VAR) arguments. Replace the process
# with the CLI after resolving only whole environment references; never use a shell.
STATUS_EXEC = (
    'import os, sys; os.execv(sys.executable, [sys.executable, '
    '*[os.environ[a[2:-1]] if a.startswith("$(") and a.endswith(")") else a '
    'for a in sys.argv[1:]]])'
)


def _server(value):
    if not isinstance(value, str):
        raise ValueError('an explicit credential-free HTTPS Kubernetes API server is required')
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or not parts.port
        or not re.fullmatch(r'[a-zA-Z0-9.-]+', parts.hostname)
        or parts.username is not None or parts.password is not None or '?' in value or '#' in value
        or parts.path not in ('', '/') or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError('an explicit credential-free HTTPS Kubernetes API server with port is required')
    return value.rstrip('/')


def render(image: str, *, environment: str, control_database: str, database_ca_config: str,
           health_store_claim: str, incident_store_claim: str, health_stream: str,
           kubernetes_server: str, cluster_uid: str, namespace_uid: str,
           deployment_uids: dict, observer_secret: str,
           prometheus: str = PROMETHEUS, jaeger: str = JAEGER,
           prometheus_secret: str | None = None, prometheus_token_key: str | None = None,
           model_claim: str | None = None, model_release_ref: str | None = None,
           remediation_store_claim: str = 'remediation-artifacts',
           known_good_store_claim: str = 'review-artifacts',
           deployment_change_store_claim: str = 'deployment-change-artifacts',
           policy_enabled: bool = False, demo_ui: bool = False) -> list[dict]:
    image_digest(image)
    _label(remediation_store_claim, 'remediation artifact claim')
    _label(known_good_store_claim, 'known-good artifact claim')
    _label(deployment_change_store_claim, 'deployment-change artifact claim')
    if type(policy_enabled) is not bool:
        raise ValueError('policy_enabled must be an explicit boolean')
    if type(demo_ui) is not bool:
        raise ValueError('demo_ui must be an explicit boolean')
    _label(environment, 'environment')
    if environment in LOCAL_ENVIRONMENTS:
        raise ValueError('cloud workers require an explicit non-local environment')
    for value, name in ((database_ca_config, 'database CA ConfigMap'),
                        (health_store_claim, 'health artifact claim'),
                        (incident_store_claim, 'incident artifact claim'),
                        (observer_secret, 'observer Secret')):
        _label(value, name)
    if observer_secret == 'control-runtime':
        raise ValueError('observer credentials require a separate Secret from control-runtime')
    if not isinstance(control_database, str):
        raise ValueError('an explicit pinned credential-free control database TLS URL is required')
    _remote_url(control_database, control_database, database=True)
    if not isinstance(health_stream, str) or not re.fullmatch(r'[0-9a-f]{64}', health_stream):
        raise ValueError('an explicit accepted health stream SHA-256 identity is required')
    if not isinstance(deployment_uids, dict) or set(deployment_uids) != set(BUSINESS_ROUTES):
        raise ValueError('pin all three business Deployment UIDs')
    if any(not isinstance(v, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', v)
           for v in (cluster_uid, namespace_uid, *deployment_uids.values())):
        raise ValueError('cluster, namespace and Deployment UID references must be explicit identifiers')
    target = Target(cluster_uid, NAMESPACE, namespace_uid, 'payment-service',
                    deployment_uids['payment-service'], 'payment')
    server = _server(kubernetes_server)
    # The demo deliberately selects its exact Service DNS source. Public remote
    # TLS policy remains separate and is not weakened by this renderer.
    if prometheus != PROMETHEUS or jaeger != JAEGER:
        raise ValueError('demo workers require the pinned in-cluster Prometheus and Jaeger Service URLs')
    SourcePolicy(name='in-cluster', remote_prometheus=PROMETHEUS).prometheus(prometheus)
    remote_jaeger(jaeger, JAEGER)
    if prometheus_secret is not None or prometheus_token_key is not None:
        _label(prometheus_secret, 'Prometheus Secret/key pair')
        if (not isinstance(prometheus_token_key, str)
            or not re.fullmatch(r'[a-zA-Z0-9._-]{1,253}', prometheus_token_key)):
            raise ValueError('Prometheus Secret/key pair requires a valid key reference')
        if prometheus_secret in ('control-runtime', observer_secret):
            raise ValueError('Prometheus authentication requires its own Secret')
    if model_claim is not None or model_release_ref is not None:
        _label(model_claim, 'model claim/release pair')
        if not isinstance(model_release_ref, str) or not MODEL_REF.fullmatch(model_release_ref):
            raise ValueError('model claim/release pair requires a checksummed artifact manifest reference')
    claims = [health_store_claim, incident_store_claim, remediation_store_claim,
              known_good_store_claim, deployment_change_store_claim] + ([model_claim] if model_claim else [])
    if len(set(claims)) != len(claims):
        raise ValueError('health, incident and read-only model stores require separate PVCs')

    labels = {'app.kubernetes.io/part-of': 'sentinelops'}
    def metadata(name):
        return {'name': name, 'namespace': NAMESPACE, 'labels': dict(labels)}
    data = {'SENTINELOPS_ENV': environment, 'SENTINELOPS_ENABLE_DEMO': 'false',
        'SENTINELOPS_SOURCE_PROFILE': SOURCE_PROFILE_V2, 'SENTINELOPS_PROMETHEUS': prometheus,
        'SENTINELOPS_JAEGER': jaeger, 'SENTINELOPS_CONTROL_DATABASE': control_database,
        'SENTINELOPS_KUBERNETES_REMOTE_SERVER': server,
        'SENTINELOPS_HEALTH_STREAM': health_stream}
    if model_claim:
        data.update(SENTINELOPS_MODEL_STORE='/models', SENTINELOPS_MODEL_RELEASE_REF=model_release_ref)
    kube_data = {'kubernetes.json': json.dumps({'server': server, 'target': asdict(target),
        'deployment_uids': deployment_uids, 'token_file': CREDENTIAL_ROOT + '/observer-token',
        'ca_file': CREDENTIAL_ROOT + '/observer-ca.crt'}, sort_keys=True, separators=(',', ':'))}
    config_sha256 = hashlib.sha256(json.dumps({'runtime': data, 'kubernetes': kube_data},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    hardening = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                 'capabilities': {'drop': ['ALL']}}
    common_args = ['--source-policy', 'in-cluster', '--environment', '$(SENTINELOPS_ENV)',
        '--prometheus', '$(SENTINELOPS_PROMETHEUS)', '--remote-prometheus', '$(SENTINELOPS_PROMETHEUS)',
        '--remote-control-database', '$(SENTINELOPS_CONTROL_DATABASE)',
        '--control-database-user', 'control_writer',
        '--control-database-password-file', CREDENTIAL_ROOT + '/database-password']
    if prometheus_secret:
        common_args += ['--prometheus-token-file', CREDENTIAL_ROOT + '/prometheus-token']
    documents = [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE,
            'labels': {**labels, 'sentinelops.dev/lane': 'B'}}},
        *[{'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata(name),
           'automountServiceAccountToken': False} for name in ('health-worker', 'incident-worker')],
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(CONFIG), 'data': data},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(KUBE_CONFIG), 'data': kube_data},
    ]
    for worker in ('health', 'incident'):
        incident = worker == 'incident'
        name = 'sentinelops-' + worker + '-worker'
        sources = ['/secret-database/database-password']
        volumes = [
            {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}},
            {'name': 'credentials', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Ki'}},
            {'name': 'database-password', 'secret': {'secretName': 'control-runtime', 'optional': False,
                'defaultMode': 0o440, 'items': [{'key': 'password', 'path': 'database-password'}]}},
            {'name': 'database-ca', 'configMap': {'name': database_ca_config, 'optional': False,
                'items': [{'key': 'ca-bundle.crt', 'path': 'ca-bundle.crt'}]}},
            {'name': 'health-artifacts', 'persistentVolumeClaim': {
                'claimName': health_store_claim, 'readOnly': incident}},
        ]
        mounts = [{'name': 'tmp', 'mountPath': '/tmp'},
            {'name': 'credentials', 'mountPath': CREDENTIAL_ROOT, 'readOnly': True},
            {'name': 'database-ca', 'mountPath': '/etc/sentinelops/tls', 'readOnly': True},
            {'name': 'health-artifacts', 'mountPath': HEALTH_ROOT, 'readOnly': incident}]
        init_mounts = [{'name': 'credentials', 'mountPath': CREDENTIAL_ROOT},
            {'name': 'database-password', 'mountPath': '/secret-database', 'readOnly': True}]
        args = common_args + (['--store', HEALTH_ROOT, '--source-profile', SOURCE_PROFILE_V2] if not incident else [
            '--health-stream', '$(SENTINELOPS_HEALTH_STREAM)', '--source-store', HEALTH_ROOT,
            '--store', INCIDENT_ROOT, '--project', 'kubernetes-read-only',
            '--kubernetes-config', '/etc/sentinelops/kubernetes/kubernetes.json',
            '--deployment-change-store', '/artifacts/deployment-changes',
            '--jaeger', '$(SENTINELOPS_JAEGER)', '--remote-jaeger', '$(SENTINELOPS_JAEGER)'])
        if prometheus_secret:
            sources.append('/secret-prometheus/prometheus-token')
            volumes.append({'name': 'prometheus-token', 'secret': {'secretName': prometheus_secret,
                'optional': False, 'defaultMode': 0o440,
                'items': [{'key': prometheus_token_key, 'path': 'prometheus-token'}]}})
            init_mounts.append({'name': 'prometheus-token', 'mountPath': '/secret-prometheus', 'readOnly': True})
        if incident:
            sources += ['/secret-observer/observer-token', '/secret-observer/observer-ca.crt']
            volumes += [
                {'name': 'observer-credentials', 'secret': {'secretName': observer_secret,
                    'optional': False, 'defaultMode': 0o440, 'items': [
                        {'key': 'token', 'path': 'observer-token'}, {'key': 'ca.crt', 'path': 'observer-ca.crt'}]}},
                {'name': 'kubernetes-config', 'configMap': {'name': KUBE_CONFIG, 'optional': False}},
                {'name': 'incident-artifacts', 'persistentVolumeClaim': {'claimName': incident_store_claim}},
                {'name': 'deployment-changes', 'persistentVolumeClaim': {
                    'claimName': deployment_change_store_claim, 'readOnly': True}},
            ]
            init_mounts.append({'name': 'observer-credentials', 'mountPath': '/secret-observer', 'readOnly': True})
            mounts += [{'name': 'kubernetes-config', 'mountPath': '/etc/sentinelops/kubernetes', 'readOnly': True},
                       {'name': 'incident-artifacts', 'mountPath': INCIDENT_ROOT},
                       {'name': 'deployment-changes', 'mountPath': '/artifacts/deployment-changes', 'readOnly': True}]
            if model_claim:
                volumes.append({'name': 'models', 'persistentVolumeClaim': {'claimName': model_claim, 'readOnly': True}})
                mounts.append({'name': 'models', 'mountPath': '/models', 'readOnly': True})
                args += ['--model-store', '$(SENTINELOPS_MODEL_STORE)', '--model-release-ref', '$(SENTINELOPS_MODEL_RELEASE_REF)']
        status_command = ['python', '-c', STATUS_EXEC, f'scripts/run_{worker}_worker.py', *args, '--status']
        documents.append({'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata(name), 'spec': {
            'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app': name}},
            'template': {'metadata': {'labels': {**labels, 'app': name}, 'annotations': {
                'sentinelops.io/config-sha256': config_sha256,
                'sentinelops.io/readiness-scope': 'Read-only CLI status: runtime control DB access and durable status; not processing progress'}},
                'spec': {'serviceAccountName': worker + '-worker', 'automountServiceAccountToken': False,
                    'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                        'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
                    'initContainers': [{'name': 'private-credentials', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                        'command': ['python', '-c', COPY_CREDENTIALS], 'args': [CREDENTIAL_ROOT, *sources],
                        'securityContext': dict(hardening), 'volumeMounts': init_mounts,
                        'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                      'limits': {'cpu': '100m', 'memory': '64Mi'}}}],
                    'containers': [{'name': worker + '-worker', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                        'command': ['python', f'scripts/run_{worker}_worker.py'], 'args': args,
                        # A probe opens one DB connection. Keep polling sparse and
                        # allow sustained dependency recovery before restarting.
                        'readinessProbe': {'exec': {'command': list(status_command)},
                            'initialDelaySeconds': 15, 'timeoutSeconds': 10,
                            'periodSeconds': 30, 'failureThreshold': 3},
                        'livenessProbe': {'exec': {'command': list(status_command)},
                            'initialDelaySeconds': 120, 'timeoutSeconds': 10,
                            'periodSeconds': 120, 'failureThreshold': 6},
                        'envFrom': [{'configMapRef': {'name': CONFIG}}], 'env': [
                            {'name': 'CONTROL_DATABASE_URL', 'valueFrom': {'secretKeyRef': {
                                'name': 'control-runtime', 'key': 'database-url', 'optional': False}}},
                            {'name': 'SSL_CERT_FILE', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
                            # asyncpg verify-full reads the root CA from here, not SSL_CERT_FILE.
                            {'name': 'PGSSLROOTCERT', 'value': '/etc/sentinelops/tls/ca-bundle.crt'}],
                        'securityContext': dict(hardening), 'volumeMounts': mounts,
                        'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                                      'limits': {'cpu': '1', 'memory': '1Gi'}}}], 'volumes': volumes}}}})
    # These are observer permissions only. SelfSubjectAccessReview is already
    # supplied to authenticated service accounts by Kubernetes; no write role
    # or remediation policy is introduced here.
    documents += [
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': metadata(OBSERVER), 'rules': [
            {'apiGroups': ['apps'], 'resources': ['deployments'], 'resourceNames': sorted(BUSINESS_ROUTES), 'verbs': ['get']},
            {'apiGroups': ['apps'], 'resources': ['replicasets'], 'verbs': ['get', 'list']},
            {'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get', 'list']},
            {'apiGroups': [''], 'resources': ['pods/log'], 'verbs': ['get']},
            {'apiGroups': ['autoscaling'], 'resources': ['horizontalpodautoscalers'], 'verbs': ['list']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': metadata(OBSERVER),
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': OBSERVER},
            'subjects': [{'kind': 'ServiceAccount', 'name': 'incident-worker', 'namespace': NAMESPACE}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
            'metadata': {'name': OBSERVER, 'labels': dict(labels)}, 'rules': [
                {'apiGroups': [''], 'resources': ['namespaces'],
                 'resourceNames': ['kube-system', NAMESPACE], 'verbs': ['get']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
            'metadata': {'name': OBSERVER, 'labels': dict(labels)},
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': OBSERVER},
            'subjects': [{'kind': 'ServiceAccount', 'name': 'incident-worker', 'namespace': NAMESPACE}]},
    ]
    documents += _controller(image, target, metadata, labels, hardening,
        health_store_claim, incident_store_claim, remediation_store_claim,
        known_good_store_claim, database_ca_config, health_stream, environment, policy_enabled,
        model_claim, model_release_ref, demo_ui)
    return documents


def _controller(image, target, metadata, labels, hardening, health_claim, incident_claim,
                remediation_claim, known_good_claim, database_ca_config, health_stream, environment,
                enabled, model_claim, model_release_ref, demo_ui):
    """Use the same non-root private credential copy as PR #70.

    remediation-runtime is provisioned separately by the operator: action-database-url
    (dedicated action writer), known-good-ledger-ref and approved-by. The catalog is
    part of the control-plane image. A KILL_SWITCH in the durable store disables it.
    Projected tokens must be renewed by replacing the Pod before their 24h expiry;
    LocalKubernetes deliberately snapshots the bearer token on construction.
    """
    name, config = 'remediation-controller', 'sentinelops-remediation-kubernetes'
    server = 'https://kubernetes.default.svc'
    data = {'target.json': json.dumps(asdict(target), sort_keys=True)}
    args = ['--health-stream', health_stream, '--source-store', HEALTH_ROOT,
        '--incident-store', INCIDENT_ROOT, '--store', '/artifacts/remediation',
        '--known-good-store', '/artifacts/review',
        '--known-good-ref', '$(SENTINELOPS_KNOWN_GOOD_LEDGER_REF)',
        '--approved-by', '$(SENTINELOPS_KNOWN_GOOD_APPROVED_BY)',
        '--target', '/etc/sentinelops/remediation/target.json', '--server', server,
        '--token-file', '/credentials/token', '--ca-file', '/credentials/ca.crt',
        '--catalog', 'configs/runbook_catalog.json', '--kill-switch', '/artifacts/remediation/KILL_SWITCH']
    if enabled:
        args.append('--policy-enabled')
    if model_claim:
        args += ['--model-store', '/models', '--model-release-ref', model_release_ref]
    volumes = [
        {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}},
        {'name': 'credentials', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Ki'}},
        {'name': 'service-account', 'projected': {'defaultMode': 0o440, 'sources': [
            {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 86400}},
            {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}},
        {'name': 'target', 'configMap': {'name': config}},
        {'name': 'database-ca', 'configMap': {'name': database_ca_config,
            'items': [{'key': 'ca-bundle.crt', 'path': 'ca-bundle.crt'}]}},
        *[{'name': name, 'persistentVolumeClaim': {'claimName': claim, 'readOnly': readonly}}
          for name, claim, readonly in (('health-artifacts', health_claim, True),
              ('incident-artifacts', incident_claim, True), ('remediation-artifacts', remediation_claim, False))]]
    volumes.append({'name': 'review-artifacts', 'persistentVolumeClaim': {
        'claimName': known_good_claim, 'readOnly': True}})
    mounts = [{'name': 'tmp', 'mountPath': '/tmp'},
        {'name': 'credentials', 'mountPath': '/credentials', 'readOnly': True},
        {'name': 'target', 'mountPath': '/etc/sentinelops/remediation', 'readOnly': True},
        {'name': 'database-ca', 'mountPath': '/etc/sentinelops/tls', 'readOnly': True},
        {'name': 'health-artifacts', 'mountPath': HEALTH_ROOT, 'readOnly': True},
        {'name': 'incident-artifacts', 'mountPath': INCIDENT_ROOT, 'readOnly': True},
        {'name': 'remediation-artifacts', 'mountPath': '/artifacts/remediation'}]
    mounts.append({'name': 'review-artifacts', 'mountPath': '/artifacts/review', 'readOnly': True})
    if model_claim:
        volumes.append({'name': 'models', 'persistentVolumeClaim': {'claimName': model_claim, 'readOnly': True}})
        mounts.append({'name': 'models', 'mountPath': '/models', 'readOnly': True})
    env = [{'name': 'SENTINELOPS_KUBERNETES_REMOTE_SERVER', 'value': server},
        {'name': 'SENTINELOPS_ENV', 'value': environment},
        {'name': 'SSL_CERT_FILE', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
        # asyncpg verify-full reads the root CA from here, not from SSL_CERT_FILE.
        {'name': 'PGSSLROOTCERT', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
        *[{'name': name, 'valueFrom': {'secretKeyRef': {'name': 'remediation-runtime', 'key': key}}}
          for name, key in (('SENTINELOPS_ACTION_DATABASE_URL', 'action-database-url'),
              ('SENTINELOPS_KNOWN_GOOD_LEDGER_REF', 'known-good-ledger-ref'),
              ('SENTINELOPS_KNOWN_GOOD_APPROVED_BY', 'approved-by'))]]
    # Same Pod/PVC as the controller (RWO cannot be attached independently on
    # another node). The API renderer supplies OIDC config and the ALB Service.
    ui = []
    if demo_ui:
        ui_bootstrap = (
            'import os\nfrom urllib.parse import urlsplit, urlunsplit, quote\n'
            'from sentinelops.api import create_app\nimport uvicorn\n'
            'from sentinelops.api_settings import Settings, LOCAL_ENVIRONMENTS\n'
            'p=urlsplit(os.environ["CONTROL_DATABASE_URL"])\n'
            'password=os.environ.pop("UI_DATABASE_PASSWORD")\n'
            'os.environ["CONTROL_DATABASE_URL"]=urlunsplit(p._replace(netloc='
            '"control_writer:"+quote(password,safe="")+"@"+p.netloc.rsplit("@",1)[-1]))\n'
            'settings=Settings.from_env()\n'
            'if settings.environment in LOCAL_ENVIRONMENTS or settings.oidc is None or not settings.oidc.login_domain: '
            'raise ValueError("exposed UI requires non-local OIDC hosted login")\n'
            'uvicorn.run(create_app(settings),host="0.0.0.0",port=8000,access_log=False)'
        )
        ui_env = [{'name': 'SENTINELOPS_ENV', 'value': environment},
            {'name': 'SENTINELOPS_ENABLE_DEMO', 'value': 'false'},
            {'name': 'SENTINELOPS_RUNBOOK_PROPOSAL_STORE', 'value': '/artifacts/remediation'},
            {'name': 'SSL_CERT_FILE', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
            {'name': 'PGSSLROOTCERT', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
            *[{'name': variable, 'valueFrom': {'secretKeyRef': {'name': secret, 'key': key,
                                                            'optional': False}}}
              for variable, secret, key in (
                ('CONTROL_DATABASE_URL', 'control-runtime', 'database-url'),
                ('UI_DATABASE_PASSWORD', 'control-runtime', 'password'))]]
        probe = {'exec': {'command': ['python', '-c',
            'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=2).read()']},
            'periodSeconds': 10, 'timeoutSeconds': 3}
        ui = [{'name': 'operator-ui', 'image': image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python', '-c', ui_bootstrap], 'env': ui_env,
            'envFrom': [{'configMapRef': {'name': 'sentinelops-api-runtime', 'optional': False}}],
            'ports': [{'name': 'http', 'containerPort': 8000}],
            'securityContext': dict(hardening),
            'volumeMounts': [{'name': 'tmp', 'mountPath': '/tmp'},
                {'name': 'database-ca', 'mountPath': '/etc/sentinelops/tls', 'readOnly': True},
                {'name': 'remediation-artifacts', 'mountPath': '/artifacts/remediation'}],
            'startupProbe': {**probe, 'failureThreshold': 30},
            'readinessProbe': probe, 'livenessProbe': probe,
            'resources': {'requests': {'cpu': '50m', 'memory': '128Mi'},
                          'limits': {'cpu': '500m', 'memory': '512Mi'}}}]
    return [
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata('payment-rollback'),
            'automountServiceAccountToken': False},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(config), 'data': data},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata(name), 'spec': {
            'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app': name}},
            'template': {'metadata': {'labels': {**labels, 'app': name}, 'annotations': {
                'sentinelops.io/config-sha256': hashlib.sha256(json.dumps(
                    [data, args, env, ui], sort_keys=True).encode()).hexdigest()}},
                'spec': {'serviceAccountName': 'payment-rollback', 'automountServiceAccountToken': False,
                    'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                        'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
                    'initContainers': [{'name': 'private-credentials', 'image': image,
                        'command': ['python', '-c', COPY_CREDENTIALS],
                        'args': ['/credentials', '/service-account/token', '/service-account/ca.crt'],
                        'securityContext': dict(hardening), 'volumeMounts': [
                            {'name': 'credentials', 'mountPath': '/credentials'},
                            {'name': 'service-account', 'mountPath': '/service-account', 'readOnly': True}],
                        'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                            'limits': {'cpu': '100m', 'memory': '64Mi'}}}],
                    'containers': [{'name': name, 'image': image,
                        'command': ['python', 'scripts/run_remediation_controller.py'], 'args': args,
                        'env': env, 'securityContext': dict(hardening), 'volumeMounts': mounts,
                        'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                            'limits': {'cpu': '1', 'memory': '1Gi'}}}, *ui], 'volumes': volumes}}}},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role',
            'metadata': metadata('payment-rollback'), 'rules': [
                {'apiGroups': ['apps'], 'resources': ['deployments'],
                    'resourceNames': ['payment-service'], 'verbs': ['get', 'patch']},
                {'apiGroups': ['apps'], 'resources': ['replicasets'], 'verbs': ['get', 'list']},
                {'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get', 'list']},
                {'apiGroups': ['autoscaling'], 'resources': ['horizontalpodautoscalers'], 'verbs': ['get', 'list']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding',
            'metadata': metadata('payment-rollback'),
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'payment-rollback'},
            'subjects': [{'kind': 'ServiceAccount', 'name': 'payment-rollback', 'namespace': NAMESPACE}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
            'metadata': {'name': 'sentinelops-b-action-identity', 'labels': dict(labels)}, 'rules': [
                {'apiGroups': [''], 'resources': ['namespaces'],
                    'resourceNames': ['kube-system', NAMESPACE], 'verbs': ['get']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
            'metadata': {'name': 'sentinelops-b-action-identity', 'labels': dict(labels)},
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole',
                'name': 'sentinelops-b-action-identity'},
            'subjects': [{'kind': 'ServiceAccount', 'name': 'payment-rollback', 'namespace': NAMESPACE}]}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    for name in ('environment', 'control-database', 'database-ca-config', 'health-store-claim',
                 'incident-store-claim', 'health-stream', 'kubernetes-server', 'cluster-uid',
                 'namespace-uid', 'observer-secret'):
        parser.add_argument('--' + name, required=True)
    for service in ('checkout', 'payment', 'inventory'):
        parser.add_argument('--' + service + '-deployment-uid', required=True)
    parser.add_argument('--prometheus', default=PROMETHEUS)
    parser.add_argument('--jaeger', default=JAEGER)
    parser.add_argument('--prometheus-secret')
    parser.add_argument('--prometheus-token-key')
    parser.add_argument('--model-claim')
    parser.add_argument('--model-release-ref')
    parser.add_argument('--remediation-store-claim', default='remediation-artifacts')
    parser.add_argument('--known-good-store-claim', default='review-artifacts')
    parser.add_argument('--deployment-change-store-claim', default='deployment-change-artifacts')
    parser.add_argument('--policy-enabled', action='store_true')
    parser.add_argument('--demo-ui', action='store_true', help='OIDC UI sidecar sharing controller PVC; API renderer supplies config/Service')
    parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop('output')
    args['deployment_uids'] = {service + '-service': args.pop(service + '_deployment_uid')
                              for service in ('checkout', 'payment', 'inventory')}
    documents = render(**args)
    with output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__':
    main()
