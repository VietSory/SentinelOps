#!/usr/bin/env python3
"""Plan two ECR image pushes; only --execute builds, authenticates or pushes.

Use an explicitly reviewed Git commit and an immutable application base image.
No AWS/Docker command, credential lookup or output write occurs in dry-run mode.
After a real push, keep the digest file and pin manifests to repository@sha256:… .
Requires Python 3.11.8+ (tar extraction filter), git, Docker and AWS CLI on execution.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-id', required=True, help='12-digit owner AWS account')
    parser.add_argument('--region', required=True, choices=['ap-southeast-1'])
    parser.add_argument('--application-repo', required=True, help='e.g. sentinelops/application')
    parser.add_argument('--control-plane-repo', required=True, help='e.g. sentinelops/control-plane')
    parser.add_argument('--tag', required=True, help='New immutable release tag; latest is rejected')
    parser.add_argument('--source-revision', required=True, help='Full reviewed 40-character Git SHA')
    parser.add_argument('--application-base', required=True,
                        help='Reviewed python:3.11-slim@sha256:<64 lowercase hex>')
    parser.add_argument('--digest-file', required=True, type=Path,
                        help='New local JSON file; saves each successful push, including partial runs')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Default: print commands only')
    mode.add_argument('--execute', action='store_true', help='Actually build and push after external review')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[0-9]{12}', args.account_id):
        parser.error('--account-id must contain exactly 12 digits')
    for name in (args.application_repo, args.control_plane_repo):
        if len(name) > 256 or not re.fullmatch(r'[a-z0-9]+(?:[._/-][a-z0-9]+)*', name):
            parser.error('repository names must be explicit ECR names, without a registry or tag')
    if args.application_repo == args.control_plane_repo:
        parser.error('the two repositories must differ')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', args.tag) or args.tag.lower() == 'latest':
        parser.error('--tag must be a new explicit release tag, never latest')
    if not re.fullmatch(r'[0-9a-f]{40}', args.source_revision):
        parser.error('--source-revision must be a full lowercase Git SHA')
    if not re.fullmatch(r'python:3\.11-slim@sha256:[0-9a-f]{64}', args.application_base):
        parser.error('--application-base must pin python:3.11-slim by digest')
    if args.digest_file.exists() or args.digest_file.is_symlink():
        parser.error('--digest-file must be new; previous evidence is never overwritten')
    if not args.digest_file.parent.is_dir():
        parser.error('--digest-file parent directory must already exist')
    return args


def show(command):
    print('+ ' + shlex.join(map(str, command)), flush=True)


def run(command, *, capture=False, data=None, binary=False):
    show(command)
    return subprocess.run(list(map(str, command)), cwd=PROJECT, check=True,
                          capture_output=capture, input=data, text=not binary).stdout


def aws(args, *command):
    return ['aws', '--region', args.region, '--no-cli-pager', '--output', 'json', *command]


def describe(args, repository):
    return aws(args, 'ecr', 'describe-repositories', '--registry-id', args.account_id,
               '--repository-names', repository)


def lookup(args, repository):
    return aws(args, 'ecr', 'describe-images', '--registry-id', args.account_id,
               '--repository-name', repository, '--image-ids', 'imageTag=' + args.tag)


def build(args, kind, uri, source, docker_config):
    command = ['docker', '--config', docker_config, 'build', '--platform', 'linux/amd64',
               '--label', 'org.opencontainers.image.revision=' + args.source_revision,
               '--tag', uri + ':' + args.tag]
    if kind == 'application':
        # Match the existing packaging workflow: pin the service base in memory.
        command += ['--file', '-', source]
    else:
        command += ['--build-arg', 'SENTINELOPS_SOURCE_REVISION=' + args.source_revision,
                    '--file', str(Path(source) / 'docker/Dockerfile.control-plane'), source]
    return command


def save(stream, record):
    stream.seek(0)
    json.dump(record, stream, indent=2)
    stream.write('\n')
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def main(argv=None):
    args = arguments(argv)
    registry = f'{args.account_id}.dkr.ecr.{args.region}.amazonaws.com'
    images = [('application', args.application_repo), ('control-plane', args.control_plane_repo)]
    identity = aws(args, 'sts', 'get-caller-identity')
    archive = ['git', 'archive', '--format=tar', args.source_revision]
    password = aws(args, 'ecr', 'get-login-password')
    print('EXECUTE' if args.execute else 'DRY-RUN (no commands executed)', flush=True)
    print('Build context: archived reviewed commit; platform: linux/amd64.')
    print('Application Dockerfile stdin: FROM ' + args.application_base)
    print('Record both ECR digests after push; manifests MUST use repository@sha256:… .')
    if not args.execute:
        show(identity)
        for _, repository in images:
            show(describe(args, repository))
        show(archive)
        print('# Extract Git archive into a private temporary build context.')
        print('# Private temporary Docker config; password passes through stdin only.')
        show(password)
        show(['docker', '--config', '<temporary-config>', 'login', '--username', 'AWS',
              '--password-stdin', registry])
        for kind, repository in images:
            uri = registry + '/' + repository
            show(build(args, kind, uri, '<archived-source>', '<temporary-config>'))
            show(['docker', '--config', '<temporary-config>', 'push', uri + ':' + args.tag])
            show(lookup(args, repository))
        print('Would save registry digests and pinned references to: ' + str(args.digest_file))
        return

    if json.loads(run(identity, capture=True)).get('Account') != args.account_id:
        raise ValueError('active AWS identity does not match the explicit owner account')
    for _, repository in images:
        details, = json.loads(run(describe(args, repository), capture=True))['repositories']
        if (details.get('repositoryUri') != registry + '/' + repository
                or details.get('imageTagMutability') != 'IMMUTABLE'):
            raise ValueError('repository URI/immutable tag guard failed: ' + repository)

    record = {'source_revision': args.source_revision, 'account_id': args.account_id,
              'region': args.region, 'platform': 'linux/amd64', 'tag': args.tag,
              'application_base': args.application_base, 'status': 'partial', 'images': {}}
    # Reserve evidence before any build/push. Fail without replacing an existing file.
    with args.digest_file.open('x', encoding='utf-8') as stream:
        save(stream, record)
        with tempfile.TemporaryDirectory(prefix='sentinelops-ecr-') as private:
            source = Path(private) / 'source'
            config = Path(private) / 'docker'
            source.mkdir()
            config.mkdir(mode=0o700)
            content = run(archive, capture=True, binary=True)
            with tarfile.open(fileobj=io.BytesIO(content)) as bundle:
                bundle.extractall(source, filter='data')
            original = (source / 'docker/Dockerfile.service').read_text()
            if not original.startswith('FROM python:3.11-slim\n'):
                raise ValueError('application Dockerfile base changed; review packaging before pushing')
            application = original.replace('FROM python:3.11-slim\n',
                                           'FROM ' + args.application_base + '\n', 1)
            token = run(password, capture=True)
            run(['docker', '--config', config, 'login', '--username', 'AWS',
                 '--password-stdin', registry], data=token)
            del token
            for kind, repository in images:
                uri = registry + '/' + repository
                run(build(args, kind, uri, str(source), str(config)),
                    data=application if kind == 'application' else None)
                output = run(['docker', '--config', config, 'push', uri + ':' + args.tag], capture=True)
                print(output, end='', flush=True)
                pushed = re.findall(r'digest: (sha256:[0-9a-f]{64})', output)
                details, = json.loads(run(lookup(args, repository), capture=True))['imageDetails']
                digest = details.get('imageDigest', '')
                if not DIGEST.fullmatch(digest) or pushed != [digest]:
                    raise ValueError('push digest and ECR digest must match: ' + repository)
                reference = uri + '@' + digest
                record['images'][kind] = {'tagged': uri + ':' + args.tag,
                                          'digest': digest, 'pinned': reference}
                save(stream, record)
                print('PIN ' + kind + '=' + reference, flush=True)
        record['status'] = 'complete'
        save(stream, record)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
