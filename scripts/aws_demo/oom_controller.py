"""Render the bounded OOM sidecar and its RBAC from a captured controller.

Offline only: no cluster calls, Secret reads, apply or approval. The guarded JSON
patch preserves the deployed controller and authenticated operator UI verbatim.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

import yaml

from sentinelops.remediation.domain import image_digest
from sentinelops.remediation.pod_memory import MemoryTarget, NAME, NAMESPACE

SERVER = 'https://kubernetes.default.svc'
CONTROLLER = 'remediation-controller'
SIDECAR = 'oom-demo-controller'
POLICY = 'sentinelops-oom-demo-policy'


def render_rbac(*, node_names=()):
    """Supplement existing SA reads with one exact patch target and pinned nodes."""
    if any(not isinstance(n, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', n)
           for n in node_names) or len(set(node_names)) != len(node_names):
        raise ValueError('explicit unique Kubernetes node names are required')
    metadata = {'name': POLICY, 'namespace': NAMESPACE}
    subject = {'kind': 'ServiceAccount', 'name': 'payment-rollback', 'namespace': NAMESPACE}
    docs = [
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': metadata, 'rules': [
            {'apiGroups': ['apps'], 'resources': ['deployments'], 'resourceNames': [NAME], 'verbs': ['get', 'patch']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': metadata,
         'subjects': [subject], 'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': POLICY}},
    ]
    if node_names:
        docs += [
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
             'metadata': {'name': POLICY}, 'rules': [
                 {'apiGroups': [''], 'resources': ['nodes'], 'resourceNames': list(node_names), 'verbs': ['get']}]},
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
             'metadata': {'name': POLICY}, 'subjects': [subject],
             'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': POLICY}},
        ]
    return docs


def render(controller, target, image, *, enabled=False, node_names=()):
    """Return (ConfigMap/RBAC documents, race-guarded controller JSON patch)."""
    image_digest(image)
    target = MemoryTarget(**target)
    meta, spec = controller['metadata'], controller['spec']['template']['spec']
    if (meta['name'] != CONTROLLER or meta['namespace'] != NAMESPACE
        or not meta.get('uid') or not meta.get('resourceVersion')
        or spec.get('serviceAccountName') != 'payment-rollback'):
        raise ValueError('capture the existing namespaced remediation-controller with its UID/version and SA')
    containers, volumes = spec['containers'], spec['volumes']
    by_name = {c['name']: c for c in containers}
    if SIDECAR in by_name or POLICY in {v['name'] for v in volumes}:
        raise ValueError('OOM sidecar already present; review current deployment instead of appending twice')
    ui = by_name['operator-ui']
    if CONTROLLER not in by_name:
        raise ValueError('existing remediation controller is required')
    env = {e['name']: e for e in ui['env']}
    database_env = []
    for key in ('CONTROL_DATABASE_URL', 'UI_DATABASE_PASSWORD'):
        item = env[key]
        if set(item) != {'name', 'valueFrom'} or set(item['valueFrom']) != {'secretKeyRef'}:
            raise ValueError('reuse existing database Secret references only; never render credentials')
        database_env.append(deepcopy(item))
    mounts = {m['name']: m for m in by_name[CONTROLLER]['volumeMounts']}
    expected_mounts = {'tmp': '/tmp', 'credentials': '/credentials',
                       'database-ca': '/etc/sentinelops/tls', 'remediation-artifacts': '/artifacts/remediation'}
    for name, path in expected_mounts.items():
        if mounts.get(name, {}).get('mountPath') != path:
            raise ValueError('existing shared credentials, CA and remediation mounts are required')
    if any(not mounts[name].get('readOnly', False) for name in ('credentials', 'database-ca')):
        raise ValueError('credentials and database CA mounts must be read-only')
    if mounts['remediation-artifacts'].get('readOnly', False):
        raise ValueError('shared remediation store must be writable')
    target_json = json.dumps(asdict(target), sort_keys=True)
    data = {'target.json': target_json}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    sidecar = {
        'name': SIDECAR, 'image': image, 'imagePullPolicy': 'IfNotPresent',
        'workingDir': '/opt/sentinelops', 'command': ['python', 'scripts/run_pod_memory_demo.py'],
        'args': ['--target', '/etc/sentinelops/oom/target.json', '--store', '/artifacts/remediation',
                 '--token-file', '/credentials/token', '--ca-file', '/credentials/ca.crt',
                 '--kill-switch', '/artifacts/remediation/KILL_SWITCH', '--server', SERVER]
                + (['--policy-enabled'] if enabled else []),
        'env': database_env + [
            {'name': 'SENTINELOPS_ENV', 'value': 'aws-dev'},
            {'name': 'SENTINELOPS_KUBERNETES_REMOTE_SERVER', 'value': SERVER},
            {'name': 'SSL_CERT_FILE', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
            {'name': 'PGSSLROOTCERT', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
            {'name': 'PYTHONUNBUFFERED', 'value': '1'},
            {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}],
        'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                            'capabilities': {'drop': ['ALL']}},
        'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'},
                      'limits': {'cpu': '250m', 'memory': '256Mi'}},
        'volumeMounts': [deepcopy(mounts[n]) for n in expected_mounts]
                        + [{'name': POLICY, 'mountPath': '/etc/sentinelops/oom', 'readOnly': True}],
    }
    annotations = deepcopy(controller['spec']['template'].get('metadata', {}).get('annotations', {}))
    annotations['sentinelops.io/oom-policy-sha256'] = digest
    patch = [
        {'op': 'test', 'path': '/metadata/uid', 'value': meta['uid']},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
        {'op': 'test', 'path': '/spec/template/spec/containers', 'value': deepcopy(containers)},
        {'op': 'test', 'path': '/spec/template/spec/volumes', 'value': deepcopy(volumes)},
        {'op': 'add', 'path': '/spec/template/spec/containers/-', 'value': sidecar},
        {'op': 'add', 'path': '/spec/template/spec/volumes/-',
         'value': {'name': POLICY, 'configMap': {'name': POLICY, 'optional': False}}},
        {'op': 'add', 'path': '/spec/template/metadata/annotations', 'value': annotations},
    ]
    docs = [{'apiVersion': 'v1', 'kind': 'ConfigMap',
             'metadata': {'name': POLICY, 'namespace': NAMESPACE}, 'data': data}]
    docs += render_rbac(node_names=node_names)
    return docs, patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller', type=Path, required=True, help='Fresh kubectl get deployment -o json capture')
    parser.add_argument('--target', type=Path, required=True, help='Pinned MemoryTarget JSON from actual workload UIDs')
    parser.add_argument('--image', required=True, help='Built controller image, pinned by digest')
    parser.add_argument('--policy-enabled', action='store_true')
    parser.add_argument('--node-name', action='append', default=[], help='Allow get only these observed node names')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    docs, patch = render(json.loads(args.controller.read_text()), json.loads(args.target.read_text()),
                         args.image, enabled=args.policy_enabled, node_names=args.node_name)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / 'oom-policy.yaml').open('x') as stream:
        yaml.safe_dump_all(docs, stream, sort_keys=False)
    with (args.output_dir / 'oom-controller-patch.json').open('x') as stream:
        json.dump(patch, stream, indent=2)
        stream.write('\n')
    print('Rendered OOM policy/RBAC and guarded controller patch; no apply or approval performed')


if __name__ == '__main__':
    main()
