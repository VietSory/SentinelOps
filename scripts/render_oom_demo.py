"""Render the isolated OOM workload; no API calls or credentials in output."""
import argparse
import hashlib
from pathlib import Path

import yaml

from sentinelops.remediation.domain import image_digest
from sentinelops.remediation.pod_memory import NAME, NAMESPACE


def render(image, *, healthy=False):
    image_digest(image)
    source = (Path(__file__).parent / 'oom_demo_workload.py').read_text()
    labels = {'app': NAME, 'sentinelops.io/demo': 'oom'}
    return [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': NAME, 'namespace': NAMESPACE},
         'data': {'workload.py': source}},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': NAME, 'namespace': NAMESPACE},
         'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': NAME}},
            'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}},
            'template': {'metadata': {'labels': labels, 'annotations': {
                'sentinelops.io/workload-sha256': hashlib.sha256(source.encode()).hexdigest()}},
                'spec': {'automountServiceAccountToken': False,
                    'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001,
                                        'seccompProfile': {'type': 'RuntimeDefault'}},
                    'containers': [{'name': 'oom-demo', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                        'command': ['python', '-u', '/demo/workload.py'],
                        'ports': [{'containerPort': 8080}],
                        'resources': {'requests': {'cpu': '25m', 'memory': '32Mi'},
                                      'limits': {'cpu': '250m', 'memory': '256Mi' if healthy else '64Mi'}},
                        'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                            'capabilities': {'drop': ['ALL']}},
                        'volumeMounts': [{'name': 'code', 'mountPath': '/demo', 'readOnly': True}],
                        'readinessProbe': {'httpGet': {'path': '/health', 'port': 8080},
                                           'periodSeconds': 2, 'timeoutSeconds': 1}}],
                    'volumes': [{'name': 'code', 'configMap': {'name': NAME}}]}}}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': NAME, 'namespace': NAMESPACE},
         'spec': {'type': 'ClusterIP', 'selector': {'app': NAME},
                  'ports': [{'port': 8080, 'targetPort': 8080}]}},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--healthy', action='store_true', help='Use 256Mi for baseline or restore')
    args = parser.parse_args()
    print(yaml.safe_dump_all(render(args.image, healthy=args.healthy), sort_keys=False))


if __name__ == '__main__':
    main()
