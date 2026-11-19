"""Copy an owner-approved release store to a model PVC; never build approvals.

The archive is a tar/tar.gz with alibaba-v2022/ and manifests/ at its root.
Use the SAME digest-pinned control-plane image as the consuming workers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
import time

MODEL_VERSION = 'de38fc6bb89896856556e13e7c6142ba21c8d0fea88ebd082f95e96d4e5b0e57'
MANIFEST_SHA256 = 'd52df5846ac5027507d96b67c751f02f2c5519e3ea970fbdc3599207d18eb84e'
MAX_ARCHIVE = 1024 ** 3
MAX_EXPANDED = 2 * MAX_ARCHIVE
REF = re.compile(r'artifact://manifests/([0-9a-f]{64})\.json')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def checksum(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('a full lowercase SHA-256 from the release owner is required')
    return value


def release_path(ref):
    match = REF.fullmatch(ref)
    if not match:
        raise ValueError('release/evidence ref must be artifact://manifests/<SHA256>.json')
    return Path('manifests') / (match[1] + '.json')


def regular(root, relative):
    """Reject links in every component, including existing PVC directories."""
    path = Path(root)
    if path.is_symlink():
        raise ValueError('symlink in store path')
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('symlink in store path')
    if not path.is_file():
        raise ValueError('missing regular store file: ' + str(relative))
    return path


def verify_store(root, ref, *, model_version=MODEL_VERSION, manifest_sha256=MANIFEST_SHA256):
    """Check the complete graph before the trusted registry deserializes joblib."""
    from sentinelops.telemetry.store import ArtifactStore
    from sentinelops.ml.registry import ModelRegistry

    root = Path(root)
    store = ArtifactStore(root)
    files = set()

    def read(reference):
        relative = release_path(reference)
        regular(root, relative)
        files.add(relative)
        return store.read(reference)

    release = read(ref)
    if release.get('release_version') != 'validated-model-release/v1' or len(release.get('models', [])) != 1:
        raise ValueError('one shared validated Alibaba release is required')
    row = release['models'][0]
    if row.get('bundle_directory') != 'alibaba-v2022' or row.get('manifest_sha256') != checksum(manifest_sha256):
        raise ValueError('bundle directory or independently pinned manifest differs')
    bundle = Path('alibaba-v2022')
    path = regular(root, bundle / 'manifest.json')
    if digest(path) != manifest_sha256:
        raise ValueError('bundle manifest checksum mismatch')
    manifest = json.loads(path.read_bytes())
    if manifest.get('model_version') != checksum(model_version) or release.get('candidate_id') != model_version:
        raise ValueError('release is for a different model version')
    if set(manifest.get('artifacts', {})) != {'detector.joblib', 'risk.json'}:
        raise ValueError('de38fc6b requires detector.joblib and risk.json')
    pins = {'manifest.json': manifest_sha256, 'report.json': manifest['provenance']['report_sha256'],
            **manifest['artifacts']}
    for name, sha in pins.items():
        relative = bundle / name
        if digest(regular(root, relative)) != checksum(sha):
            raise ValueError('bundle file checksum mismatch: ' + name)
        files.add(relative)
    approval = read(row['validation_ref'])
    for reference in approval.get('evaluation_refs', []):
        read(reference)
    # No approval is invented here: the production loader checks G2/G3/parity,
    # frozen config, report, library versions and loaded estimator metadata.
    registry = ModelRegistry.load(store, ref)
    if not registry.available or not registry.is_alibaba or registry.identity != ref:
        raise ValueError('registry is unavailable or not the selected Alibaba release')
    return files


def extract(archive, destination):
    """Bounded manual extraction: no links, special files, duplicates or traversal."""
    destination = Path(destination)
    seen, total = set(), 0
    with tarfile.open(archive, 'r:*') as source:
        for count, member in enumerate(source, 1):
            name = PurePosixPath(member.name)
            if (count > 1024 or not name.parts or name.is_absolute() or '..' in name.parts
                    or '\\' in member.name or ':' in member.name
                    or name.parts[0] not in ('alibaba-v2022', 'manifests')
                    or not (member.isdir() or member.isfile()) or member.name in seen
                    or getattr(member, 'sparse', None)):
                raise ValueError('unsafe or unsupported archive entry: ' + member.name)
            seen.add(member.name)
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += member.size
            if member.size < 0 or total > MAX_EXPANDED:
                raise ValueError('expanded archive exceeds 2 GiB')
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            target.chmod(0o644)


def install(archive, expected_sha256, destination, ref, *, model_version=MODEL_VERSION,
            manifest_sha256=MANIFEST_SHA256):
    archive, destination = Path(archive), Path(destination)
    checksum(expected_sha256)
    release_relative = release_path(ref)
    if archive.is_symlink() or not archive.is_file() or not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ValueError('supply a regular archive, nonempty and <= 1 GiB')
    if digest(archive) != expected_sha256:
        raise ValueError('archive checksum mismatch; nothing extracted or written to PVC')
    pins = dict(model_version=model_version, manifest_sha256=manifest_sha256)
    with tempfile.TemporaryDirectory(prefix='modelwire-') as temporary:
        stage = Path(temporary)
        extract(archive, stage)
        files = verify_store(stage, ref, **pins)
        actual = {p.relative_to(stage) for p in stage.rglob('*') if p.is_file()}
        if actual != files:
            raise ValueError('archive must contain exactly the selected release graph')
        if destination.is_symlink():
            raise ValueError('symlink destination')
        destination.mkdir(parents=True, exist_ok=True)
        # Preflight all collisions before copying. A retry can reuse identical
        # files after interruption, but must never overwrite a different store.
        for relative in files:
            target = destination / relative
            for parent in (target, *target.parents):
                if parent.is_symlink():
                    raise ValueError('symlink destination')
            if target.exists() and (not target.is_file() or digest(target) != digest(stage / relative)):
                raise ValueError('immutable PVC file collision: ' + str(relative))
        # Publish the release ledger last. Consumers are selected only after Job
        # Complete; this is not a whole-directory transaction or hot activation.
        for relative in sorted(files - {release_relative}) + [release_relative]:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                private = Path(stream.name)
                try:
                    with (stage / relative).open('rb') as incoming:
                        shutil.copyfileobj(incoming, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                except BaseException:
                    private.unlink(missing_ok=True)
                    raise
            try:
                private.chmod(0o644)
                try:
                    os.link(private, target)
                except FileExistsError:
                    if digest(regular(destination, relative)) != digest(stage / relative):
                        raise ValueError('immutable PVC file collision: ' + str(relative))
            finally:
                private.unlink(missing_ok=True)
        verify_store(destination, ref, **pins)
    return {'status': 'MODELWIRE_PVC_VERIFIED', 'registry_available': True, 'release_ref': ref,
            'model_version': model_version, 'archive_sha256': expected_sha256, 'files': len(files),
            'activation_performed': False}


def receive(path, stream):
    """kubectl exec -i transport; atomic arrival, no tar executable required."""
    path = Path(path)
    partial = path.with_suffix('.part')
    if path.exists() or path.is_symlink():
        raise FileExistsError('archive already received')
    total = 0
    with partial.open('xb') as output:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(chunk)
            if total > MAX_ARCHIVE:
                raise ValueError('archive exceeds 1 GiB')
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if not total:
        raise ValueError('empty archive')
    os.rename(partial, path)


def render(args):
    """Offline ConfigMap + one-shot Job, optionally a new RWX PVC."""
    checksum(args.expected_sha256)
    checksum(args.model_version)
    checksum(args.manifest_sha256)
    release_path(args.release_ref)
    for value in (args.name, args.namespace, args.claim, args.service_account):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
            raise ValueError('use a DNS label for Job, namespace, PVC and service account')
    for image in [args.image] + ([args.aws_cli_image] if args.source == 's3' else []):
        if not image or not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image):
            raise ValueError('control-plane and AWS CLI images must be digest-pinned')
    metadata = {'name': args.name, 'namespace': args.namespace}
    hardening = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                 'capabilities': {'drop': ['ALL']}}
    mounts = [{'name': 'work', 'mountPath': '/work'}, {'name': 'tmp', 'mountPath': '/tmp'}]
    command = ['python', '/wire/modelwire.py', 'install', '--bundle', '/work/bundle.tar.gz',
               '--expected-sha256', args.expected_sha256, '--destination', '/models',
               '--release-ref', args.release_ref, '--model-version', args.model_version,
               '--manifest-sha256', args.manifest_sha256]
    if args.source == 'local':
        command += ['--wait-seconds', '600']
    container = {'name': 'modelwire', 'image': args.image, 'command': command,
                 'securityContext': hardening, 'env': [{'name': 'TMPDIR', 'value': '/work'}],
                 'volumeMounts': mounts + [{'name': 'wire', 'mountPath': '/wire', 'readOnly': True},
                                         {'name': 'models', 'mountPath': '/models'}],
                 'resources': {'requests': {'cpu': '100m', 'memory': '256Mi', 'ephemeral-storage': '3Gi'},
                               'limits': {'cpu': '1', 'memory': '2Gi', 'ephemeral-storage': '4Gi'}}}
    pod = {'restartPolicy': 'Never', 'serviceAccountName': args.service_account,
           'automountServiceAccountToken': False,
           'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                               'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
           'containers': [container], 'volumes': [
               {'name': 'work', 'emptyDir': {'sizeLimit': '3Gi'}},
               {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}},
               {'name': 'wire', 'configMap': {'name': args.name}},
               {'name': 'models', 'persistentVolumeClaim': {'claimName': args.claim}}]}
    if args.source == 's3':
        if (not args.bucket or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', args.bucket)
                or not args.key or not args.key.startswith('models/')
                or not args.version_id or args.version_id == 'null'
                or not args.account_id or not re.fullmatch(r'[0-9]{12}', args.account_id)):
            raise ValueError('S3 requires bucket, models/ key, immutable version-id and owner account-id')
        pod['initContainers'] = [{'name': 'download', 'image': args.aws_cli_image,
            'command': ['aws'], 'args': ['--region', args.region, '--no-cli-pager', 's3api', 'get-object',
                '--bucket', args.bucket, '--key', args.key, '--version-id', args.version_id,
                '--expected-bucket-owner', args.account_id, '--checksum-mode', 'ENABLED', '/work/bundle.tar.gz'],
            'env': [{'name': 'HOME', 'value': '/tmp'}, {'name': 'AWS_EC2_METADATA_DISABLED', 'value': 'true'}],
            'securityContext': hardening, 'volumeMounts': mounts,
            'resources': {'requests': {'cpu': '50m', 'memory': '128Mi'},
                          'limits': {'cpu': '500m', 'memory': '512Mi'}}}]
    access_mode = getattr(args, 'access_mode', 'ReadWriteMany')
    colocate = getattr(args, 'colocate_with', None)
    if access_mode not in ('ReadWriteMany', 'ReadWriteOnce'):
        raise ValueError('access mode must be ReadWriteMany or ReadWriteOnce')
    if access_mode == 'ReadWriteOnce':
        # EBS binds in the first consumer's AZ and attaches to one node, so the Job
        # must land on the node that already holds the consumers' RWO volumes.
        if not args.storage_class or not colocate or not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?', colocate):
            raise ValueError('ReadWriteOnce needs --storage-class and --colocate-with <consumer app label>')
        pod['affinity'] = {'podAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': [
            {'labelSelector': {'matchLabels': {'app': colocate}}, 'topologyKey': 'kubernetes.io/hostname'}]}}
    elif colocate:
        raise ValueError('--colocate-with applies only to ReadWriteOnce')
    documents = []
    if args.storage_class:
        documents.append({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
            'metadata': {'name': args.claim, 'namespace': args.namespace},
            'spec': {'accessModes': [access_mode], 'storageClassName': args.storage_class,
                     'resources': {'requests': {'storage': '4Gi'}}}})
    documents += [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata,
                   'data': {'modelwire.py': Path(__file__).read_text(encoding='utf-8')}},
                  {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': metadata,
                   'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 1200,
                            'template': {'metadata': {'labels': {'app': args.name}}, 'spec': pod}}}]
    return documents


def health_epoch(documents, root, *, health_only=False):
    """Select a fresh stream via its real artifact root; retain PVC mount paths.

    A model changes the consumer's evidence profile, which cannot be updated in
    place. The health producer hashes its artifact root into the stream identity.
    """
    if not re.fullmatch(r'/artifacts/health/[a-z0-9][a-z0-9-]{0,62}', root):
        raise ValueError('health root must be a new named child of /artifacts/health')
    documents = json.loads(json.dumps(documents))
    expected = {'sentinelops-health-worker': '--store',
                'sentinelops-incident-worker': '--source-store',
                'remediation-controller': '--source-store'}
    found = set()
    for doc in documents:
        name = doc['metadata']['name']
        if doc['kind'] != 'Deployment' or name not in expected:
            continue
        found.add(name)
        template = doc['spec']['template']
        template['metadata'].setdefault('annotations', {})['sentinelops.io/modelwire-health-root'] = root
        for container in template['spec']['containers']:
            sequences = [container['args']]
            for probe in ('readinessProbe', 'livenessProbe'):
                if probe in container:
                    sequences.append(container[probe]['exec']['command'])
            for sequence in sequences:
                position = sequence.index(expected[name]) + 1
                if not sequence[position].startswith('/artifacts/health'):
                    raise ValueError('unexpected health artifact root in worker manifest')
                sequence[position] = root
    if found != set(expected):
        raise ValueError('expected all three worker/controller Deployments from render_worker_manifests')
    if health_only:
        documents = [doc for doc in documents if (
            doc['kind'] == 'Deployment' and doc['metadata']['name'] == 'sentinelops-health-worker') or (
            doc['kind'] == 'ServiceAccount' and doc['metadata']['name'] == 'health-worker') or (
            doc['kind'] == 'ConfigMap' and doc['metadata']['name'] == 'sentinelops-workers-runtime')]
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for operation in ('install', 'verify', 'render'):
        cli = sub.add_parser(operation)
        cli.add_argument('--release-ref', required=True)
        cli.add_argument('--model-version', default=MODEL_VERSION)
        cli.add_argument('--manifest-sha256', default=MANIFEST_SHA256)
        if operation == 'verify':
            cli.add_argument('--store', type=Path, required=True)
        else:
            cli.add_argument('--expected-sha256', required=True)
        if operation == 'install':
            cli.add_argument('--bundle', type=Path, required=True)
            cli.add_argument('--destination', type=Path, required=True)
            cli.add_argument('--wait-seconds', type=int, default=0, help='local arrival timeout, 0..600 seconds')
    cli = sub.choices['render']
    cli.add_argument('--source', choices=('local', 's3'), required=True)
    cli.add_argument('--image', required=True, help='same control-plane digest as worker/controller')
    cli.add_argument('--name', default='modelwire-de38fc6b')
    cli.add_argument('--namespace', default='sentinelops-b-actions')
    cli.add_argument('--claim', default='model-de38fc6b')
    cli.add_argument('--service-account', default='default', help='existing SA; S3 needs IRSA/Pod Identity')
    cli.add_argument('--storage-class', help='optionally create a new PVC with this reviewed class')
    cli.add_argument('--access-mode', choices=('ReadWriteMany', 'ReadWriteOnce'), default='ReadWriteMany',
                     help='ReadWriteOnce (EBS) requires --colocate-with')
    cli.add_argument('--colocate-with', help='app label of the pod whose node holds the consumers RWO volumes')
    for name in ('bucket', 'key', 'version-id', 'account-id', 'aws-cli-image'):
        cli.add_argument('--' + name)
    cli.add_argument('--region', default='ap-southeast-1')
    cli.add_argument('--output', type=Path, required=True)
    cli = sub.add_parser('receive')
    cli.add_argument('--bundle', type=Path, required=True)
    cli = sub.add_parser('epoch', help='offline fresh-health-root selection, including exec probes')
    cli.add_argument('--workers', type=Path, required=True)
    cli.add_argument('--health-root', required=True)
    cli.add_argument('--health-only', action='store_true', help='bootstrap health before selecting its real stream')
    cli.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'install' and not 0 <= args.wait_seconds <= 600:
        parser.error('wait-seconds must be within 0..600')
    if args.operation in ('render', 'epoch'):
        import yaml
        if args.operation == 'render':
            documents = render(args)
        else:
            documents = health_epoch(list(yaml.safe_load_all(args.workers.read_text(encoding='utf-8'))),
                                     args.health_root, health_only=args.health_only)
        with args.output.open('x', encoding='utf-8') as stream:
            yaml.safe_dump_all(documents, stream, sort_keys=False)
    elif args.operation == 'receive':
        receive(args.bundle, sys.stdin.buffer)
    else:
        pins = dict(model_version=args.model_version, manifest_sha256=args.manifest_sha256)
        if args.operation == 'verify':
            files = verify_store(args.store, args.release_ref, **pins)
            print(json.dumps({'status': 'MODELWIRE_STORE_VERIFIED', 'registry_available': True,
                              'release_ref': args.release_ref, 'files': len(files)}))
        else:
            deadline = time.monotonic() + args.wait_seconds
            while not args.bundle.exists() and time.monotonic() < deadline:
                time.sleep(1)
            print(json.dumps(install(args.bundle, args.expected_sha256, args.destination, args.release_ref, **pins)))


if __name__ == '__main__':
    main()
