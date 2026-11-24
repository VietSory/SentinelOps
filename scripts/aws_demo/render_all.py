"""Compose existing offline renderers, then validate before publishing a new directory."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

import yaml

from scripts import kubernetes_testbed as testbed
from scripts import render_api_manifests as api
from scripts import render_collector_manifests as collector
from scripts import render_worker_manifests as workers
from sentinelops.remediation.domain import image_digest
from sentinelops.remediation.pod_memory_metrics import COLLECTOR

ROOT = Path(__file__).resolve().parents[2]
IMAGE_KEYS = testbed.IMAGE_KEYS | {'control-plane', 'aws-collector'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def terraform_values(raw):
    """Accept terraform output -json or a plain map; never execute Terraform."""
    return {key: value['value'] if isinstance(value, dict) and 'value' in value else value
            for key, value in raw.items()}


def traffic(image):
    # The service image installs sentinelops/httpx, but does not COPY scripts/.
    # Mount the exact existing drive() function instead of importing its local
    # kind/docker/model harness (which would require tools and optional ML deps).
    path = ROOT / 'scripts/run_s2_h4.py'
    source = path.read_text()
    tree = ast.parse(source)
    drive = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'drive')
    driver = ast.get_source_segment(source, drive)
    wrapper = (Path(__file__).with_name('traffic_loop.py')).read_text()
    code = wrapper.replace('# EXISTING_DRIVE_FUNCTION', driver)
    labels = {'app': 'sentinelops-traffic', 'app.kubernetes.io/part-of': 'sentinelops'}
    metadata = {'name': 'sentinelops-traffic', 'namespace': testbed.NAMESPACE, 'labels': labels}
    import hashlib
    checksum = hashlib.sha256(code.encode()).hexdigest()
    return [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata,
         'data': {'traffic.py': code}},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': metadata, 'spec': {
            'replicas': 1, 'strategy': {'type': 'Recreate'},
            'selector': {'matchLabels': {'app': 'sentinelops-traffic'}},
            'template': {'metadata': {'labels': labels, 'annotations': {
                'sentinelops.io/config-sha256': checksum}}, 'spec': {
                'automountServiceAccountToken': False,
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001,
                                    'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [{'name': 'traffic', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                    'command': ['python', '-u', '/driver/traffic.py'],
                    'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'},
                                  'limits': {'cpu': '250m', 'memory': '128Mi'}},
                    'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                        'capabilities': {'drop': ['ALL']}},
                    'volumeMounts': [{'name': 'driver', 'mountPath': '/driver', 'readOnly': True}]}],
                'volumes': [{'name': 'driver', 'configMap': {'name': 'sentinelops-traffic'}}]}}}},
    ]


def endpoint_with_port(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('cluster_endpoint must be credential-free HTTPS')
    return urlunsplit(parsed._replace(netloc=parsed.netloc + (':443' if parsed.port is None else '')))


def render(images, tf, runtime):
    early_warning = runtime.get('early_warning', False)
    if type(early_warning) is not bool:
        raise ValueError('early_warning must be an explicit boolean')
    image_keys = IMAGE_KEYS | {'operator-ui'} if early_warning else IMAGE_KEYS
    if set(images) != image_keys:
        raise ValueError('images.json requires exactly: ' + ', '.join(sorted(image_keys)))
    for value in images.values():
        image_digest(value)
    bindings = tf['collector_bindings']
    if bindings['region'] != 'ap-southeast-1' or bindings['environment'] != 'dev':
        raise ValueError('this EKS testbed targets ap-southeast-1/dev')
    if bindings['region'] != tf['region']:
        raise ValueError('Terraform region and collector bindings disagree')
    if bindings['exporter_role_arn'] != tf['workload_role_arns']['telemetry-exporter']:
        raise ValueError('collector role must match Terraform telemetry-exporter output')
    if bindings['workload_log_group'] != '/sentinelops/dev/workloads':
        raise ValueError('unexpected collector workload log group')
    ecr_keys = ('application', 'control-plane', 'operator-ui') if early_warning else ('application', 'control-plane')
    for key in ecr_keys:
        expected = tf['image_repository_urls']['control-plane' if key == 'operator-ui' else key]
        if images[key].split('@')[0] != expected:
            raise ValueError(f'{key} digest must match Terraform ECR repository')
    business = testbed.render({key: images[key] for key in testbed.IMAGE_KEYS}, target='eks')
    aws = collector.render(images['aws-collector'], region=bindings['region'],
        environment=bindings['environment'], exporter_role_arn=bindings['exporter_role_arn'],
        trace_sampling_percentage=bindings['trace_sampling_percentage'])
    api_options = {}
    worker_options = {key: runtime[key] for key in ('remediation_store_claim', 'known_good_store_claim',
        'deployment_change_store_claim', 'model_claim', 'model_release_ref', 'policy_enabled') if key in runtime}
    if early_warning:
        if images['operator-ui'] == images['control-plane']:
            raise ValueError('operator-ui must be the distinct live overlay digest')
        edge_domain, topic = tf.get('edge_domain'), tf.get('demo_alerts_topic_arn')
        binding = tf.get('operator_oidc_binding')
        if not edge_domain or not topic or not binding:
            raise ValueError('early warning requires edge, operator OIDC and SNS Terraform outputs')
        oidc = {key: binding[key] for key in ('issuer', 'audience', 'jwks_url', 'audience_claim', 'approval_scope')}
        oidc['login_domain'] = tf.get('operator_login_domain')
        api_options.update(controller_sidecar=True, service_type='NodePort', node_port=tf.get('edge_api_node_port'))
        worker_options.update(early_warning=True, demo_ui=True, demo_ui_image=images['operator-ui'],
            alerts_topic_arn=topic, ui_url=f'https://{edge_domain}/ui/', alerts_otlp_endpoint=f'http://{COLLECTOR}:4317')
    else:
        oidc = runtime['oidc']
        if runtime.get('api_action_read_secret'):
            api_options['action_read_secret'] = runtime['api_action_read_secret']
    api_docs = api.render(images['control-plane'], environment='dev',
        control_secret=runtime['api_control_secret'], **oidc, **api_options)
    # Build a public, credential-free RDS URL. Secret contents never enter this process.
    database = f"postgresql://{tf['control_database_endpoint']}:5432/sentinelops_control?sslmode=verify-full"
    worker_docs = workers.render(images['control-plane'], environment='dev',
        control_database=database, database_ca_config=runtime['database_ca_config'],
        health_store_claim=runtime['health_store_claim'], incident_store_claim=runtime['incident_store_claim'],
        health_stream=runtime['health_stream'], kubernetes_server=endpoint_with_port(tf['cluster_endpoint']),
        cluster_uid=runtime['cluster_uid'], namespace_uid=runtime['namespace_uid'],
        deployment_uids=runtime['deployment_uids'], observer_secret=runtime['observer_secret'], **worker_options)
    groups = {'10-testbed.yaml': business, '20-collector.yaml': aws,
              '30-api.yaml': api_docs, '40-workers.yaml': worker_docs, '50-traffic.yaml': traffic(images['application'])}
    # One Namespace definition per namespace; keep dependencies in numbered files.
    namespaces = {}
    for documents in groups.values():
        for document in documents:
            if document['kind'] == 'Namespace':
                meta = document['metadata']
                namespaces.setdefault(meta['name'], {}).update(meta.get('labels', {}))
        documents[:] = [doc for doc in documents if doc['kind'] != 'Namespace']
    return {'00-namespaces.yaml': [{'apiVersion': 'v1', 'kind': 'Namespace',
        'metadata': {'name': name, 'labels': labels}} for name, labels in sorted(namespaces.items())], **groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path)
    parser.add_argument('terraform_outputs', type=Path)
    parser.add_argument('runtime', type=Path, help='Public OIDC, real UID/health-stream and resource references')
    parser.add_argument('--output', type=Path, default=Path('artifacts/aws-demo/k8s'))
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error('output must be a new directory; choose --output for another run')
    groups = render(read_json(args.images), terraform_values(read_json(args.terraform_outputs)), read_json(args.runtime))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Validate in a sibling staging directory; failed validation never publishes k8s/.
    with tempfile.TemporaryDirectory(prefix='.k8s-render-', dir=output.parent) as private:
        stage = Path(private) / 'k8s'
        stage.mkdir()
        for name, documents in groups.items():
            (stage / name).write_text(yaml.safe_dump_all(documents, sort_keys=False))
        subprocess.run(['kubeconform', '-strict', '-summary', '-kubernetes-version', '1.34.0',
                        *map(str, sorted(stage.glob('*.yaml')))], check=True)
        os.rename(stage, output)
    print(f'Rendered and validated {sum(map(len, groups.values()))} resources: {output}')
    print('Apply order and external prerequisites: scripts/aws_demo/README.render.md')


if __name__ == '__main__':
    main()
