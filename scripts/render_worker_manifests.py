"""Render offline EKS health/incident workers; no deployment or action authority.

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
           model_claim: str | None = None, model_release_ref: str | None = None) -> list[dict]:
    image_digest(image)
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
    claims = [health_store_claim, incident_store_claim] + ([model_claim] if model_claim else [])
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
            ]
            init_mounts.append({'name': 'observer-credentials', 'mountPath': '/secret-observer', 'readOnly': True})
            mounts += [{'name': 'kubernetes-config', 'mountPath': '/etc/sentinelops/kubernetes', 'readOnly': True},
                       {'name': 'incident-artifacts', 'mountPath': INCIDENT_ROOT}]
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
                    # The health artifact claim is EBS ReadWriteOnce: it attaches to one node. The incident
                    # worker reads it, so it must run on the node where the health worker holds the volume.
                    **({'affinity': {'podAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': [{
                        'labelSelector': {'matchLabels': {'app': 'sentinelops-health-worker'}},
                        'topologyKey': 'kubernetes.io/hostname'}]}}} if incident else {}),
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
                            {'name': 'SSL_CERT_FILE', 'value': '/etc/sentinelops/tls/ca-bundle.crt'}],
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
    return documents


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
