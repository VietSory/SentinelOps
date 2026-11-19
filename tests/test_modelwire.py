"""Model delivery must preserve immutable evidence and fail before PVC writes."""
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import zipfile

import pytest

from scripts.aws_demo import modelwire as wire


def pack(store, archive):
    with tarfile.open(archive, 'w:gz') as stream:
        for path in sorted(store.rglob('*')):
            if path.is_file():
                stream.add(path, arcname=path.relative_to(store), recursive=False)
    return archive


@pytest.fixture
def released(tmp_path):
    pytest.importorskip('sklearn')
    pytest.importorskip('xgboost')
    from tests.test_alibaba_release import evidence
    from sentinelops.ml.alibaba_release import build_alibaba_release

    sha, manifest, _, controls, record, verification = evidence(tmp_path)
    store = tmp_path / 'trusted'
    receipt = build_alibaba_release(tmp_path / 'bundle', sha, controls=controls, parity_record=record,
        parity_verification=verification, approved_by='synthetic-test-only',
        decision_note='Synthetic fixture, never production authority', output=store)
    return SimpleNamespace(store=store, ref=receipt['release_ref'],
        pins={'model_version': manifest['model_version'], 'manifest_sha256': sha},
        archive=pack(store, tmp_path / 'release.tar.gz'))


def test_real_registry_round_trip_and_idempotent_pvc_retry(released, tmp_path):
    from sentinelops.ml.registry import ModelRegistry
    from sentinelops.telemetry.store import ArtifactStore

    target = tmp_path / 'pvc'
    for _ in range(2):
        receipt = wire.install(released.archive, wire.digest(released.archive), target, released.ref, **released.pins)
        assert receipt['registry_available'] and receipt['files'] == 10
        assert not receipt['activation_performed']
    registry = ModelRegistry.load(ArtifactStore(target), released.ref)
    assert registry.available and registry.identity == released.ref
    assert {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()} == {
        p.relative_to(released.store): p.read_bytes() for p in released.store.rglob('*') if p.is_file()}


def test_archive_checksum_precedes_extraction_and_registry(tmp_path, monkeypatch):
    archive = tmp_path / 'invalid.tar'
    archive.write_bytes(b'not even a tar')
    monkeypatch.setattr(wire, 'verify_store', lambda *a, **k: pytest.fail('must not deserialize'))
    with pytest.raises(ValueError, match='archive checksum'):
        wire.install(archive, 'a' * 64, tmp_path / 'pvc', 'artifact://manifests/' + 'b' * 64 + '.json')
    assert not (tmp_path / 'pvc').exists()


@pytest.mark.parametrize('change', ['weight', 'evidence', 'manifest', 'approval-missing', 'extra', 'wrong-model'])
def test_corrupt_or_incomplete_graph_cannot_populate_pvc(released, tmp_path, change):
    if change == 'weight':
        (released.store / 'alibaba-v2022/detector.joblib').write_bytes(b'tampered')
    elif change == 'manifest':
        (released.store / 'alibaba-v2022/manifest.json').write_bytes(b'{}')
    elif change in ('evidence', 'approval-missing'):
        release = json.loads((released.store / wire.release_path(released.ref)).read_bytes())
        approval_path = released.store / wire.release_path(release['models'][0]['validation_ref'])
        if change == 'approval-missing':
            approval_path.unlink()
        else:
            approval = json.loads(approval_path.read_bytes())
            (released.store / wire.release_path(approval['evaluation_refs'][0])).write_bytes(b'{}')
    elif change == 'extra':
        (released.store / 'manifests/unreferenced.json').write_bytes(b'{}')
    else:
        released.pins['model_version'] = 'f' * 64
    pack(released.store, released.archive)
    with pytest.raises((ValueError, FileNotFoundError)):
        wire.install(released.archive, wire.digest(released.archive), tmp_path / 'pvc', released.ref, **released.pins)
    assert not (tmp_path / 'pvc').exists()


def test_runtime_library_mismatch_cannot_populate_pvc(released, tmp_path, monkeypatch):
    import sentinelops.ml.models as models
    monkeypatch.setattr(models.importlib.metadata, 'version', lambda name: 'wrong-runtime')
    with pytest.raises(ValueError, match='library version'):
        wire.install(released.archive, wire.digest(released.archive), tmp_path / 'pvc', released.ref, **released.pins)
    assert not (tmp_path / 'pvc').exists()


def test_built_wheel_loads_registry_without_source_checkout(released, tmp_path):
    """Reproduce the actual image failure: config used to exist only in source."""
    root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / 'build-checkout'
    checkout.mkdir()
    for name in ('pyproject.toml', 'README.md', 'setup.py', 'MANIFEST.in'):
        shutil.copyfile(root / name, checkout / name)
    for name in ('src', 'contracts', 'configs'):
        shutil.copytree(root / name, checkout / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    build = subprocess.run([sys.executable, 'setup.py', 'bdist_wheel'], cwd=checkout,
                           capture_output=True, text=True, timeout=120)
    assert build.returncode == 0, build.stdout + build.stderr
    installed = tmp_path / 'installed'
    with zipfile.ZipFile(next((checkout / 'dist').glob('*.whl'))) as wheel:
        wheel.extractall(installed)
    packaged_config = installed / 'sentinelops/ml/configs/alibaba_relative_features.json'
    assert packaged_config.read_bytes() == (root / 'configs/alibaba_relative_features.json').read_bytes()
    isolated = tmp_path / 'isolated'
    isolated.mkdir()
    code = '''import sys
from pathlib import Path
from sentinelops.ml import alibaba_training
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.store import ArtifactStore
assert Path(alibaba_training.__file__).is_relative_to(Path(sys.argv[1]))
assert not alibaba_training.CONFIG.exists()
assert alibaba_training.load_config()['feature_set_version'] == 'http-relative-5m/v2'
registry = ModelRegistry.load(ArtifactStore(sys.argv[2]), sys.argv[3])
assert registry.available and registry.is_alibaba
print('installed registry available')
'''
    result = subprocess.run([sys.executable, '-c', code, str(installed), str(released.store), released.ref],
        cwd=isolated, env={**os.environ, 'PYTHONPATH': str(installed)},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'installed registry available' in result.stdout


def test_existing_pvc_collision_preserves_bytes(released, tmp_path):
    target = tmp_path / 'pvc'
    path = target / 'alibaba-v2022/risk.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'previous release')
    with pytest.raises(ValueError, match='collision'):
        wire.install(released.archive, wire.digest(released.archive), target, released.ref, **released.pins)
    assert path.read_bytes() == b'previous release'
    assert not (target / 'manifests').exists()


@pytest.mark.parametrize('kind', ['traversal', 'absolute', 'symlink', 'hardlink', 'fifo', 'duplicate', 'oversize'])
def test_unsafe_tar_rejected(tmp_path, kind):
    archive = tmp_path / 'unsafe.tar'
    member = tarfile.TarInfo('manifests/test.json')
    if kind == 'traversal':
        member.name = 'manifests/../../outside'
    elif kind == 'absolute':
        member.name = '/outside'
    elif kind in ('symlink', 'hardlink'):
        member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
        member.linkname = '/outside'
    elif kind == 'fifo':
        member.type = tarfile.FIFOTYPE
    elif kind == 'oversize':
        member.size = wire.MAX_EXPANDED + 1
    with tarfile.open(archive, 'w') as stream:
        stream.addfile(member)
        if kind == 'duplicate':
            stream.addfile(member)
    with pytest.raises(ValueError):
        wire.extract(archive, tmp_path / 'extract')
    assert not (tmp_path / 'outside').exists()


def test_symlink_pvc_subdirectory_is_rejected(released, tmp_path):
    target, outside = tmp_path / 'pvc', tmp_path / 'outside'
    target.mkdir()
    outside.mkdir()
    (target / 'alibaba-v2022').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        wire.install(released.archive, wire.digest(released.archive), target, released.ref, **released.pins)
    assert list(outside.iterdir()) == []


def test_local_receive_and_size_bound(tmp_path, monkeypatch):
    path = tmp_path / 'bundle.tar.gz'
    wire.receive(path, BytesIO(b'archive bytes'))
    assert path.read_bytes() == b'archive bytes' and not path.with_suffix('.part').exists()
    with pytest.raises(FileExistsError):
        wire.receive(path, BytesIO(b'other'))
    monkeypatch.setattr(wire, 'MAX_ARCHIVE', 2)
    with pytest.raises(ValueError, match='exceeds'):
        wire.receive(tmp_path / 'too-big.tar.gz', BytesIO(b'abc'))
    assert not (tmp_path / 'too-big.tar.gz').exists()


def rendering(**changes):
    values = dict(name='modelwire-test', namespace='sentinelops-b-actions', claim='model-de38fc6b',
        service_account='default', image='example/control@sha256:' + 'a' * 64, source='local',
        expected_sha256='b' * 64, release_ref='artifact://manifests/' + 'c' * 64 + '.json',
        model_version=wire.MODEL_VERSION, manifest_sha256=wire.MANIFEST_SHA256,
        storage_class=None, aws_cli_image=None, bucket=None, key=None, version_id=None,
        account_id=None, region='ap-southeast-1')
    values.update(changes)
    return SimpleNamespace(**values)


def test_local_job_is_nonroot_credential_free_and_embeds_current_script():
    docs = wire.render(rendering(storage_class='reviewed-efs'))
    pvc, config, job = docs
    assert pvc['spec']['accessModes'] == ['ReadWriteMany']
    assert config['data']['modelwire.py'] == Path(wire.__file__).read_text()
    pod = job['spec']['template']['spec']
    assert pod['securityContext']['runAsUser'] == pod['securityContext']['fsGroup'] == 10001
    assert pod['automountServiceAccountToken'] is False and 'initContainers' not in pod
    assert '--wait-seconds' in pod['containers'][0]['command']
    assert job['spec']['backoffLimit'] == 0
    assert not any(d['kind'] in ('Secret', 'Role', 'RoleBinding') for d in docs)


def test_cli_renders_executable_configmap_and_refuses_output_overwrite(tmp_path):
    import yaml
    output = tmp_path / 'job.yaml'
    args = rendering()
    command = [sys.executable, wire.__file__, 'render', '--source', 'local', '--image', args.image,
               '--release-ref', args.release_ref, '--expected-sha256', args.expected_sha256,
               '--output', str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    docs = list(yaml.safe_load_all(output.read_text()))
    code = docs[0]['data']['modelwire.py']
    compile(code, '<mounted-configmap>', 'exec')
    assert docs[-1]['kind'] == 'Job'
    assert subprocess.run(command, capture_output=True, timeout=30).returncode != 0


def test_s3_job_pins_version_owner_and_checksum_transport():
    docs = wire.render(rendering(source='s3', aws_cli_image='example/aws@sha256:' + 'd' * 64,
        bucket='example-artifacts', key='models/de38fc6b/hash/bundle.tar.gz', version_id='exact-version',
        account_id='123456789012', service_account='model-reader'))
    pod = docs[-1]['spec']['template']['spec']
    download = pod['initContainers'][0]
    assert '--version-id' in download['args'] and 'exact-version' in download['args']
    assert '--expected-bucket-owner' in download['args'] and '--checksum-mode' in download['args']
    assert pod['serviceAccountName'] == 'model-reader'
    assert download['volumeMounts'] == pod['containers'][0]['volumeMounts'][:2]
    assert '--wait-seconds' not in pod['containers'][0]['command']


@pytest.mark.parametrize('changes', [dict(image='example:latest'), dict(expected_sha256='de38fc6b'),
    dict(release_ref='s3://bucket/models/release.json'), dict(claim='../bad'), dict(source='s3'),
    dict(source='s3', aws_cli_image='example/aws@sha256:' + 'd' * 64, bucket='example-artifacts',
         key='other/bundle', version_id='null', account_id='123456789012')])
def test_invalid_render_input_rejected(changes):
    with pytest.raises(ValueError):
        wire.render(rendering(**changes))


def test_wrapper_forwards_paired_model_selection_to_both_consumers():
    from tests.test_aws_demo_render import inputs
    from scripts.aws_demo import render_all
    images, terraform, runtime = inputs()
    runtime.update(model_claim='model-de38fc6b', model_release_ref=rendering().release_ref)
    docs = render_all.render(images, terraform, runtime)['40-workers.yaml']
    deployments = {d['metadata']['name']: d for d in docs if d['kind'] == 'Deployment'}
    for name in ('sentinelops-incident-worker', 'remediation-controller'):
        pod = deployments[name]['spec']['template']['spec']
        assert '--model-release-ref' in pod['containers'][0]['args']
        assert {'name': 'models', 'mountPath': '/models', 'readOnly': True} in pod['containers'][0]['volumeMounts']
        assert {'name': 'models', 'persistentVolumeClaim': {'claimName': 'model-de38fc6b', 'readOnly': True}} in pod['volumes']
    assert '--model-store' not in deployments['sentinelops-health-worker']['spec']['template']['spec']['containers'][0]['args']
    runtime.pop('model_release_ref')
    with pytest.raises(ValueError):
        render_all.render(images, terraform, runtime)


def test_new_epoch_root_updates_all_readers_and_probes_without_moving_mounts():
    from tests.test_aws_demo_render import inputs
    from scripts.aws_demo import render_all
    original = render_all.render(*inputs())['40-workers.yaml']
    root = '/artifacts/health/model-de38fc6b-20261118'
    docs = wire.health_epoch(original, root)
    for doc in docs:
        if doc['kind'] != 'Deployment':
            continue
        container = doc['spec']['template']['spec']['containers'][0]
        flag = '--store' if doc['metadata']['name'] == 'sentinelops-health-worker' else '--source-store'
        assert container['args'][container['args'].index(flag) + 1] == root
        assert any(m['mountPath'] == '/artifacts/health' for m in container['volumeMounts'])
        for probe in ('readinessProbe', 'livenessProbe'):
            if probe in container:
                assert root in container[probe]['exec']['command']
    health = wire.health_epoch(original, root, health_only=True)
    assert {d['metadata']['name'] for d in health} == {
        'sentinelops-health-worker', 'health-worker', 'sentinelops-workers-runtime'}
    assert root not in json.dumps(original)
    with pytest.raises(ValueError):
        wire.health_epoch(original, '/artifacts/health/../elsewhere')


def test_rwo_claim_is_pinned_to_the_consumer_node():
    docs = wire.render(rendering(storage_class='gp2', access_mode='ReadWriteOnce',
                                 colocate_with='remediation-controller'))
    pvc, _, job = docs
    assert pvc['spec']['accessModes'] == ['ReadWriteOnce']
    term, = job['spec']['template']['spec']['affinity']['podAffinity']['requiredDuringSchedulingIgnoredDuringExecution']
    assert term == {'labelSelector': {'matchLabels': {'app': 'remediation-controller'}},
                    'topologyKey': 'kubernetes.io/hostname'}


@pytest.mark.parametrize('changes', [
    dict(storage_class='gp2', access_mode='ReadWriteOnce'),
    dict(access_mode='ReadWriteOnce', colocate_with='remediation-controller'),
    dict(storage_class='efs', colocate_with='remediation-controller'),
    dict(storage_class='gp2', access_mode='ReadWriteOnce', colocate_with='Bad_Label')])
def test_rwo_requires_a_class_and_a_valid_colocation(changes):
    with pytest.raises(ValueError):
        wire.render(rendering(**changes))


def test_wrapper_forwards_policy_and_controller_trusts_rds_ca():
    from tests.test_aws_demo_render import inputs
    from scripts.aws_demo import render_all
    images, terraform, runtime = inputs()
    runtime.update(model_claim='model-de38fc6b', model_release_ref=rendering().release_ref, policy_enabled=True)
    docs = render_all.render(images, terraform, runtime)['40-workers.yaml']
    controller = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'remediation-controller')
    container = controller['spec']['template']['spec']['containers'][0]
    assert '--policy-enabled' in container['args']
    assert {'name': 'PGSSLROOTCERT', 'value': '/etc/sentinelops/tls/ca-bundle.crt'} in container['env']
    runtime.pop('policy_enabled')
    docs = render_all.render(images, terraform, runtime)['40-workers.yaml']
    controller = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'remediation-controller')
    assert '--policy-enabled' not in controller['spec']['template']['spec']['containers'][0]['args']


def test_new_epoch_root_skips_the_demo_ui_sidecar():
    # --demo-ui adds an operator-ui container with a command and HTTP probes only;
    # the epoch rewrite must leave it alone and still move every health reader.
    from scripts import render_worker_manifests as worker
    from tests.test_worker_manifests import IMAGE, PARAMETERS
    original = worker.render(IMAGE, **PARAMETERS, demo_ui=True)
    controller = next(d for d in original if d['kind'] == 'Deployment' and d['metadata']['name'] == 'remediation-controller')
    assert len(controller['spec']['template']['spec']['containers']) == 2
    root = '/artifacts/health/model-de38fc6b-20261119'
    docs = wire.health_epoch(original, root)
    controller = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'remediation-controller')
    main, sidecar = controller['spec']['template']['spec']['containers']
    assert main['args'][main['args'].index('--source-store') + 1] == root
    assert root not in json.dumps(sidecar)
