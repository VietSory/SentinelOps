"""Offline AWS collector configuration; no account, cluster or parity proof."""
import json
import sys

import pytest
import yaml

from scripts import render_collector_manifests as manifests

PARAMETERS = {'region': 'eu-central-1', 'environment': 'dev',
              'exporter_role_arn': 'arn:aws:iam::123456789012:role/sentinelops-telemetry-exporter',
              'trace_sampling_percentage': 10}


def bundle(**changes):
    return manifests.render(manifests.CHECK_IMAGE, **{**PARAMETERS, **changes})


def by_kind(documents, kind):
    return next(d for d in documents if d['kind'] == kind)


def test_explicit_web_identity_has_only_sts_audience_and_no_node_or_kubernetes_credentials():
    docs = bundle()
    assert len(docs) == 6
    assert {d['kind'] for d in docs} == {'Namespace', 'ServiceAccount', 'ConfigMap', 'Deployment', 'Service', 'NetworkPolicy'}
    account = by_kind(docs, 'ServiceAccount')
    assert account['metadata']['name'] == 'telemetry-exporter'
    assert account['automountServiceAccountToken'] is False
    pod = by_kind(docs, 'Deployment')['spec']['template']['spec']
    assert pod['serviceAccountName'] == account['metadata']['name']
    assert pod['automountServiceAccountToken'] is False
    assert pod['securityContext']['runAsNonRoot'] is True
    container, = pod['containers']
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert container['securityContext']['allowPrivilegeEscalation'] is False
    assert container['securityContext']['capabilities']['drop'] == ['ALL']
    env = {e['name']: e.get('value') for e in container['env']}
    assert env['AWS_ROLE_ARN'] == PARAMETERS['exporter_role_arn']
    assert env['AWS_WEB_IDENTITY_TOKEN_FILE'] == '/var/run/secrets/aws/token'
    assert env['AWS_EC2_METADATA_DISABLED'] == 'true'
    assert env['AWS_STS_REGIONAL_ENDPOINTS'] == 'regional'
    token = next(v for v in pod['volumes'] if v['name'] == 'aws-token')['projected']
    assert token['sources'] == [{'serviceAccountToken': {
        'audience': 'sts.amazonaws.com', 'expirationSeconds': 3600, 'path': 'token'}}]
    assert token['defaultMode'] == 0o440
    assert pod['securityContext']['fsGroup'] == pod['securityContext']['runAsGroup'] == 10001
    wire = json.dumps(docs)
    for forbidden in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'hostPath', 'hostNetwork',
                      'secretKeyRef', 'SENTINELOPS_MODEL_', 'SENTINELOPS_ACTION_', 'LoadBalancer'):
        assert forbidden not in wire


def test_metrics_logs_and_traces_have_separate_aws_exporters_and_no_local_backend_fallback():
    docs = bundle()
    config = yaml.safe_load(by_kind(docs, 'ConfigMap')['data']['config.yaml'])
    pipelines = config['service']['pipelines']
    assert {name: p['exporters'] for name, p in pipelines.items()} == {
        'metrics': ['awsemf'], 'logs': ['awscloudwatchlogs'], 'traces': ['awsxray']}
    emf, logs, xray = [config['exporters'][name] for name in ('awsemf', 'awscloudwatchlogs', 'awsxray')]
    assert emf['log_group_name'] == logs['log_group_name'] == '/sentinelops/dev/workloads'
    assert emf['log_stream_name'] == 'otel-metrics-${env:POD_UID}'
    assert logs['log_stream_name'] == 'otel-logs-${env:POD_UID}'
    assert logs['raw_log'] is False
    assert emf['dimension_rollup_option'] == 'NoDimensionRollup'
    assert emf['metric_declarations'][0]['dimensions'] == [['service', 'route', 'status_code']]
    assert xray['local_mode'] is True and xray['telemetry']['enabled'] is False
    assert xray['aws_log_groups'] == ['/sentinelops/dev/workloads']
    assert 'probabilistic_sampler' in pipelines['traces']['processors']
    assert all('probabilistic_sampler' not in pipelines[n]['processors'] for n in ('logs', 'metrics'))
    assert config['processors']['probabilistic_sampler']['sampling_percentage'] == 10
    assert config['processors']['attributes/sampling']['actions'] == [{'key': 'sampling.priority', 'action': 'delete'}]
    for exporter in config['exporters'].values():
        assert exporter['region'] == PARAMETERS['region']
        for forbidden in ('role_arn', 'endpoint', 'no_verify_ssl', 'log_retention', 'tags'):
            assert forbidden not in exporter
    deployment = by_kind(docs, 'Deployment')
    assert deployment['spec']['replicas'] == 1 and deployment['spec']['strategy'] == {'type': 'Recreate'}


def test_otlp_is_internal_and_ingress_is_only_for_same_namespace_sentinelops_pods():
    docs = bundle()
    service = by_kind(docs, 'Service')['spec']
    assert service['type'] == 'ClusterIP'
    assert {p['port'] for p in service['ports']} == {4317, 4318}
    assert by_kind(docs, 'NetworkPolicy')['spec'] == {
        'podSelector': {'matchLabels': {'app': 'otel-collector'}}, 'policyTypes': ['Ingress'],
        'ingress': [{'from': [{'podSelector': {'matchLabels': {'app.kubernetes.io/part-of': 'sentinelops'}}}],
                     'ports': [{'protocol': 'TCP', 'port': 4317}, {'protocol': 'TCP', 'port': 4318}]}]}


@pytest.mark.parametrize('field,value', [
    ('region', '${env:AWS_SECRET_ACCESS_KEY}'), ('region', 'eu-central-1\n'), ('region', None),
    ('environment', 'aws-dev'), ('environment', '../prod'), ('environment', None),
    ('exporter_role_arn', 'arn:aws:iam::123456789012:role/sentinelops-telemetry-reader'),
    ('exporter_role_arn', 'arn:aws:iam::*:role/sentinelops-telemetry-exporter'),
    ('exporter_role_arn', 'arn:aws:iam::123456789012:role/sentinelops-telemetry-exporter\n'),
    ('exporter_role_arn', 'credential-value'), ('exporter_role_arn', None),
    ('trace_sampling_percentage', 0), ('trace_sampling_percentage', -1),
    ('trace_sampling_percentage', 101), ('trace_sampling_percentage', float('nan')),
    ('trace_sampling_percentage', float('inf')), ('trace_sampling_percentage', True),
    ('trace_sampling_percentage', '10'),
])
def test_invalid_or_interpolated_inputs_never_render(field, value):
    with pytest.raises(ValueError):
        bundle(**{field: value})


@pytest.mark.parametrize('image', ['otel/opentelemetry-collector-contrib:latest', None, 'image@sha256:abc'])
def test_unpinned_images_are_rejected(image):
    with pytest.raises(ValueError):
        manifests.render(image, **PARAMETERS)


@pytest.mark.parametrize('change', [{'region': 'ap-southeast-1'}, {'environment': 'prod'},
                                    {'trace_sampling_percentage': 25},
                                    {'exporter_role_arn': 'arn:aws:iam::123456789012:role/next-telemetry-exporter'}])
def test_changed_destination_sampling_or_identity_triggers_rollout(change):
    before = by_kind(bundle(), 'Deployment')['spec']['template']
    after = by_kind(bundle(**change), 'Deployment')['spec']['template']
    assert before != after
    assert before == by_kind(bundle(), 'Deployment')['spec']['template']


def test_cli_output_is_exclusive_and_deterministic(tmp_path, monkeypatch):
    target = tmp_path/'collector.yaml'
    args = ['render_collector_manifests', '--collector-image', manifests.CHECK_IMAGE,
            '--region', 'eu-central-1', '--environment', 'dev', '--exporter-role-arn',
            PARAMETERS['exporter_role_arn'], '--trace-sampling-percentage', '10', '--output', str(target)]
    monkeypatch.setattr(sys, 'argv', args)
    manifests.main()
    original = target.read_bytes()
    assert list(yaml.safe_load_all(original)) == bundle()
    with pytest.raises(FileExistsError):
        manifests.main()
    assert original == target.read_bytes()
    args[-1] = str(tmp_path/'invalid.yaml')
    args[args.index('--region') + 1] = '${env:TOKEN}'
    with pytest.raises(ValueError):
        manifests.main()
    assert not (tmp_path/'invalid.yaml').exists()
