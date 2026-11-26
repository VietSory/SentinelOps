"""Prepare isolated holdout group contexts after prospective registration."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import sentinelops

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import (
    SOURCE_SERVICES, acquisition_lock, clean_source, configure_compose, context_snapshot, docker,
    volume_created_within,
)
from sentinelops.ml.model_benchmark_holdout import (
    verify_candidate_freeze, verify_capture_group, verify_capture_registration,
)
from sentinelops.ml.model_benchmark_protocol import _bytes, _ref, publish_freeze
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


def load_capture_design(root, registration_sha256, *, project):
    """Check all frozen identities before accessing a holdout source or Docker."""
    root, project = Path(root).resolve(), Path(project).resolve()
    if not Path(sentinelops.__file__).samefile(project / 'src/sentinelops/__init__.py'):
        raise ValueError('capture must import its clean checkout source')
    commit = clean_source(project)
    registration = json.loads(_ref(root, {'path': 'capture/registration.json', 'sha256': registration_sha256}))
    frozen = verify_candidate_freeze(root, registration['candidates_sha256'])
    registration = verify_capture_registration(root, registration_sha256=registration_sha256,
        matrix_sha256=registration['matrix_sha256'], candidates_sha256=registration['candidates_sha256'],
        capture_commit=commit, frozen=frozen)
    matrix = json.loads((root / 'capture/matrix.json').read_bytes())
    if (stable_id(matrix) != registration['matrix_sha256'] or matrix.get('role') != 'sealed-final'
            or matrix.get('slot_count') != 35 or len(matrix.get('groups', [])) != 5):
        raise ValueError('holdout matrix differs from the registered five-group design')
    pilot = json.loads(_ref(root, registration['pilot_receipt']))
    return registration, matrix, pilot


def _prepare_group(root, group_id, registration_sha256, *, project):
    registration, matrix, pilot = load_capture_design(root, registration_sha256, project=project)
    groups = matrix['groups']
    selected = [index for index, group in enumerate(groups) if group['group_id'] == group_id]
    if len(selected) != 1 or not re.fullmatch('bench-final-[0-9]{2}', group_id):
        raise ValueError('group is not part of the registered holdout matrix')
    root = Path(root).resolve()
    for group in groups[:selected[0]]:
        path = root / 'capture/groups' / group['group_id'] / 'receipt.json'
        if not path.is_file():
            raise ValueError('earlier holdout group is not complete')
        verify_capture_group(group, json.loads(path.read_bytes()), matrix_sha256=registration['matrix_sha256'],
                             candidates_sha256=registration['candidates_sha256'], source_profile=matrix['source_profile'])
    directory = root / 'capture/groups' / group_id
    group_path = directory / 'registration.json'
    receipt_path = directory / 'receipt.json'
    if receipt_path.exists():
        verify_capture_group(groups[selected[0]], json.loads(receipt_path.read_bytes()),
            matrix_sha256=registration['matrix_sha256'], candidates_sha256=registration['candidates_sha256'],
            source_profile=matrix['source_profile'])
        return {'status': 'complete', 'group_id': group_id}
    name = 'sentinelops-corpus-' + group_id
    if group_path.exists():
        value = json.loads(group_path.read_bytes())
        if (value.get('schema_version') != 'benchmark-holdout-group-registration/v1'
                or value.get('group_id') != group_id or value.get('capture_commit') != registration['capture_commit']
                or value.get('capture_registration_sha256') != registration_sha256 or value.get('compose_project') != name
                or value.get('initial_payment_rows') != 0):
            raise ValueError('existing holdout group registration differs')
        configure_compose(project, root / 'capture', group_id)
        if context_snapshot(project, name) != value['context']:
            raise ValueError('holdout source context changed after registration')
        return {'status': 'ready', 'group_id': group_id,
                'group_registration_sha256': hashlib.sha256(group_path.read_bytes()).hexdigest()}
    intent_path = directory / 'provisioning.json'
    if intent_path.exists():
        raise ValueError('partial holdout provisioning requires investigation; no automatic restart')
    pilot_group = pilot['group_id']
    if not isinstance(pilot_group, str) or not re.fullmatch('ale-0[12]', pilot_group):
        raise ValueError('invalid development pilot group')
    if docker('ps', '-q', '--filter', 'label=com.docker.compose.project=sentinelops-corpus-' + pilot_group).strip():
        raise ValueError('pilot source context is still running; finish and close it first')
    for group in groups[:selected[0]]:
        previous = 'sentinelops-corpus-' + group['group_id']
        if docker('ps', '-q', '--filter', 'label=com.docker.compose.project=' + previous).strip():
            raise ValueError('previous holdout source context is still running')
    if docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + name).strip():
        raise ValueError('unregistered holdout containers already exist')
    if docker('volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + name).strip():
        raise ValueError('unregistered holdout volume already exists')
    created = datetime.now(timezone.utc)
    intent = {'schema_version': 'benchmark-holdout-provisioning/v1', 'group_id': group_id,
              'capture_registration_sha256': registration_sha256, 'capture_commit': registration['capture_commit'],
              'compose_project': name, 'created_at': timestamp(created)}
    publish_freeze(intent_path, _bytes(intent), conflict='different holdout provisioning intent exists')
    configure_compose(project, root / 'capture', group_id)
    subprocess.run(['docker', 'compose', 'up', '--build', '-d', '--wait', '--wait-timeout', '180', *SOURCE_SERVICES],
                   cwd=project, check=True, timeout=600)
    context = context_snapshot(project, name)
    observed = datetime.now(timezone.utc)
    if (any(not created <= utc_time(item['created_at']) <= observed for item in context['containers'].values())
            or not volume_created_within(context['database_volume']['created_at'], created, observed)):
        raise ValueError('holdout source was created outside its provisioning interval')
    rows = docker('compose', 'exec', '-T', 'postgres', 'psql', '-U', 'sentinel', '-d', 'sentinelops',
                  '-At', '-v', 'ON_ERROR_STOP=1', '-c', 'SELECT count(*) FROM payments;').strip()
    if rows != '0':
        raise ValueError('holdout group requires a fresh payment database')
    if clean_source(project) != registration['capture_commit']:
        raise ValueError('capture source changed during group provisioning')
    value = {'schema_version': 'benchmark-holdout-group-registration/v1', 'group_id': group_id,
             'capture_registration_sha256': registration_sha256, 'capture_commit': registration['capture_commit'],
             'registered_at': timestamp(datetime.now(timezone.utc)), 'compose_project': name,
             'context': context, 'initial_payment_rows': 0}
    publish_freeze(group_path, _bytes(value), conflict='different holdout group already registered')
    return {'status': 'ready', 'group_id': group_id,
            'group_registration_sha256': hashlib.sha256(group_path.read_bytes()).hexdigest()}


def prepare_group(root, group_id, registration_sha256, *, project):
    """Provision one group under the acquisition lock; never start a workload."""
    with acquisition_lock(ArtifactStore(root)):
        return _prepare_group(root, group_id, registration_sha256, project=Path(project).resolve())
