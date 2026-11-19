"""Offline manifest/adapter checks; no AWS, provider or runtime acceptance claim."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import yaml

from scripts import render_worker_manifests as worker
from scripts import run_health_worker, run_incident_worker
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2, queries, source_configuration_hash
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers import kubernetes_evidence as evidence
from sentinelops.workers import metrics as worker_metrics
from sentinelops.workers.source_policy import SourcePolicy, _credential
from test_kubernetes_evidence import TARGET, inventory, original_log, pod_response, projected
from test_prometheus_capture import END, source_response
from test_rca_replay import POLICY as RCA_POLICY

IMAGE = 'registry.example.invalid/control-plane@sha256:' + 'a' * 64
DATABASE = 'postgresql://control.example.invalid:5432/sentinelops_control?sslmode=verify-full'
RELEASE = 'artifact://manifests/' + 'b' * 64 + '.json'
PARAMETERS = {'environment': 'aws-dev', 'control_database': DATABASE,
    'database_ca_config': 'control-database-ca', 'health_store_claim': 'health-artifacts',
    'incident_store_claim': 'incident-artifacts', 'health_stream': 'c' * 64,
    'kubernetes_server': 'https://eks.example.invalid:443', 'cluster_uid': 'kube-system-uid',
    'namespace_uid': 'namespace-uid', 'deployment_uids': {s: s + '-uid' for s in BUSINESS_ROUTES},
    'observer_secret': 'incident-observer-credentials'}


def controller(documents):
    return next(d for d in documents if d['kind'] == 'Deployment'
                and d['metadata']['name'] == 'remediation-controller')


def test_oidc_ui_sidecar_shares_controller_rwo_store_without_public_service_or_action_credentials():
    documents = worker.render(IMAGE, **PARAMETERS, demo_ui=True)
    assert not any(d['kind'] in ('Service', 'Ingress') for d in documents)
    pod = controller(documents)['spec']['template']['spec']
    main, ui = pod['containers']
    assert ui['name'] == 'operator-ui' and ui['image'] == main['image'] == IMAGE
    assert pod['automountServiceAccountToken'] is False
    assert ui['ports'] == [{'name': 'http', 'containerPort': 8000}]
    assert ui['envFrom'] == [{'configMapRef': {'name': 'sentinelops-api-runtime', 'optional': False}}]
    mounts = {m['name']: m for m in ui['volumeMounts']}
    assert set(mounts) == {'tmp', 'database-ca', 'remediation-artifacts'}
    shared = next(m for m in main['volumeMounts'] if m['name'] == 'remediation-artifacts')
    assert mounts['remediation-artifacts'] == shared == {'name': 'remediation-artifacts', 'mountPath': '/artifacts/remediation'}
    volume = next(v for v in pod['volumes'] if v['name'] == 'remediation-artifacts')
    assert volume['persistentVolumeClaim']['claimName'] == 'remediation-artifacts'
    env = {e['name']: e for e in ui['env']}
    assert env['SENTINELOPS_ENV']['value'] == 'aws-dev'
    assert env['SENTINELOPS_ENABLE_DEMO']['value'] == 'false'
    assert not any('ACTION' in name or 'TOKEN' in name for name in env)
    assert '0.0.0.0' in ui['command'][2] and 'access_log=False' in ui['command'][2]
    assert ui['securityContext']['readOnlyRootFilesystem']
    assert len(controller(worker.render(IMAGE, **PARAMETERS))['spec']['template']['spec']['containers']) == 1
    assert controller(documents)['spec']['template']['metadata']['annotations'] != controller(
        worker.render(IMAGE, **PARAMETERS))['spec']['template']['metadata']['annotations']


def test_ui_bootstrap_uses_nonlocal_oidc_and_urlencodes_database_password(monkeypatch):
    import sentinelops.api
    import uvicorn
    from sentinelops.api_settings import Settings
    pod = controller(worker.render(IMAGE, **PARAMETERS, demo_ui=True))['spec']['template']['spec']
    ui = pod['containers'][1]
    for name in ('SENTINELOPS_API_TOKEN', 'SENTINELOPS_ACTION_READ_DATABASE_URL',
                 'SENTINELOPS_OIDC_ISSUER', 'SENTINELOPS_OIDC_AUDIENCE', 'SENTINELOPS_OIDC_JWKS_URL'):
        monkeypatch.delenv(name, raising=False)
    secret_values = {('control-runtime', 'database-url'): DATABASE,
                     ('control-runtime', 'password'): 'space @:/?#%'}
    for item in ui['env']:
        ref = item.get('valueFrom', {}).get('secretKeyRef')
        monkeypatch.setenv(item['name'], item['value'] if ref is None else secret_values[ref['name'], ref['key']])
    seen = []
    for name, value in {'SENTINELOPS_OIDC_ISSUER': 'https://identity.invalid/pool',
        'SENTINELOPS_OIDC_AUDIENCE': 'public-client', 'SENTINELOPS_OIDC_AUDIENCE_CLAIM': 'client_id',
        'SENTINELOPS_OIDC_JWKS_URL': 'https://identity.invalid/pool/.well-known/jwks.json',
        'SENTINELOPS_OIDC_LOGIN_DOMAIN': 'https://login.invalid'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('SENTINELOPS_OPERATOR_TOKEN', raising=False)
    monkeypatch.setattr(sentinelops.api, 'create_app', lambda settings: settings)
    monkeypatch.setattr(uvicorn, 'run', lambda settings, **kwargs: seen.append((settings, kwargs)))
    exec(ui['command'][2], {})
    settings, options = seen[0]
    assert settings.environment == 'aws-dev' and settings.api_token is None and settings.oidc is not None
    assert settings.operator_token is None and settings.demo_mode is False
    assert settings.runbook_proposal_store == '/artifacts/remediation'
    assert settings.control_database_url.startswith('postgresql://control_writer:space%20%40%3A%2F%3F%23%25@')
    assert options == {'host': '0.0.0.0', 'port': 8000, 'access_log': False}
    monkeypatch.setenv('UI_DATABASE_PASSWORD', 'space @:/?#%')
    monkeypatch.setenv('SENTINELOPS_OPERATOR_TOKEN', 'x' * 40)
    with pytest.raises(ValueError, match='OIDC or static'):
        exec(ui['command'][2], {})


def test_trial_ui_requires_explicit_boolean():
    with pytest.raises(ValueError, match='demo_ui'):
        worker.render(IMAGE, **PARAMETERS, demo_ui='true')


@pytest.mark.parametrize('enabled', [False, True])
def test_controller_private_sa_token_and_exact_payment_rbac(enabled):
    documents = worker.render(IMAGE, **PARAMETERS, policy_enabled=enabled)
    deployment = controller(documents)
    assert deployment['spec']['replicas'] == 1
    assert deployment['spec']['strategy'] == {'type': 'Recreate'}
    pod = deployment['spec']['template']['spec']
    assert pod['serviceAccountName'] == 'payment-rollback'
    assert pod['automountServiceAccountToken'] is False
    main, = pod['containers']
    init, = pod['initContainers']
    assert main['image'] == init['image'] == IMAGE
    assert main['command'] == ['python', 'scripts/run_remediation_controller.py']
    assert ('--policy-enabled' in main['args']) is enabled
    assert {'name': 'SENTINELOPS_KUBERNETES_REMOTE_SERVER',
            'value': 'https://kubernetes.default.svc'} in main['env']
    volumes = {v['name']: v for v in pod['volumes']}
    assert volumes['service-account']['projected']['sources'] == [
        {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 86400}},
        {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]
    assert init['command'] == ['python', '-c', worker.COPY_CREDENTIALS]
    assert init['args'] == ['/credentials', '/service-account/token', '/service-account/ca.crt']
    assert all(m['name'] != 'service-account' for m in main['volumeMounts'])
    assert next(m for m in main['volumeMounts'] if m['name'] == 'credentials')['readOnly']
    assert pod['securityContext']['runAsUser'] == 10001
    assert volumes['credentials']['emptyDir']['medium'] == 'Memory'
    for name in ('health-artifacts', 'incident-artifacts', 'review-artifacts'):
        assert volumes[name]['persistentVolumeClaim']['readOnly'] is True
    for role in (d for d in documents if d['kind'] in ('Role', 'ClusterRole')):
        for rule in role['rules']:
            if 'patch' in rule['verbs']:
                assert rule == {'apiGroups': ['apps'], 'resources': ['deployments'],
                    'resourceNames': ['payment-service'], 'verbs': ['get', 'patch']}
            assert not set(rule['verbs']) & {'create', 'update', 'delete', '*'}
    bindings = [d for d in documents if d['kind'].endswith('RoleBinding')
                and d['metadata']['name'] != worker.OBSERVER]
    assert all(d['subjects'] == [{'kind': 'ServiceAccount', 'name': 'payment-rollback',
                                 'namespace': worker.NAMESPACE}] for d in bindings)


def test_controller_projected_token_copy_passes_actual_adapter_validation(tmp_path, monkeypatch):
    from sentinelops.remediation.kubernetes import LocalKubernetes
    import ssl
    source, private = tmp_path / 'projected', tmp_path / 'private'
    source.mkdir(); private.mkdir()
    token = source / '..token'; token.write_text('x' * 64)
    (source / 'token').symlink_to(token)
    ca = source / '..ca'; ca.write_text('test CA')
    (source / 'ca.crt').symlink_to(ca)
    pod = controller(worker.render(IMAGE, **PARAMETERS))['spec']['template']['spec']
    init, = pod['initContainers']
    subprocess.run([sys.executable, '-c', init['command'][2], str(private),
                    str(source / 'token'), str(source / 'ca.crt')], check=True)
    copied = private / 'token'
    assert not copied.is_symlink() and copied.stat().st_uid == os.getuid()
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600
    monkeypatch.setenv('SENTINELOPS_KUBERNETES_REMOTE_SERVER', 'https://kubernetes.default.svc')
    real_context = ssl.create_default_context()
    monkeypatch.setattr(ssl, 'create_default_context', lambda **kwargs: real_context)
    async def verify():
        with pytest.raises(ValueError, match='owned private regular file'):
            LocalKubernetes('https://kubernetes.default.svc', TARGET,
                token_file=source / 'token', ca_file=source / 'ca.crt')
        adapter = LocalKubernetes('https://kubernetes.default.svc', TARGET,
            token_file=copied, ca_file=private / 'ca.crt', transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        assert adapter.client.headers['Authorization'] == 'Bearer ' + 'x' * 64
        await adapter.close()
    asyncio.run(verify())


def test_controller_args_parse_in_controller_cli_and_preserve_model_wiring():
    from scripts.run_remediation_controller import parser
    pod = controller(worker.render(IMAGE, **PARAMETERS,
        model_claim='models', model_release_ref=RELEASE))['spec']['template']['spec']
    args = parser().parse_args(pod['containers'][0]['args'])
    assert args.source_store == Path(worker.HEALTH_ROOT)
    assert args.incident_store == Path(worker.INCIDENT_ROOT)
    assert args.store == Path('/artifacts/remediation')
    assert args.known_good_store == Path('/artifacts/review')
    assert args.model_store == Path('/models') and args.model_release_ref == RELEASE
    assert args.server == 'https://kubernetes.default.svc'
    assert args.policy_enabled is False and args.kill_switch == args.store / 'KILL_SWITCH'


@pytest.mark.parametrize('values', [
    {'remediation_store_claim': 'health-artifacts'}, {'known_good_store_claim': 'incident-artifacts'},
    {'remediation_store_claim': '../unsafe'}, {'known_good_store_claim': None}, {'policy_enabled': 'true'}])
def test_controller_rejects_unsafe_store_aliases_and_implicit_enabling(values):
    with pytest.raises(ValueError):
        worker.render(IMAGE, **PARAMETERS, **values)


def deployments(documents):
    return [d for d in documents if d['kind'] == 'Deployment'
            and d['metadata']['name'] != 'remediation-controller']


def configs(documents):
    return {d['metadata']['name']: d['data'] for d in documents if d['kind'] == 'ConfigMap'}


def test_resources_have_valid_references_and_separate_worker_entrypoints():
    documents = worker.render(IMAGE, **PARAMETERS)
    assert len(documents) == 18
    assert len({(d['kind'], d['metadata']['name']) for d in documents}) == 18
    namespace = next(d for d in documents if d['kind'] == 'Namespace')
    assert namespace['metadata']['name'] == 'sentinelops-b-actions'
    assert namespace['metadata']['labels']['sentinelops.dev/lane'] == 'B'
    accounts = {d['metadata']['name'] for d in documents if d['kind'] == 'ServiceAccount'}
    config_names = set(configs(documents))
    for document in documents:
        assert isinstance(document['apiVersion'], str)
        if document['kind'] not in ('Namespace', 'ClusterRole', 'ClusterRoleBinding'):
            assert document['metadata']['namespace'] == worker.NAMESPACE
    for deployment, name in zip(deployments(documents), ('health', 'incident'), strict=True):
        assert deployment['apiVersion'] == 'apps/v1'
        spec = deployment['spec']
        assert spec['replicas'] == 1 and spec['strategy'] == {'type': 'Recreate'}
        assert spec['selector']['matchLabels'].items() <= spec['template']['metadata']['labels'].items()
        pod = spec['template']['spec']
        assert pod['serviceAccountName'] in accounts and pod['automountServiceAccountToken'] is False
        assert pod['securityContext'] == {'runAsNonRoot': True, 'runAsUser': 10001,
            'runAsGroup': 10001, 'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}}
        volumes = {v['name']: v for v in pod['volumes']}
        for container in pod['containers'] + pod['initContainers']:
            assert container['image'] == IMAGE
            assert container['securityContext'] == {'allowPrivilegeEscalation': False,
                'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
            assert container['resources']['requests'] and container['resources']['limits']
            assert all(m['name'] in volumes for m in container['volumeMounts'])
        container, = pod['containers']
        assert container['command'] == ['python', f'scripts/run_{name}_worker.py']
        assert all(e['configMapRef']['name'] in config_names for e in container['envFrom'])
        assert container['env'][0] == {'name': 'CONTROL_DATABASE_URL', 'valueFrom': {'secretKeyRef': {
            'name': 'control-runtime', 'key': 'database-url', 'optional': False}}}
        assert '--source-policy' in container['args'] and 'in-cluster' in container['args']
        assert '--metrics-otlp-endpoint' not in container['args']
        assert '--metrics-instance' not in container['args']
        assert 'ports' not in container
    assert not any(d['kind'] in ('Secret', 'PersistentVolumeClaim', 'Service') for d in documents)


@pytest.fixture
def offline_otlp_exporters(monkeypatch):
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    exporters = []
    def reader(exporter, **kwargs):
        exporters.append(exporter)
        return InMemoryMetricReader()
    # Keep runtime validation and real OTLP exporter construction; disable sending.
    monkeypatch.setattr(worker_metrics, 'PeriodicExportingMetricReader', reader)
    try:
        yield exporters
    finally:
        for exporter in exporters:
            exporter.shutdown()


@pytest.mark.parametrize('environment', ['staging', 'production'])
@pytest.mark.parametrize('endpoint', ['http://127.0.0.1:4317', 'http://localhost:4317/'])
@pytest.mark.parametrize('slot', [0, 15])
def test_metrics_only_enable_main_workers_and_preserve_status_probes(
        environment, endpoint, slot, monkeypatch, offline_otlp_exporters):
    parameters = {**PARAMETERS, 'environment': environment}
    extra = {'metrics_otlp_endpoint': endpoint}
    if slot:
        extra.update(health_metrics_instance=f'health-{slot}', incident_metrics_instance=f'incidents-{slot}')
    documents = worker.render(IMAGE, **parameters, **extra)
    expected = deepcopy(worker.render(IMAGE, **parameters))
    runtime = configs(documents)[worker.CONFIG]
    for deployment, baseline, cli, instance in zip(deployments(documents), deployments(expected),
            (run_health_worker, run_incident_worker), (f'health-{slot}', f'incidents-{slot}'), strict=True):
        container, = deployment['spec']['template']['spec']['containers']
        original, = baseline['spec']['template']['spec']['containers']
        original['args'] += ['--metrics-otlp-endpoint', endpoint, '--metrics-instance', instance]
        assert '--status' not in container['args']
        for key in ('readinessProbe', 'livenessProbe'):
            assert container[key] == original[key]
            command = container[key]['exec']['command']
            assert '--status' in command
            assert '--metrics-otlp-endpoint' not in command and '--metrics-instance' not in command
        for arguments, status in ((container['args'], False),
                                  (container['readinessProbe']['exec']['command'][4:], True),
                                  (container['livenessProbe']['exec']['command'][4:], True)):
            seen = []
            async def capture(args):
                seen.append(args)
            monkeypatch.setattr(cli, 'run', capture)
            monkeypatch.setattr(sys, 'argv', [container['command'][1],
                *[runtime[a[2:-1]] if a.startswith('$(') else a for a in arguments]])
            cli.main()
            parsed, = seen
            assert parsed.status is status
            assert parsed.metrics_otlp_endpoint == (None if status else endpoint)
            if not status:
                assert parsed.metrics_instance == instance
                metrics_type = cli.WorkerMetrics if cli is run_health_worker else cli.IncidentWorkerMetrics
                metrics = metrics_type.otlp(parsed.metrics_otlp_endpoint,
                    environment=parsed.environment, instance=parsed.metrics_instance)
                try:
                    assert offline_otlp_exporters[-1]._endpoint == urlsplit(endpoint).netloc
                    assert metrics.provider._sdk_config.resource.attributes['deployment.environment'] == environment
                finally:
                    metrics.close()
    assert documents == expected


@pytest.mark.parametrize('environment', ['aws-dev', 'dev'])
@pytest.mark.parametrize('endpoint', ['http://127.0.0.1:4317', 'http://otel-collector:4317'])
def test_metrics_reject_environments_rejected_by_both_runtime_exporters(environment, endpoint):
    for metrics_type, instance in ((worker_metrics.WorkerMetrics, 'health-0'),
                                   (worker_metrics.IncidentWorkerMetrics, 'incidents-0')):
        with pytest.raises(ValueError, match='environment'):
            metrics_type.otlp(endpoint, environment=environment, instance=instance)
    with pytest.raises(ValueError, match='environment') as error:
        worker.render(IMAGE, **{**PARAMETERS, 'environment': environment}, metrics_otlp_endpoint=endpoint)
    assert repr(environment) in str(error.value)
    assert all(allowed in str(error.value) for allowed in ('local', 'test', 'staging', 'production'))


@pytest.mark.parametrize('endpoint', [
    'http://otel-collector:4317', 'http://otel-collector.sentinelops-b-actions.svc.cluster.local:4317',
    'http://10.0.0.1:4317', 'http://[::1]:4317', 'https://localhost:4317',
    'http://user:password@localhost:4317', 'http://localhost:4317/v1/metrics',
    'http://localhost:4317?token=value', 'http://localhost:4317#fragment',
    'http://localhost:0', 'http://localhost:65536', 'http://localhost:invalid', ''])
def test_metrics_reject_endpoints_rejected_by_both_runtime_exporters(endpoint):
    for metrics_type, instance in ((worker_metrics.WorkerMetrics, 'health-0'),
                                   (worker_metrics.IncidentWorkerMetrics, 'incidents-0')):
        with pytest.raises(ValueError, match='loopback'):
            metrics_type.otlp(endpoint, environment='staging', instance=instance)
    with pytest.raises(ValueError, match='endpoint') as error:
        worker.render(IMAGE, **{**PARAMETERS, 'environment': 'staging'}, metrics_otlp_endpoint=endpoint)
    assert repr(endpoint) in str(error.value)
    assert '127.0.0.1' in str(error.value) and 'localhost' in str(error.value)


@pytest.mark.parametrize('values', [
    {'health_metrics_instance': 'health-16'}, {'health_metrics_instance': 'incidents-0'},
    {'health_metrics_instance': None}, {'incident_metrics_instance': 'incidents-16'},
    {'incident_metrics_instance': 'health-0'}, {'incident_metrics_instance': 'incidents-01'}])
def test_metrics_reject_unbounded_or_mismatched_worker_instances(values):
    with pytest.raises(ValueError, match='stable'):
        worker.render(IMAGE, **{**PARAMETERS, 'environment': 'staging'},
            metrics_otlp_endpoint='http://127.0.0.1:4317', **values)


def test_observer_rbac_is_read_only_and_never_bound_to_health_worker():
    documents = worker.render(IMAGE, **PARAMETERS)
    roles = [d for d in documents if d['kind'] in ('Role', 'ClusterRole')
             and d['metadata']['name'] == worker.OBSERVER]
    for role in roles:
        for rule in role['rules']:
            assert set(rule['verbs']) <= {'get', 'list'}
            assert '*' not in rule['resources'] and 'secrets' not in rule['resources']
    bindings = [d for d in documents if d['kind'].endswith('RoleBinding')
                and d['metadata']['name'] == worker.OBSERVER]
    for binding in bindings:
        assert binding['subjects'] == [{'kind': 'ServiceAccount', 'name': 'incident-worker',
                                        'namespace': worker.NAMESPACE}]
        assert any(r['kind'] == binding['roleRef']['kind'] and
                   r['metadata']['name'] == binding['roleRef']['name'] for r in roles)
    serialized = yaml.safe_dump_all(documents)
    assert 'eks.amazonaws.com/role-arn' not in serialized
    assert 'hostPath' not in serialized and 'docker.sock' not in serialized


@pytest.mark.parametrize('optional_wiring', [False, True])
def test_status_probes_preserve_runtime_arguments_and_bound_connection_cost(optional_wiring, monkeypatch):
    extra = {'prometheus_secret': 'prometheus-auth', 'prometheus_token_key': 'token',
             'model_claim': 'models', 'model_release_ref': RELEASE} if optional_wiring else {}
    documents = worker.render(IMAGE, **PARAMETERS, **extra)
    runtime = configs(documents)[worker.CONFIG]
    for deployment, cli in zip(deployments(documents), (run_health_worker, run_incident_worker), strict=True):
        container, = deployment['spec']['template']['spec']['containers']
        for key, delay, period, failures in (('readinessProbe', 15, 30, 3), ('livenessProbe', 120, 120, 6)):
            probe = container[key]
            assert probe == {'exec': {'command': ['python', '-c', worker.STATUS_EXEC,
                container['command'][1], *container['args'], '--status']},
                'initialDelaySeconds': delay, 'timeoutSeconds': 10,
                'periodSeconds': period, 'failureThreshold': failures}
            command = probe['exec']['command']
            arguments = [runtime[a[2:-1]] if a.startswith('$(') else a for a in command[4:]]
            seen = []
            async def capture(args):
                seen.append(args)
            monkeypatch.setattr(cli, 'run', capture)
            monkeypatch.setattr(sys, 'argv', [command[3], *arguments])
            cli.main()
            args, = seen
            assert args.status is True and args.metrics_otlp_endpoint is None
            assert args.environment == PARAMETERS['environment']
            assert args.remote_control_database == DATABASE
            assert args.control_database_user == 'control_writer'
            assert args.control_database_password_file == Path('/credentials/database-password')
            assert bool(args.prometheus_token_file) is optional_wiring


def test_status_exec_expands_environment_without_shell_and_preserves_failure(tmp_path):
    script = tmp_path / 'inspect.py'
    script.write_text('import json, os, sys\n'
                      'print(json.dumps([sys.argv[1:], os.environ["CONTROL_DATABASE_URL"], '
                      'os.environ["SSL_CERT_FILE"]]))\n'
                      'sys.exit(17)\n')
    value = 'literal with spaces; $(do-not-run)'
    result = subprocess.run([sys.executable, '-c', worker.STATUS_EXEC, str(script),
        '--environment', '$(SENTINELOPS_ENV)', '--status'], capture_output=True, text=True,
        env={**os.environ, 'SENTINELOPS_ENV': value, 'CONTROL_DATABASE_URL': DATABASE,
             'SSL_CERT_FILE': '/etc/sentinelops/tls/ca-bundle.crt'})
    assert result.returncode == 17 and not result.stderr
    assert json.loads(result.stdout) == [['--environment', value, '--status'], DATABASE,
                                        '/etc/sentinelops/tls/ca-bundle.crt']


@pytest.mark.parametrize('status', [False, True])
def test_incident_cli_pool_sizes_and_database_failure_are_preserved(status, monkeypatch):
    documents = worker.render(IMAGE, **PARAMETERS)
    runtime = configs(documents)[worker.CONFIG]
    incident = deployments(documents)[1]['spec']['template']['spec']['containers'][0]
    arguments = [runtime[a[2:-1]] if a.startswith('$(') else a for a in incident['args']]
    if status:
        arguments.append('--status')
    monkeypatch.setattr(sys, 'argv', [incident['command'][1], *arguments])
    monkeypatch.setenv('CONTROL_DATABASE_URL', DATABASE)
    monkeypatch.setattr(SourcePolicy, 'database_options', lambda *args, **kwargs: {})
    create_pool = AsyncMock(side_effect=ConnectionError('control DB unavailable'))
    monkeypatch.setattr(run_incident_worker.asyncpg, 'create_pool', create_pool)
    with pytest.raises(SystemExit, match='incident worker failed: ConnectionError'):
        run_incident_worker.main()
    create_pool.assert_awaited_once_with(DATABASE, min_size=1 if status else 2,
                                         max_size=1 if status else 4, command_timeout=10)


def test_health_status_fails_when_control_database_is_unavailable(monkeypatch):
    documents = worker.render(IMAGE, **PARAMETERS)
    runtime = configs(documents)[worker.CONFIG]
    health = deployments(documents)[0]['spec']['template']['spec']['containers'][0]
    arguments = [runtime[a[2:-1]] if a.startswith('$(') else a for a in health['args']]
    monkeypatch.setattr(sys, 'argv', [health['command'][1], *arguments, '--status'])
    monkeypatch.setenv('CONTROL_DATABASE_URL', DATABASE)
    monkeypatch.setattr(SourcePolicy, 'database_options', lambda *args, **kwargs: {})
    monkeypatch.setattr(run_health_worker.PrometheusSource, 'verify_configuration',
                        lambda self: {'configuration_hash': 'a' * 64})
    # Configuration I/O is already stubbed; keep this DB failure check independent
    # of thread-pool scheduling and network access.
    monkeypatch.setattr(run_health_worker.asyncio, 'to_thread', AsyncMock(side_effect=lambda fn: fn()))
    create_pool = AsyncMock(side_effect=ConnectionError('control DB unavailable'))
    monkeypatch.setattr(run_health_worker.asyncpg, 'create_pool', create_pool)
    with pytest.raises(SystemExit, match='health worker failed: ConnectionError'):
        run_health_worker.main()
    assert create_pool.await_args.kwargs['min_size'] == 1


def test_artifacts_preserve_source_path_and_models_are_read_only_on_a_separate_claim():
    documents = worker.render(IMAGE, **PARAMETERS, model_claim='models', model_release_ref=RELEASE)
    health, incident = [d['spec']['template']['spec'] for d in deployments(documents)]
    for pod, read_only in ((health, False), (incident, True)):
        volume = next(v for v in pod['volumes'] if v['name'] == 'health-artifacts')
        mount = next(m for m in pod['containers'][0]['volumeMounts'] if m['name'] == 'health-artifacts')
        assert volume['persistentVolumeClaim'] == {'claimName': 'health-artifacts', 'readOnly': read_only}
        assert mount['mountPath'] == worker.HEALTH_ROOT and mount['readOnly'] is read_only
    assert not any(v['name'] == 'models' for v in health['volumes'])
    assert next(v for v in incident['volumes'] if v['name'] == 'models')['persistentVolumeClaim'] == {
        'claimName': 'models', 'readOnly': True}
    assert next(m for m in incident['containers'][0]['volumeMounts'] if m['name'] == 'models')['readOnly']
    baseline = worker.render(IMAGE, **PARAMETERS)
    assert not any(v['name'] == 'models' for d in deployments(baseline) for v in d['spec']['template']['spec']['volumes'])


@pytest.mark.parametrize('prometheus_auth', [False, True])
def test_credentials_are_file_references_with_private_copy_and_read_only_runtime_mount(prometheus_auth):
    extra = {'prometheus_secret': 'prometheus-auth', 'prometheus_token_key': 'token'} if prometheus_auth else {}
    documents = worker.render(IMAGE, **PARAMETERS, **extra)
    for deployment in deployments(documents):
        pod = deployment['spec']['template']['spec']
        container, = pod['containers']
        init, = pod['initContainers']
        assert init['command'] == ['python', '-c', worker.COPY_CREDENTIALS]
        assert init['args'][0] == worker.CREDENTIAL_ROOT
        volume = next(v for v in pod['volumes'] if v['name'] == 'credentials')
        assert volume['emptyDir']['medium'] == 'Memory'
        assert next(m for m in container['volumeMounts'] if m['name'] == 'credentials')['readOnly']
        assert not any(m['name'] in ('database-password', 'observer-credentials', 'prometheus-token')
                       for m in container['volumeMounts'])
        secret = next(v['secret'] for v in pod['volumes'] if v['name'] == 'database-password')
        assert secret == {'secretName': 'control-runtime', 'optional': False, 'defaultMode': 0o440,
                          'items': [{'key': 'password', 'path': 'database-password'}]}
        assert ('--prometheus-token-file' in container['args']) is prometheus_auth
    kube = json.loads(configs(documents)[worker.KUBE_CONFIG]['kubernetes.json'])
    assert kube['token_file'] == '/credentials/observer-token'
    assert kube['ca_file'] == '/credentials/observer-ca.crt'
    assert kube['server'] == PARAMETERS['kubernetes_server']
    assert configs(documents)[worker.CONFIG]['SENTINELOPS_KUBERNETES_REMOTE_SERVER'] == kube['server']
    assert kube['target']['namespace'] == worker.NAMESPACE
    assert kube['deployment_uids'] == PARAMETERS['deployment_uids']


def test_init_copy_produces_owned_private_regular_files_accepted_by_source_policy(tmp_path):
    source, destination = tmp_path / 'secret', tmp_path / 'private'
    source.mkdir(); destination.mkdir()
    original = source / 'versioned-data'
    original.write_text('synthetic-credential')
    original.chmod(0o440)
    projected_secret = source / 'database-password'
    projected_secret.symlink_to(original)
    result = subprocess.run([sys.executable, '-c', worker.COPY_CREDENTIALS,
        str(destination), str(projected_secret)], capture_output=True, check=True)
    copied = destination / 'database-password'
    assert not copied.is_symlink() and stat.S_IMODE(copied.stat().st_mode) == 0o600
    assert _credential(copied) == 'synthetic-credential'
    assert not result.stdout and not result.stderr


def test_renderer_never_reads_credentials_or_requests_database_options(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline renderer must not read files or credentials')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(SourcePolicy, 'headers', forbidden)
    monkeypatch.setattr(SourcePolicy, 'database_options', forbidden)
    worker.render(IMAGE, **PARAMETERS, prometheus_secret='prometheus-auth', prometheus_token_key='token')


@pytest.mark.parametrize('change', [
    {'environment': 'aws-stage'}, {'health_stream': 'd' * 64},
    {'control_database': DATABASE.replace('control.', 'replacement.')},
    {'cluster_uid': 'another-cluster'}, {'namespace_uid': 'another-namespace'},
    {'deployment_uids': {s: s + '-replacement' for s in BUSINESS_ROUTES}},
    {'model_release_ref': 'artifact://manifests/' + 'd' * 64 + '.json'},
])
def test_public_config_hash_is_deterministic_and_rotation_changes_pod_template(change):
    parameters = {**PARAMETERS, 'model_claim': 'models', 'model_release_ref': RELEASE}
    original = worker.render(IMAGE, **parameters)
    rotated = worker.render(IMAGE, **{**parameters, **change})
    assert original == worker.render(IMAGE, **parameters)
    assert rotated == worker.render(IMAGE, **{**parameters, **change})
    data = configs(original)
    expected = hashlib.sha256(json.dumps({'runtime': data[worker.CONFIG], 'kubernetes': data[worker.KUBE_CONFIG]},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    for before, after in zip(deployments(original), deployments(rotated), strict=True):
        before, after = before['spec']['template'], after['spec']['template']
        assert before['spec'] == after['spec']
        assert before['metadata']['annotations']['sentinelops.io/config-sha256'] == expected
        assert before['metadata'] != after['metadata']


@pytest.mark.parametrize('values', [
    {'environment': 'local'}, {'environment': 'test'}, {'environment': None},
    {'control_database': None}, {'control_database': DATABASE.replace('postgresql:', 'postgres:')},
    {'control_database': DATABASE.replace('://', '://control_writer:secret@')},
    {'control_database': DATABASE.replace('verify-full', 'disable')},
    {'control_database': DATABASE.replace('/sentinelops_control', '/other')},
    {'control_database': DATABASE + '&password=secret'},
    {'database_ca_config': ''}, {'health_store_claim': None}, {'incident_store_claim': 'health-artifacts'},
    {'health_stream': None}, {'health_stream': 'not-a-stream'},
    {'kubernetes_server': 'http://eks.example.invalid:443'},
    {'kubernetes_server': 'https://eks.example.invalid'},
    {'kubernetes_server': 'https://user:secret@eks.example.invalid:443'},
    {'kubernetes_server': 'https://eks.example.invalid:443?token=secret'},
    {'kubernetes_server': 'https://eks.example.invalid:443#'},
    {'cluster_uid': None}, {'namespace_uid': ''}, {'deployment_uids': {}},
    {'deployment_uids': {s: None for s in BUSINESS_ROUTES}},
    {'observer_secret': 'control-runtime'}, {'observer_secret': ''},
    {'prometheus': 'http://prometheus.other.svc.cluster.local:9090'},
    {'prometheus': worker.PROMETHEUS.replace('http:', 'https:')},
    {'jaeger': 'http://127.0.0.1:16686'},
    {'prometheus_secret': 'prometheus-auth'}, {'prometheus_token_key': 'token'},
    {'prometheus_secret': 'control-runtime', 'prometheus_token_key': 'token'},
    {'prometheus_secret': 'prometheus-auth', 'prometheus_token_key': 'bad/key'},
    {'model_claim': 'models'}, {'model_release_ref': RELEASE},
    {'model_claim': 'models', 'model_release_ref': 'artifact://manifests/../model.json'},
    {'model_claim': 'health-artifacts', 'model_release_ref': RELEASE},
])
def test_incomplete_unsafe_or_mismatched_configuration_fails_closed(values):
    with pytest.raises(ValueError):
        worker.render(IMAGE, **{**PARAMETERS, **values})


@pytest.mark.parametrize('image', [None, 'repo/control-plane:latest', 'sha256:' + 'a' * 64])
def test_only_registry_digest_images_are_accepted(image):
    with pytest.raises(ValueError):
        worker.render(image, **PARAMETERS)


def cli_arguments(output, parameters=PARAMETERS):
    arguments = ['render_worker_manifests', '--image', IMAGE, '--output', str(output)]
    for key, value in parameters.items():
        if key == 'deployment_uids':
            arguments += [part for service, uid in value.items()
                          for part in ('--' + service.removesuffix('-service') + '-deployment-uid', uid)]
        else:
            arguments += ['--' + key.replace('_', '-'), value]
    return arguments


@pytest.mark.parametrize('metrics', [False, True])
def test_cli_yaml_roundtrip_is_deterministic_and_refuses_overwrite(tmp_path, monkeypatch, metrics):
    first, second = tmp_path / 'first.yaml', tmp_path / 'second.yaml'
    parameters = {**PARAMETERS, 'environment': 'staging'} if metrics else PARAMETERS
    extra = {'metrics_otlp_endpoint': 'http://127.0.0.1:4317',
             'health_metrics_instance': 'health-15', 'incident_metrics_instance': 'incidents-15'} if metrics else {}
    for output in (first, second):
        arguments = cli_arguments(output, parameters)
        for key, value in extra.items():
            arguments += ['--' + key.replace('_', '-'), value]
        monkeypatch.setattr(sys, 'argv', arguments)
        worker.main()
    assert first.read_bytes() == second.read_bytes()
    assert list(yaml.safe_load_all(first.read_text())) == worker.render(IMAGE, **parameters, **extra)
    with pytest.raises(FileExistsError):
        worker.main()


@pytest.mark.parametrize(('environment', 'endpoint'), [
    ('aws-dev', 'http://127.0.0.1:4317'), ('dev', 'http://otel-collector:4317'),
    ('staging', 'http://otel-collector:4317')])
def test_cli_invalid_metrics_does_not_create_output(tmp_path, monkeypatch, environment, endpoint):
    output = tmp_path / 'manifest.yaml'
    arguments = cli_arguments(output, {**PARAMETERS, 'environment': environment})
    monkeypatch.setattr(sys, 'argv', [*arguments, '--metrics-otlp-endpoint', endpoint])
    with pytest.raises(ValueError, match='worker metrics'):
        worker.main()
    assert not output.exists()


@pytest.mark.parametrize('invalid', ['missing', 'pair', 'unsafe'])
def test_cli_invalid_input_does_not_create_output(tmp_path, monkeypatch, invalid):
    output = tmp_path / 'manifest.yaml'
    arguments = cli_arguments(output)
    if invalid == 'missing':
        index = arguments.index('--observer-secret'); del arguments[index:index + 2]
    elif invalid == 'pair':
        arguments += ['--model-claim', 'models']
    else:
        arguments[arguments.index('--control-database') + 1] = DATABASE.replace('verify-full', 'disable')
    monkeypatch.setattr(sys, 'argv', arguments)
    with pytest.raises((ValueError, SystemExit)):
        worker.main()
    assert not output.exists()


def test_rendered_arguments_parse_in_real_worker_clis_and_select_kubernetes_model_branch(monkeypatch):
    documents = worker.render(IMAGE, **PARAMETERS, model_claim='models', model_release_ref=RELEASE)
    runtime = configs(documents)[worker.CONFIG]
    seen = []
    async def capture(args):
        seen.append(args)
    for deployment, cli in zip(deployments(documents), (run_health_worker, run_incident_worker), strict=True):
        container = deployment['spec']['template']['spec']['containers'][0]
        arguments = [runtime.get(a[2:-1], a) if a.startswith('$(') else a for a in container['args']]
        monkeypatch.setattr(cli, 'run', capture)
        monkeypatch.setattr(sys, 'argv', [container['command'][1], *arguments])
        cli.main()
    health, incident = seen
    assert health.source_profile == SOURCE_PROFILE_V2
    for args in seen:
        policy = SourcePolicy.from_args(args)
        assert policy.name == 'in-cluster' and policy.prometheus(args.prometheus) == worker.PROMETHEUS
        assert policy.headers() == {} and policy.control_database_user == 'control_writer'
        assert policy.control_database_password_file == Path('/credentials/database-password')
    assert health.store == incident.source_store == Path(worker.HEALTH_ROOT)
    assert incident.kubernetes_config == Path('/etc/sentinelops/kubernetes/kubernetes.json')
    assert incident.environment == health.environment == 'aws-dev'
    assert incident.model_store == Path('/models') and incident.model_release_ref == RELEASE
    assert incident.remote_jaeger == worker.JAEGER


def test_kubernetes_config_and_environment_select_the_same_explicit_remote_server(monkeypatch):
    from sentinelops.remediation.kubernetes import configured_server
    documents = worker.render(IMAGE, **PARAMETERS)
    data = configs(documents)
    server = json.loads(data[worker.KUBE_CONFIG]['kubernetes.json'])['server']
    monkeypatch.delenv('SENTINELOPS_KUBERNETES_REMOTE_SERVER', raising=False)
    with pytest.raises(ValueError, match='local HTTPS'):
        configured_server(server)
    monkeypatch.setenv('SENTINELOPS_KUBERNETES_REMOTE_SERVER',
                       data[worker.CONFIG]['SENTINELOPS_KUBERNETES_REMOTE_SERVER'])
    assert configured_server(server) == server


def kube_stub():
    return SimpleNamespace(target=TARGET, close=AsyncMock(), require_observer=AsyncMock(), observe=AsyncMock())


@pytest.mark.parametrize('environment,policy,pin', [
    ('aws-dev', None, None), ('local', SourcePolicy('in-cluster', worker.PROMETHEUS), worker.JAEGER),
    ('aws-dev', SourcePolicy('in-cluster', worker.PROMETHEUS), None),
    ('', SourcePolicy('in-cluster', worker.PROMETHEUS), worker.JAEGER),
])
def test_remote_evidence_requires_explicit_environment_policy_and_jaeger_pin(environment, policy, pin):
    with pytest.raises(ValueError):
        evidence.KubernetesIncidentEvidence(kube_stub(), PARAMETERS['deployment_uids'],
            prometheus_endpoint=worker.PROMETHEUS, jaeger_endpoint=worker.JAEGER,
            environment=environment, source_policy=policy, remote_jaeger_endpoint=pin)


@pytest.mark.parametrize('url', [None, worker.JAEGER + '?token=x', worker.JAEGER + '#',
    'http://jaeger.other.svc.cluster.local:16686', 'http://user:secret@jaeger:16686'])
def test_jaeger_pin_rejects_arbitrary_http_and_credential_bearing_urls(url):
    with pytest.raises(ValueError):
        evidence.remote_jaeger(url, url)


def test_https_prometheus_bearer_is_not_sent_to_jaeger_or_unpinned_hosts(tmp_path):
    token = tmp_path / 'token'; token.write_text('synthetic-token'); token.chmod(0o600)
    prometheus, jaeger = 'https://prometheus.example.invalid:9090', 'https://jaeger.example.invalid:16686'
    policy = SourcePolicy('remote', prometheus, prometheus_token_file=token)
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b'{}')
    async def run():
        source = evidence.KubernetesIncidentEvidence(kube_stub(), PARAMETERS['deployment_uids'],
            prometheus_endpoint=prometheus, jaeger_endpoint=jaeger, environment='aws-dev',
            source_policy=policy, remote_jaeger_endpoint=jaeger)
        await source.client.aclose()
        source.client = httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False)
        try:
            await source._http(prometheus + '/api/v1/status/config', limit=100)
            await source._http(jaeger + '/api/v3/traces/id', limit=100)
            with pytest.raises(ValueError, match='pinned sources'):
                await source._http('https://other.example.invalid:9090/api/v1/query', limit=100)
            with pytest.raises(ValueError, match='byte budget'):
                await source._http(prometheus + '/api/v1/status/config', limit=1)
            with pytest.raises(ValueError, match='accepted health source'):
                await source.verify_source({'environment': 'another-environment', 'endpoint_hash': stable_id({'endpoint': prometheus})})
            source.kube.observe.assert_not_awaited()
        finally:
            await source.close()
    asyncio.run(run())
    assert requests[0].headers['Authorization'] == 'Bearer synthetic-token'
    assert 'Authorization' not in requests[1].headers
    assert len(requests) == 3


def test_remote_incident_cli_requires_kubernetes_before_opening_a_database(monkeypatch):
    monkeypatch.setattr(SourcePolicy, 'database_options', lambda *args, **kwargs: {})
    pool = AsyncMock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr(run_incident_worker.asyncpg, 'create_pool', pool)
    with pytest.raises(ValueError, match='explicit Kubernetes config'):
        asyncio.run(run_incident_worker.run(SimpleNamespace(source_policy='in-cluster', environment='aws-dev')))
    pool.assert_not_awaited()


def test_nonlocal_v2_kubernetes_capture_and_replay_bind_environment_and_original_pod_identity(tmp_path, monkeypatch):
    from sentinelops.workers import incident_evidence
    now = END + timedelta(seconds=60)
    monkeypatch.setattr(evidence, 'now', lambda: now)
    monkeypatch.setattr(incident_evidence, 'now', lambda: now)
    store, api, pods = ArtifactStore(tmp_path), inventory(), projected()
    config = {'global': {'scrape_interval': '5s'}, 'scrape_configs': []}
    identity = {'environment': 'aws-dev', 'endpoint_hash': stable_id({'endpoint': worker.PROMETHEUS}),
        'source_profile': SOURCE_PROFILE_V2, 'prometheus_configuration_hash': source_configuration_hash(config)}
    raw_refs = []
    for service in BUSINESS_ROUTES:
        responses = {}
        for key, expression in queries(service, SOURCE_PROFILE_V2).items():
            if key in ('duration_sum', 'duration_count'):
                response = {'status': 'success', 'data': {'resultType': 'vector', 'result': [
                    {'metric': {}, 'value': [END.timestamp(), '12000' if key == 'duration_sum' else '120']}]}}
            else:
                response = source_response(key)
            responses[key] = {'expression': expression, 'response': response}
        raw_refs.append(store.write('raw', {'capture_version': '1.0', 'source_profile': SOURCE_PROFILE_V2,
            'service': service, 'bucket_end': timestamp(END),
            'collected_at': timestamp(END + timedelta(seconds=30)), 'queries': responses}))
    normalization = store.write('manifests', {'raw_refs': raw_refs})
    batch = store.write('manifests', {'identity': identity, 'stream_id': stable_id(identity),
        'bucket_end': timestamp(END), 'normalization_manifest_ref': normalization})
    state = store.write('manifests', {'last_batch_ref': batch})
    class FakeKube:
        target = TARGET
        def __init__(self):
            self.client = httpx.AsyncClient(base_url='https://kubernetes.default.svc:443',
                                            transport=httpx.MockTransport(self.log))
        async def require_observer(self):
            pass
        async def observe(self):
            pass
        async def close(self):
            await self.client.aclose()
        def log(self, request):
            assert request.method == 'GET' and request.url.path.endswith('/log')
            pod = next(p for p in pods.values() if '/' + p['pod_name'] + '/' in request.url.path)
            return httpx.Response(200, text=json.dumps(original_log(pod)))
        async def _request(self, method, path, **kwargs):
            assert method == 'GET'
            if '/deployments/' in path:
                return deepcopy(api['deployments'][path.rsplit('/', 1)[1]])
            key = 'replicasets' if path.endswith('/replicasets') else 'pods'
            return {'items': deepcopy(api[key][kwargs['params']['labelSelector'][4:]])}
    async def run():
        source = evidence.KubernetesIncidentEvidence(FakeKube(), PARAMETERS['deployment_uids'],
            prometheus_endpoint=worker.PROMETHEUS, jaeger_endpoint=worker.JAEGER,
            environment='aws-dev', source_policy=SourcePolicy('in-cluster', worker.PROMETHEUS),
            remote_jaeger_endpoint=worker.JAEGER)
        await source.client.aclose()
        def http(request):
            assert request.method == 'GET' and 'Authorization' not in request.headers
            if request.url.path.endswith('/api/v1/status/config'):
                return httpx.Response(200, json={'status': 'success', 'data': {'yaml': yaml.safe_dump(config)}})
            params = parse_qs(urlsplit(str(request.url)).query)
            service = next(s for s in pods if s in params['query'][0])
            return httpx.Response(200, json=pod_response(pods[service]))
        source.client = httpx.AsyncClient(transport=httpx.MockTransport(http))
        try:
            reference = await source.capture(store, source_state_ref=state, job_id=stable_id('job'), intent_ref=state,
                group={'first_onset_at': timestamp(END - timedelta(seconds=60))}, policy=RCA_POLICY,
                health_identity=identity, latency_ms=200.)
            capture = store.read(reference)
            assert capture['actions_allowed'] is False
            assert capture['profile']['environment'] == 'aws-dev'
            assert evidence.verify_kubernetes_binding(store, capture, identity) == pods
            mismatched = deepcopy(capture)
            mismatched['profile']['environment'] = 'aws-other'
            with pytest.raises(ValueError, match='profile/source binding'):
                evidence.verify_kubernetes_binding(store, mismatched, identity)
        finally:
            await source.close()
    asyncio.run(run())
