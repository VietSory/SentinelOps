"""Pin observed local Docker bytes and bind inputs; never approve a model/action.

Image IDs are local content identities, not portable registry pull references or
proof of an image build's Git origin. The observer's own implementation is pinned.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from scripts import lane_b
from sentinelops.contracts import validate_payload
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


def bind_hashes(config, project):
    files = {}
    for service in config['services'].values():
        for mount in service.get('volumes',[]):
            if mount.get('type') != 'bind': continue
            path = Path(mount['source']).resolve()
            if not path.is_relative_to(project.resolve()) or not path.is_file() or mount.get('read_only') is not True:
                raise ValueError('a local deployment lock requires owned read-only bind files')
            files[path.relative_to(project.resolve()).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files: raise ValueError('a deployment lock must retain its actual mounted configuration inputs')
    return files


def snapshot(config, *, project, root):
    lane_b.validate_config(config)
    lane_b.require_owned_containers()
    if subprocess.check_output(['git','status','--porcelain'],cwd=project).strip():
        raise ValueError('commit the release observer/configuration before taking an immutable snapshot')
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=project,text=True).strip()
    identifiers = subprocess.check_output(['docker','ps','-aq','--filter',
        'label=com.docker.compose.project='+lane_b.NAME],text=True).split()
    if len(identifiers) != len(lane_b.PORTS) or any(not re.fullmatch('[0-9a-f]{12,64}',item) for item in identifiers):
        raise ValueError('snapshot requires exactly the eight owned B runtime containers')
    template = ('{"container_id":{{json .Id}},"image_id":{{json .Image}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"running":{{json .State.Running}},"health":{{with (index .State "Health")}}{{json .Status}}{{else}}null{{end}}}')
    raw = subprocess.check_output(['docker','inspect','--format',template,*identifiers],text=True)
    images = {}
    for line in raw.splitlines():
        row = json.loads(line); service = row['service']
        if service not in lane_b.PORTS or service in images or row['running'] is not True or row['health'] not in (None,'healthy'):
            raise ValueError('snapshot encountered duplicate, unowned or nonready container state')
        images[service] = {key:row[key] for key in ('image_id','container_id')}
    record = {'lock_version':'lane-b-deployment/v1','scope':'local-compose-images-and-bind-inputs',
        'compose_project':lane_b.NAME,'observed_at':timestamp(datetime.now(timezone.utc)),
        'observer_commit':commit,'observer_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'config_sha256':stable_id(config),'bind_file_sha256':bind_hashes(config,project),'images':images,
        'host_processes_pinned':False,'model_activation_allowed':False,'remediation_approval':False}
    validate_payload('lane-b-deployment-lock.schema.json',record)
    if (subprocess.check_output(['git','status','--porcelain'],cwd=project).strip()
        or subprocess.check_output(['git','rev-parse','HEAD'],cwd=project,text=True).strip() != commit):
        raise ValueError('release observer changed while Docker/configuration was observed')
    return ArtifactStore(root/'releases').write('manifests',record)


def materialize(config, reference, *, project, root):
    lane_b.validate_config(config)
    store = ArtifactStore(root/'releases')
    record = store.read(reference)
    validate_payload('lane-b-deployment-lock.schema.json',record)
    if record['config_sha256'] != stable_id(config) or record['bind_file_sha256'] != bind_hashes(config,project):
        raise ValueError('current configuration/bind bytes differ from the selected deployment lock')
    # Inspect all image identities before any compose mutation; no mutable-tag
    # fallback, remote pulling or partial deployment when a local image is absent.
    for image in record['images'].values():
        actual = subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',image['image_id']],text=True).strip()
        if actual != image['image_id']: raise ValueError('locked local image is missing or changed')
    lines = ['services:']
    for service, image in sorted(record['images'].items()):
        lines.extend(['  '+service+':','    image: '+image['image_id'],'    pull_policy: never','    build: !reset null'])
    body = '\n'.join(lines)+'\n'
    path = root/'release-overrides'/(stable_id(record)+'.yml')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_text() != body:
            raise ValueError('immutable deployment override differs from its locked manifest')
    else:
        with path.open('x') as stream: stream.write(body)
    return path
