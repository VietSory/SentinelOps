"""Offline OTLP gateway for the declared AWS CloudWatch/X-Ray environment.

References a separately provisioned exporter role and encrypted workload log
group. No AWS/Kubernetes calls, credentials, source parity or model authority.
"""
from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path
import re

import yaml

from sentinelops.remediation.domain import image_digest

NAMESPACE = 'sentinelops'
NAME = 'otel-collector'
ACCOUNT = 'telemetry-exporter'
CONFIG = 'sentinelops-aws-collector'
# The checker runs this known component set by immutable registry digest.
CHECK_IMAGE = ('otel/opentelemetry-collector-contrib@sha256:'
               '39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6')
REGION = re.compile(r'[a-z]{2}(?:-[a-z]+)+-[0-9]')
ROLE = re.compile(r'arn:aws:iam::[0-9]{12}:role/[a-z][a-z0-9-]{2,20}-telemetry-exporter')


def collector_config(*, region: str, environment: str, trace_sampling_percentage: float) -> dict:
    if not isinstance(region, str) or not REGION.fullmatch(region):
        raise ValueError('an explicit AWS region is required')
    if environment not in ('dev', 'staging', 'prod'):
        raise ValueError('environment must match the AWS baseline: dev, staging or prod')
    if (isinstance(trace_sampling_percentage, bool)
            or not isinstance(trace_sampling_percentage, (int, float))
            or not math.isfinite(trace_sampling_percentage)
            or not 0 < trace_sampling_percentage <= 100):
        raise ValueError('explicit trace sampling percentage must be finite and in (0, 100]')
    group = f'/sentinelops/{environment}/workloads'
    return {
        'extensions': {'health_check': {'endpoint': '0.0.0.0:13133'}},
        'receivers': {'otlp': {'protocols': {
            'grpc': {'endpoint': '0.0.0.0:4317'}, 'http': {'endpoint': '0.0.0.0:4318'}}}},
        'processors': {
            'memory_limiter': {'check_interval': '1s', 'limit_mib': 384, 'spike_limit_mib': 96},
            'batch': {'timeout': '1s', 'send_batch_size': 512, 'send_batch_max_size': 512},
            # Do not let a producer's sampling.priority bypass the declared rate.
            'attributes/sampling': {'actions': [{'key': 'sampling.priority', 'action': 'delete'}]},
            'probabilistic_sampler': {'sampling_percentage': float(trace_sampling_percentage),
                                      'mode': 'hash_seed', 'hash_seed': 0},
        },
        'exporters': {
            'awsemf': {
                'region': region, 'namespace': f'SentinelOps/{environment}',
                'log_group_name': group, 'log_stream_name': 'otel-metrics-${env:POD_UID}',
                'dimension_rollup_option': 'NoDimensionRollup', 'max_retries': 2,
                'metric_declarations': [{
                    'dimensions': [['service', 'route', 'status_code']],
                    'metric_name_selectors': ['^sentinelops_http_(requests|errors|request_duration_ms)$'],
                }],
            },
            'awscloudwatchlogs': {
                'region': region, 'log_group_name': group,
                'log_stream_name': 'otel-logs-${env:POD_UID}', 'raw_log': False,
                'sending_queue': {'num_consumers': 2, 'queue_size': 64},
            },
            'awsxray': {
                'region': region, 'local_mode': True, 'num_workers': 2,
                'request_timeout_seconds': 10, 'max_retries': 2,
                'index_all_attributes': False, 'aws_log_groups': [group],
                'telemetry': {'enabled': False},
            },
        },
        'service': {
            'extensions': ['health_check'],
            'pipelines': {
                'metrics': {'receivers': ['otlp'], 'processors': ['memory_limiter', 'batch'],
                            'exporters': ['awsemf']},
                'logs': {'receivers': ['otlp'], 'processors': ['memory_limiter', 'batch'],
                         'exporters': ['awscloudwatchlogs']},
                'traces': {'receivers': ['otlp'], 'processors': [
                    'memory_limiter', 'attributes/sampling', 'probabilistic_sampler', 'batch'],
                    'exporters': ['awsxray']},
            },
        },
    }


def render(image: str, *, region: str, environment: str, exporter_role_arn: str,
           trace_sampling_percentage: float) -> list[dict]:
    image_digest(image)
    if not isinstance(exporter_role_arn, str) or not ROLE.fullmatch(exporter_role_arn):
        raise ValueError('use the declared telemetry-exporter IAM role ARN, never a reader or credential')
    config = collector_config(region=region, environment=environment,
                              trace_sampling_percentage=trace_sampling_percentage)
    config_yaml = yaml.safe_dump(config, sort_keys=False)
    checksum = hashlib.sha256(config_yaml.encode()).hexdigest()
    labels = {'app.kubernetes.io/part-of': 'sentinelops', 'app': NAME}

    def metadata(name):
        return {'name': name, 'namespace': NAMESPACE, 'labels': dict(labels)}

    container = {
        'name': 'collector', 'image': image, 'imagePullPolicy': 'IfNotPresent',
        'args': ['--config=/etc/otelcol/config.yaml'],
        'env': [
            {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}},
            {'name': 'AWS_REGION', 'value': region}, {'name': 'AWS_DEFAULT_REGION', 'value': region},
            {'name': 'AWS_ROLE_ARN', 'value': exporter_role_arn},
            {'name': 'AWS_WEB_IDENTITY_TOKEN_FILE', 'value': '/var/run/secrets/aws/token'},
            {'name': 'AWS_STS_REGIONAL_ENDPOINTS', 'value': 'regional'},
            {'name': 'AWS_EC2_METADATA_DISABLED', 'value': 'true'},
        ],
        'ports': [{'name': 'otlp-grpc', 'containerPort': 4317},
                  {'name': 'otlp-http', 'containerPort': 4318},
                  {'name': 'health', 'containerPort': 13133}],
        'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                      'limits': {'cpu': '500m', 'memory': '512Mi'}},
        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                            'capabilities': {'drop': ['ALL']}},
        'volumeMounts': [{'name': 'config', 'mountPath': '/etc/otelcol', 'readOnly': True},
                         {'name': 'aws-token', 'mountPath': '/var/run/secrets/aws', 'readOnly': True}],
        'startupProbe': {'httpGet': {'path': '/', 'port': 'health'},
                         'periodSeconds': 2, 'timeoutSeconds': 2, 'failureThreshold': 30},
        'livenessProbe': {'httpGet': {'path': '/', 'port': 'health'},
                          'periodSeconds': 10, 'timeoutSeconds': 2},
        'readinessProbe': {'httpGet': {'path': '/', 'port': 'health'},
                           'periodSeconds': 5, 'timeoutSeconds': 2},
    }
    return [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}},
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata(ACCOUNT),
         'automountServiceAccountToken': False},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(CONFIG),
         'data': {'config.yaml': config_yaml}},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata(NAME),
         'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': NAME}},
                  # EMF delta conversion has in-memory state. Do not route one
                  # cumulative series randomly across simultaneous collectors.
                  'strategy': {'type': 'Recreate'},
                  'template': {'metadata': {'labels': labels, 'annotations': {
                      'sentinelops.io/config-sha256': checksum}}, 'spec': {
                      'serviceAccountName': ACCOUNT, 'automountServiceAccountToken': False,
                      'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                          'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
                      'containers': [container],
                      'volumes': [{'name': 'config', 'configMap': {'name': CONFIG}},
                                  {'name': 'aws-token', 'projected': {'defaultMode': 288, 'sources': [
                                      {'serviceAccountToken': {'audience': 'sts.amazonaws.com',
                                                               'expirationSeconds': 3600, 'path': 'token'}}]}}],
                  }}}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(NAME),
         'spec': {'type': 'ClusterIP', 'selector': {'app': NAME}, 'ports': [
             {'name': 'otlp-grpc', 'port': 4317, 'targetPort': 'otlp-grpc'},
             {'name': 'otlp-http', 'port': 4318, 'targetPort': 'otlp-http'}]}},
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': metadata(NAME),
         'spec': {'podSelector': {'matchLabels': {'app': NAME}}, 'policyTypes': ['Ingress'],
                  'ingress': [{'from': [{'podSelector': {'matchLabels': {
                      'app.kubernetes.io/part-of': 'sentinelops'}}}],
                      'ports': [{'protocol': 'TCP', 'port': 4317}, {'protocol': 'TCP', 'port': 4318}]}]}},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collector-image', required=True, help='Collector-contrib image pinned by registry digest')
    parser.add_argument('--region', required=True)
    parser.add_argument('--environment', required=True, choices=['dev', 'staging', 'prod'])
    parser.add_argument('--exporter-role-arn', required=True)
    parser.add_argument('--trace-sampling-percentage', required=True, type=float)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    documents = render(args.collector_image, region=args.region, environment=args.environment,
                       exporter_role_arn=args.exporter_role_arn,
                       trace_sampling_percentage=args.trace_sampling_percentage)
    with args.output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__':
    main()
