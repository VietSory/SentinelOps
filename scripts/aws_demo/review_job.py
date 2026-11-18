"""Render the one-shot EKS Payment image evidence Job; never apply or approve.

Uses existing worker DSN ConfigMap, control-runtime password, payment-rollback SA
and PVCs. Supply the current health worker's kubernetes.io/hostname label value
to keep both RWO volumes on the same node. No AWS/kubectl calls or secret reads.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

import yaml

from scripts.aws_demo.render_all import ROOT, read_json
from scripts.render_worker_manifests import CONFIG, COPY_CREDENTIALS, HEALTH_ROOT, NAMESPACE
from scripts.render_business_manifests import _label
from sentinelops.remediation.domain import Target, image_digest

HEALTH_STREAM = '6028fde726c16b94bd0d5df07e4ae5cc2baa69cc5aec763a08359750f01aecaf'
SERVER = 'https://kubernetes.default.svc'
REVIEW_ROOT = '/artifacts/review'


def render(images, runtime, *, health_node, operation='collect', review_ref=None):
    image = image_digest(images['control-plane'])
    if (not isinstance(health_node, str) or len(health_node) > 253
        or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', health_node)):
        raise ValueError('explicit health worker node hostname is required for RWO storage')
    if operation not in ('open', 'complete', 'collect'):
        raise ValueError('only open, complete or collect image evidence')
    if (operation == 'complete') != (review_ref is not None):
        raise ValueError('complete requires a review-ref; other operations must not supply one')
    if review_ref is not None and not re.fullmatch(r'artifact://manifests/[0-9a-f]{64}\.json', review_ref):
        raise ValueError('review-ref must be a checksummed manifest reference')
    stream = runtime.get('health_stream', HEALTH_STREAM)
    if not isinstance(stream, str) or not re.fullmatch(r'[0-9a-f]{64}', stream):
        raise ValueError('selected accepted health stream SHA-256 is required')
    uids = (runtime['cluster_uid'], runtime['namespace_uid'], runtime['deployment_uids']['payment-service'])
    if any(not isinstance(uid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', uid) for uid in uids):
        raise ValueError('pin the cluster, namespace and Payment Deployment UIDs')
    target = Target(uids[0], NAMESPACE, uids[1], 'payment-service', uids[2], 'payment')
    ca = runtime.get('database_ca_config', 'control-rds-ca')
    health = runtime.get('health_store_claim', 'health-artifacts')
    review = runtime.get('known_good_store_claim', 'review-artifacts')
    for value in (ca, health, review):
        _label(value, 'CA ConfigMap or artifact PVC')
    if health == review:
        raise ValueError('health and review stores require separate PVCs')
    labels = {'app': 'review', 'app.kubernetes.io/part-of': 'sentinelops'}
    metadata = {'name': 'review', 'namespace': NAMESPACE, 'labels': labels}
    data = {'review_payment_image.py': (ROOT / 'scripts/review_payment_image.py').read_text(encoding='utf-8'),
            'target.json': json.dumps(asdict(target), sort_keys=True)}
    hardening = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                 'capabilities': {'drop': ['ALL']}}
    args = ['--in-cluster-worker', '--health-stream', stream, '--source-store', HEALTH_ROOT,
            '--review-store', REVIEW_ROOT, '--target', '/review/target.json', '--server', SERVER,
            '--token-file', '/credentials/token', '--ca-file', '/credentials/ca.crt', operation]
    if review_ref:
        args += ['--review-ref', review_ref]
    documents = [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata, 'data': data},
        {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': metadata, 'spec': {
            'backoffLimit': 0, 'completions': 1, 'parallelism': 1, 'activeDeadlineSeconds': 660,
            'template': {'metadata': {'labels': labels, 'annotations': {
                'sentinelops.io/config-sha256': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()}},
                'spec': {'serviceAccountName': 'payment-rollback', 'automountServiceAccountToken': False,
                    'restartPolicy': 'Never',
                    'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                        'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
                    'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
                        'nodeSelectorTerms': [{'matchExpressions': [{'key': 'kubernetes.io/hostname',
                            'operator': 'In', 'values': [health_node]}]}]}}},
                    'initContainers': [{'name': 'private-credentials', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                        'command': ['python', '-c', COPY_CREDENTIALS],
                        'args': ['/credentials', '/service-account/token', '/service-account/ca.crt',
                                 '/secret-database/database-password'],
                        'securityContext': dict(hardening),
                        'volumeMounts': [{'name': 'credentials', 'mountPath': '/credentials'},
                            {'name': 'service-account', 'mountPath': '/service-account', 'readOnly': True},
                            {'name': 'database-password', 'mountPath': '/secret-database', 'readOnly': True}],
                        'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                      'limits': {'cpu': '100m', 'memory': '64Mi'}}}],
                    'containers': [{'name': 'review', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                        'workingDir': '/opt/sentinelops',
                        'command': ['python', '-u', '/review/review_payment_image.py'], 'args': args,
                        'env': [{'name': 'SENTINELOPS_ENV', 'value': 'dev'},
                            {'name': 'SENTINELOPS_KUBERNETES_REMOTE_SERVER', 'value': SERVER},
                            {'name': 'SENTINELOPS_IMAGE_REVIEW_DATABASE_URL', 'valueFrom': {
                                'configMapKeyRef': {'name': CONFIG, 'key': 'SENTINELOPS_CONTROL_DATABASE', 'optional': False}}},
                            {'name': 'SSL_CERT_FILE', 'value': '/etc/sentinelops/tls/ca-bundle.crt'},
                            {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}],
                        'securityContext': dict(hardening),
                        'volumeMounts': [{'name': 'tmp', 'mountPath': '/tmp'},
                            {'name': 'script', 'mountPath': '/review', 'readOnly': True},
                            {'name': 'credentials', 'mountPath': '/credentials', 'readOnly': True},
                            {'name': 'database-ca', 'mountPath': '/etc/sentinelops/tls', 'readOnly': True},
                            {'name': 'health-artifacts', 'mountPath': HEALTH_ROOT, 'readOnly': True},
                            {'name': 'review-artifacts', 'mountPath': REVIEW_ROOT}],
                        'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                                      'limits': {'cpu': '1', 'memory': '1Gi'}}}],
                    'volumes': [{'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}},
                        {'name': 'credentials', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Ki'}},
                        {'name': 'script', 'configMap': {'name': 'review', 'optional': False}},
                        {'name': 'service-account', 'projected': {'defaultMode': 0o440, 'sources': [
                            {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 86400}},
                            {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}},
                        {'name': 'database-password', 'secret': {'secretName': 'control-runtime', 'optional': False,
                            'defaultMode': 0o440, 'items': [{'key': 'password', 'path': 'database-password'}]}},
                        {'name': 'database-ca', 'configMap': {'name': ca, 'optional': False,
                            'items': [{'key': 'ca-bundle.crt', 'path': 'ca-bundle.crt'}]}},
                        {'name': 'health-artifacts', 'persistentVolumeClaim': {'claimName': health, 'readOnly': True}},
                        {'name': 'review-artifacts', 'persistentVolumeClaim': {'claimName': review, 'readOnly': False}}]}}}},
    ]
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path)
    parser.add_argument('runtime', type=Path, help='render_all runtime JSON, or UID-only JSON with demo defaults')
    parser.add_argument('--uids', type=Path, help='Optional separately captured UIDs; must agree with runtime if present')
    parser.add_argument('--health-node', required=True, help="Current health worker node's kubernetes.io/hostname value")
    parser.add_argument('--operation', choices=('collect', 'open', 'complete'), default='collect')
    parser.add_argument('--review-ref')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runtime = read_json(args.runtime)
    if args.uids:
        for key, value in read_json(args.uids).items():
            if key not in ('cluster_uid', 'namespace_uid', 'deployment_uids'):
                parser.error('uids JSON must contain only cluster, namespace and Deployment UIDs')
            if key in runtime and runtime[key] != value:
                parser.error('runtime and captured UIDs disagree')
            runtime[key] = value
    documents = render(read_json(args.images), runtime, health_node=args.health_node,
                       operation=args.operation, review_ref=args.review_ref)
    with args.output.open('x', encoding='utf-8') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)
    print(f'Rendered review ConfigMap + Job: {args.output}; no apply or approval performed')


if __name__ == '__main__':
    main()
