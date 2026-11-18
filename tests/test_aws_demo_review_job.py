"""Offline review Job wiring and bounded collection; no cloud or approval calls."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from scripts import review_payment_image as cli
from scripts.aws_demo import review_job as renderer
from sentinelops.remediation.domain import Blocked
from sentinelops.telemetry.core import stable_id
from test_payment_image_review import setup


def inputs():
    folder = renderer.ROOT / 'scripts/aws_demo'
    return (json.loads((folder / 'k8s-images.example.json').read_text()),
            json.loads((folder / 'k8s-runtime.example.json').read_text()))


def test_job_mounts_exact_script_private_credentials_and_rwo_stores():
    images, runtime = inputs()
    runtime['health_stream'] = renderer.HEALTH_STREAM
    documents = renderer.render(images, runtime, health_node='ip-10-0-1-23')
    config, job = list(yaml.safe_load_all(yaml.safe_dump_all(documents)))
    assert [d['kind'] for d in documents] == ['ConfigMap', 'Job']
    assert all(d['metadata']['name'] == 'review' and d['metadata']['namespace'] == renderer.NAMESPACE
               for d in documents)
    assert config['data']['review_payment_image.py'] == (renderer.ROOT / 'scripts/review_payment_image.py').read_text()
    target = json.loads(config['data']['target.json'])
    assert target == {'cluster_uid': runtime['cluster_uid'], 'namespace': renderer.NAMESPACE,
        'namespace_uid': runtime['namespace_uid'], 'deployment': 'payment-service',
        'deployment_uid': runtime['deployment_uids']['payment-service'], 'container': 'payment'}
    assert job['spec']['backoffLimit'] == 0
    assert job['spec']['activeDeadlineSeconds'] == 660
    assert job['spec']['completions'] == job['spec']['parallelism'] == 1
    pod = job['spec']['template']['spec']
    assert pod['restartPolicy'] == 'Never'
    assert pod['serviceAccountName'] == 'payment-rollback'
    assert pod['automountServiceAccountToken'] is False
    assert pod['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution'] == {
        'nodeSelectorTerms': [{'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In',
                                                    'values': ['ip-10-0-1-23']}]}]}
    volumes = {v['name']: v for v in pod['volumes']}
    assert volumes['service-account']['projected'] == {'defaultMode': 0o440, 'sources': [
        {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 86400}},
        {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}
    assert volumes['database-password']['secret']['secretName'] == 'control-runtime'
    assert volumes['database-password']['secret']['items'] == [{'key': 'password', 'path': 'database-password'}]
    assert volumes['database-ca']['configMap']['name'] == 'control-rds-ca'
    for name, readonly in [('health-artifacts', True), ('review-artifacts', False)]:
        assert volumes[name]['persistentVolumeClaim'] == {'claimName': name, 'readOnly': readonly}
    init = pod['initContainers'][0]
    assert init['command'] == ['python', '-c', renderer.COPY_CREDENTIALS]
    assert init['args'] == ['/credentials', '/service-account/token', '/service-account/ca.crt',
                            '/secret-database/database-password']
    container = pod['containers'][0]
    assert container['image'] == images['control-plane']
    assert container['command'] == ['python', '-u', '/review/review_payment_image.py']
    assert container['args'][-1] == 'collect'
    assert renderer.HEALTH_STREAM in container['args']
    mounts = {m['name']: m for m in container['volumeMounts']}
    assert mounts['health-artifacts']['readOnly'] is True
    assert not mounts['review-artifacts'].get('readOnly', False)
    assert not {'database-password', 'service-account'} & mounts.keys()
    env = {e['name']: e for e in container['env']}
    assert env['SENTINELOPS_IMAGE_REVIEW_DATABASE_URL']['valueFrom']['configMapKeyRef'] == {
        'name': 'sentinelops-workers-runtime', 'key': 'SENTINELOPS_CONTROL_DATABASE', 'optional': False}
    assert env['SSL_CERT_FILE']['value'] == '/etc/sentinelops/tls/ca-bundle.crt'
    assert pod['securityContext']['runAsUser'] == pod['securityContext']['fsGroup'] == 10001
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert not any(d['kind'] in ('Secret', 'Role', 'RoleBinding', 'ServiceAccount') for d in documents)


@pytest.mark.parametrize('problem', ['tag', 'node', 'uid', 'stream', 'same-pvc', 'approve', 'ref', 'missing-ref'])
def test_invalid_job_inputs_fail_before_output(problem):
    images, runtime = deepcopy(inputs())
    options = dict(health_node='ip-10-0-1-23')
    if problem == 'tag': images['control-plane'] = 'control:latest'
    if problem == 'node': options['health_node'] = ''
    if problem == 'uid': runtime['cluster_uid'] = ''
    if problem == 'stream': runtime['health_stream'] = 'not-accepted'
    if problem == 'same-pvc': runtime['known_good_store_claim'] = runtime['health_store_claim']
    if problem == 'approve': options['operation'] = 'approve_known_good'
    if problem == 'ref': options.update(operation='complete', review_ref='../../not-a-manifest')
    if problem == 'missing-ref': options['operation'] = 'complete'
    with pytest.raises((ValueError, Blocked)):
        renderer.render(images, runtime, **options)


def test_uid_only_defaults_and_manual_open_complete():
    images, runtime = inputs()
    uids = {key: runtime[key] for key in ('cluster_uid', 'namespace_uid', 'deployment_uids')}
    ref = 'artifact://manifests/' + 'a' * 64 + '.json'
    docs = renderer.render(images, uids, health_node='node-a', operation='complete', review_ref=ref)
    args = docs[1]['spec']['template']['spec']['containers'][0]['args']
    assert args[-3:] == ['complete', '--review-ref', ref]
    assert renderer.HEALTH_STREAM in args
    docs = renderer.render(images, uids, health_node='node-a', operation='open')
    assert docs[1]['spec']['template']['spec']['containers'][0]['args'][-1] == 'open'


def test_collection_produces_only_unapproved_evidence_from_real_review(tmp_path):
    async def run():
        review, _, _, clock = setup(tmp_path)
        outputs = []
        async def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)
        result = await cli.collect(review, emit=outputs.append, sleep=sleep)
        assert outputs[0]['status'] == 'REVIEW_OPENED'
        assert result['status'] == 'AWAITING_OPERATOR_REVIEW'
        assert result['evidence']['known_good_approved'] is False
        assert result['evidence']['physical_binding_verified'] is False
        assert result['evidence']['action_dispatch_authorized'] is False
        assert all('approved-payment-images/v1' not in p.read_text()
                   for p in (review.store.root / 'manifests').glob('*.json'))
    asyncio.run(run())


def test_collection_timeout_is_bounded_and_keeps_one_open_reference():
    async def run():
        review = SimpleNamespace(open=AsyncMock(return_value={'review_ref': 'one-review'}),
            complete=AsyncMock(return_value={'status': 'PENDING'}))
        sleep = AsyncMock()
        with pytest.raises(Blocked, match='timeout'):
            await cli.collect(review, emit=lambda value: None, sleep=sleep)
        review.open.assert_awaited_once()
        assert review.complete.await_count == 39
        assert all(call.args == ('one-review',) for call in review.complete.await_args_list)
        assert sum(call.args[0] for call in sleep.await_args_list) == 570
    asyncio.run(run())


def test_worker_observer_checks_scope_and_exposes_no_patch():
    async def run():
        async def request(method, path, **kwargs):
            assert method == 'POST' and path.endswith('/selfsubjectaccessreviews')
            attributes = kwargs['json']['spec']['resourceAttributes']
            allowed = attributes.get('name') == 'payment-service' and attributes['verb'] in ('get', 'patch')
            return {'status': {'allowed': allowed}}
        kube = SimpleNamespace(target=SimpleNamespace(namespace=renderer.NAMESPACE, deployment='payment-service'),
                               _request=AsyncMock(side_effect=request), observe=AsyncMock(return_value='observed'))
        observer = cli.WorkerObserver(kube)
        await observer.require_observer()
        assert await observer.observe() == 'observed'
        assert not hasattr(observer, 'patch')
        kube._request = AsyncMock(return_value={'status': {'allowed': True}})
        with pytest.raises(Blocked, match='scope-mismatch'):
            await observer.require_observer()
    asyncio.run(run())


@pytest.mark.parametrize('valid', [True, False, None])
def test_worker_database_scope_fails_closed(valid):
    async def run():
        query = AsyncMock(return_value=valid)
        class Connection:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            fetchval = query
        pool = SimpleNamespace(acquire=Connection)
        if valid is True:
            await cli.require_worker_reader(pool)
        else:
            with pytest.raises(Blocked, match='database-scope-mismatch'):
                await cli.require_worker_reader(pool)
        sql = query.await_args.args[0]
        assert "current_setting('default_transaction_read_only')='on'" in sql
        assert "current_user='control_writer'" in sql
    asyncio.run(run())


@pytest.mark.parametrize('binding', ['valid', 'wrong-environment', 'wrong-root'])
def test_in_cluster_run_uses_readonly_db_exact_environment_and_closes_resources(tmp_path, monkeypatch, binding):
    async def run():
        original, source, _, _ = setup(tmp_path)
        args = SimpleNamespace(in_cluster_worker=True, server=renderer.SERVER,
            health_stream='a' * 64, source_store=source.store.root, review_store=original.store.root,
            target=tmp_path / 'target.json', health_policy=tmp_path / 'policy.json',
            token_file=Path('/credentials/token'), ca_file=Path('/credentials/ca.crt'), operation='open')
        args.target.write_text(json.dumps(asdict(original.target)))
        args.health_policy.write_text(json.dumps(source.policy))
        monkeypatch.setenv('SENTINELOPS_KUBERNETES_REMOTE_SERVER', renderer.SERVER)
        monkeypatch.setenv('SENTINELOPS_ENV', 'dev')
        dsn = 'postgresql://rds.example.invalid:5432/sentinelops_control?sslmode=verify-full'
        monkeypatch.setenv('SENTINELOPS_IMAGE_REVIEW_DATABASE_URL', dsn)
        class Connection:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def fetchrow(self, *args): return {'stream': 'fixture'}
        pool = SimpleNamespace(acquire=Connection, close=AsyncMock())
        create_pool = AsyncMock(return_value=pool)
        monkeypatch.setattr(cli.asyncpg, 'create_pool', create_pool)
        options = {'user': 'control_writer', 'password': 'test-only-password'}
        monkeypatch.setattr(cli.SourcePolicy, 'database_options', lambda self, *args, **kwargs: options)
        worker_check = AsyncMock()
        monkeypatch.setattr(cli, 'require_worker_reader', worker_check)
        monkeypatch.setattr(cli, 'require_evidence_reader', AsyncMock(side_effect=AssertionError('local reader only')))
        monkeypatch.setattr(cli, 'validate_stream', lambda *args: {
            'environment': 'local' if binding == 'wrong-environment' else 'dev',
            'artifact_root_hash': 'f' * 64 if binding == 'wrong-root' else stable_id({'root': str(source.store.root.resolve())})})
        monkeypatch.setattr(cli, 'HealthLedgerReader', lambda *args: source)
        kube = SimpleNamespace(close=AsyncMock())
        monkeypatch.setattr(cli, 'LocalKubernetes', lambda *args, **kwargs: kube)
        constructed = []
        def review(*args, **kwargs):
            constructed.append((args, kwargs))
            return SimpleNamespace(open=AsyncMock(return_value={'status': 'REVIEW_OPENED'}))
        monkeypatch.setattr(cli, 'ImageEvidenceReview', review)
        if binding == 'valid':
            await cli.run(args)
        else:
            with pytest.raises(Blocked, match='source-root-or-environment-mismatch'):
                await cli.run(args)
        create_pool.assert_awaited_once_with(dsn, min_size=0, max_size=2, command_timeout=5, timeout=5,
            server_settings={'default_transaction_read_only': 'on'}, **options)
        worker_check.assert_awaited_once_with(pool)
        if binding == 'valid':
            assert constructed[0][1] == {'environment': 'dev'}
            assert isinstance(constructed[0][0][1], cli.WorkerObserver)
            kube.close.assert_awaited_once()
        else:
            assert not constructed
            kube.close.assert_not_awaited()
        pool.close.assert_awaited_once()
    asyncio.run(run())


def test_renderer_cli_writes_once_and_rejects_uid_conflict(tmp_path, monkeypatch):
    images, runtime = inputs()
    for name, value in [('images', images), ('runtime', runtime), ('uids', {'cluster_uid': 'different-cluster'})]:
        (tmp_path / (name + '.json')).write_text(json.dumps(value))
    argv = ['review_job', str(tmp_path / 'images.json'), str(tmp_path / 'runtime.json'),
            '--health-node', 'node-a', '--output', str(tmp_path / 'review.yaml')]
    monkeypatch.setattr('sys.argv', argv)
    renderer.main()
    assert len(list(yaml.safe_load_all((tmp_path / 'review.yaml').read_text()))) == 2
    with pytest.raises(FileExistsError): renderer.main()
    monkeypatch.setattr('sys.argv', [*argv, '--uids', str(tmp_path / 'uids.json')])
    with pytest.raises(SystemExit): renderer.main()
