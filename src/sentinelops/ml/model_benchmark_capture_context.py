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
    SOURCE_SERVICES, acquisition_lock, clean_source, configure_compose, context_snapshot, docker, docker_clock,
    validate_end_context, volume_created_within,
)
from sentinelops.ml.model_benchmark_holdout import (
    holdout_compose_project,
    verify_candidate_freeze, verify_capture_group, verify_capture_order, verify_capture_registration,
)
from sentinelops.ml.model_benchmark_protocol import _bytes, _ref, publish_freeze
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


def inspect_group_continuity(group, *, completed_at=None, require_running=True):
    """Inspect original container processes without inferring their past state."""
    containers = group['context']['containers']
    actual = json.loads(docker('inspect', *[item['container_id'] for item in containers.values()]))
    projected = {}
    for item in actual:
        labels = item['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.project') != group['compose_project']:
            raise ValueError('holdout context contains an unowned source')
        service = labels['com.docker.compose.service']
        if service in projected:
            raise ValueError('holdout context contains a duplicate service')
        state = item['State']
        projected[service] = {'container_id': item['Id'], 'image': item['Image'],
            'created_at': docker_clock(item['Created']), 'started_at': docker_clock(state['StartedAt']),
            'finished_at': docker_clock(state['FinishedAt']), 'running': state['Running'],
            'created_at_source': item['Created'], 'started_at_source': state['StartedAt'],
            'finished_at_source': state['FinishedAt'], 'restart_count': item['RestartCount']}
    observed = {'kind': 'local-corpus-context-inspection/v1', 'compose_project': group['compose_project'],
                'containers': projected, 'collected_at': timestamp(datetime.now(timezone.utc))}
    validate_end_context(group, observed, utc_time(completed_at or group['registered_at']))
    if require_running and any(not item['running'] for item in projected.values()):
        raise ValueError('holdout source must be running before workload')
    return observed


def ensure_previous_contexts_stopped(matrix, group_id, pilot_group, registration_sha256):
    """Keep pilot and prior holdout contexts stopped before each workload."""
    groups = matrix['groups']
    selected = [index for index, group in enumerate(groups) if group['group_id'] == group_id]
    if len(selected) != 1 or not isinstance(pilot_group, str) or not re.fullmatch('ale-0[12]', pilot_group):
        raise ValueError('invalid holdout or development pilot group')
    if docker('ps', '-q', '--filter', 'label=com.docker.compose.project=sentinelops-corpus-' + pilot_group).strip():
        raise ValueError('pilot source context is still running; finish and close it first')
    for group in groups[:selected[0]]:
        previous = holdout_compose_project(registration_sha256, group['group_id'])
        if docker('ps', '-q', '--filter', 'label=com.docker.compose.project=' + previous).strip():
            raise ValueError('previous holdout source context is still running')


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
    name = holdout_compose_project(registration_sha256, group_id)
    ensure_previous_contexts_stopped(matrix, group_id, pilot['group_id'], registration_sha256)
    if group_path.exists():
        value = json.loads(group_path.read_bytes())
        if (value.get('schema_version') != 'benchmark-holdout-group-registration/v1'
                or value.get('group_id') != group_id or value.get('capture_commit') != registration['capture_commit']
                or value.get('capture_registration_sha256') != registration_sha256 or value.get('compose_project') != name
                or value.get('initial_payment_rows') != 0):
            raise ValueError('existing holdout group registration differs')
        configure_compose(project, root / 'capture', group_id, name)
        if context_snapshot(project, name) != value['context']:
            raise ValueError('holdout source context changed after registration')
        inspect_group_continuity(value)
        return {'status': 'ready', 'group_id': group_id,
                'group_registration_sha256': hashlib.sha256(group_path.read_bytes()).hexdigest()}
    intent_path = directory / 'provisioning.json'
    if intent_path.exists():
        raise ValueError('partial holdout provisioning requires investigation; no automatic restart')
    if docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + name).strip():
        raise ValueError('unregistered holdout containers already exist')
    if docker('volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + name).strip():
        raise ValueError('unregistered holdout volume already exists')
    created = datetime.now(timezone.utc)
    intent = {'schema_version': 'benchmark-holdout-provisioning/v1', 'group_id': group_id,
              'capture_registration_sha256': registration_sha256, 'capture_commit': registration['capture_commit'],
              'compose_project': name, 'created_at': timestamp(created)}
    publish_freeze(intent_path, _bytes(intent), conflict='different holdout provisioning intent exists')
    configure_compose(project, root / 'capture', group_id, name)
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


def register_slot(root, group_id, slot_id, registration_sha256, *, project):
    """Register one ordered slot before its workload; never start or resume capture."""
    root, project = Path(root).resolve(), Path(project).resolve()
    with acquisition_lock(ArtifactStore(root)):
        registration, matrix, _ = load_capture_design(root, registration_sha256, project=project)
        groups = [group for group in matrix['groups'] if group['group_id'] == group_id]
        if len(groups) != 1:
            raise ValueError('group is not part of the registered holdout matrix')
        group = groups[0]
        selected = [index for index, slot in enumerate(group['slots']) if slot['slot_id'] == slot_id]
        if len(selected) != 1:
            raise ValueError('slot is not part of the registered holdout group')
        group_dir = root / 'capture/groups' / group_id
        if not (group_dir / 'registration.json').is_file():
            raise ValueError('holdout group must be prepared before slot registration')
        prepared = _prepare_group(root, group_id, registration_sha256, project=project)
        if prepared['status'] != 'ready':
            raise ValueError('completed holdout group cannot receive new slots')
        group_pin = prepared['group_registration_sha256']
        verify_capture_order(root, matrix, group_id, slot_id, matrix_sha256=registration['matrix_sha256'],
                             candidates_sha256=registration['candidates_sha256'])
        directory = group_dir / 'slots' / slot_id
        path = directory / 'registration.json'
        if (directory / 'receipt.json').exists() or (directory / 'runs').exists():
            raise ValueError('existing holdout capture requires investigation; no automatic restart')
        expected = {'schema_version': 'benchmark-holdout-slot-registration/v1', 'group_id': group_id,
                    'slot_id': slot_id, 'group_registration_sha256': group_pin,
                    'capture_commit': registration['capture_commit'], 'recipe': group['slots'][selected[0]]['recipe']}
        if path.exists():
            value = json.loads(path.read_bytes())
            if {key: item for key, item in value.items() if key != 'registered_at'} != expected:
                raise ValueError('existing holdout slot registration differs')
            utc_time(value['registered_at'])
        else:
            if directory.exists() and any(directory.iterdir()):
                raise ValueError('unregistered holdout slot artifacts already exist')
            value = {**expected, 'registered_at': timestamp(datetime.now(timezone.utc))}
            publish_freeze(path, _bytes(value), conflict='different holdout slot already registered')
        return {'status': 'ready', 'slot_id': slot_id, 'group_registration_sha256': group_pin,
                'slot_registration_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def seal_slot_context(root, group_id, slot_id, run_id, registration_sha256, *, project):
    """Record a post-run container observation tied to one registered capture."""
    root, project = Path(root).resolve(), Path(project).resolve()
    with acquisition_lock(ArtifactStore(root)):
        registration, matrix, _ = load_capture_design(root, registration_sha256, project=project)
        groups = [group for group in matrix['groups'] if group['group_id'] == group_id]
        slots = [slot for group in groups for slot in group['slots'] if slot['slot_id'] == slot_id]
        if len(groups) != 1 or len(slots) != 1 or not re.fullmatch('run-[0-9a-f]{32}', run_id):
            raise ValueError('run is not part of the registered holdout slot')
        prepared = _prepare_group(root, group_id, registration_sha256, project=project)
        if prepared['status'] != 'ready':
            raise ValueError('completed holdout group cannot receive new context evidence')
        group_pin = prepared['group_registration_sha256']
        group_path = root / 'capture/groups' / group_id / 'registration.json'
        group = json.loads(group_path.read_bytes())
        directory = group_path.parent / 'slots' / slot_id
        slot_path = directory / 'registration.json'
        slot = json.loads(slot_path.read_bytes())
        slot_pin = hashlib.sha256(slot_path.read_bytes()).hexdigest()
        if (slot.get('schema_version') != 'benchmark-holdout-slot-registration/v1'
                or slot.get('group_id') != group_id or slot.get('slot_id') != slot_id
                or slot.get('group_registration_sha256') != group_pin
                or slot.get('capture_commit') != registration['capture_commit']
                or stable_id(slot.get('recipe')) != stable_id(slots[0]['recipe'])):
            raise ValueError('slot context differs from its registration')
        runs = directory / 'runs'
        captured = runs / run_id
        if any(path.is_symlink() for path in (runs, captured, captured / 'run.json')):
            raise ValueError('holdout context source cannot traverse symlinks')
        run = json.loads((captured / 'run.json').read_bytes())
        if (run.get('run_id') != run_id or run.get('session_id') != group_id or run.get('status') != 'completed'
                or run.get('source_commit') != registration['capture_commit']
                or run.get('source_profile') != matrix['source_profile']
                or run.get('image_digests') != group['context']['images']
                or utc_time(run['started_at']) < utc_time(slot['registered_at'])
                or utc_time(run['ended_at']) < utc_time(run['started_at'])):
            raise ValueError('completed run differs from its registered holdout source')
        store = ArtifactStore(root)
        path = directory / 'end-context.json'
        expected = {'schema_version': 'benchmark-holdout-context-end/v1', 'run_id': run_id,
                    'run_sha256': stable_id(run), 'capture_registration_sha256': registration_sha256,
                    'group_registration_sha256': group_pin, 'slot_registration_sha256': slot_pin}
        if path.exists():
            receipt = json.loads(path.read_bytes())
            if {key: value for key, value in receipt.items() if key != 'inspection_ref'} != expected:
                raise ValueError('stored holdout context belongs to another run or registration')
            validate_end_context(group, store.read(receipt['inspection_ref']), utc_time(run['ended_at']))
        else:
            observation = inspect_group_continuity(group, completed_at=run['ended_at'])
            receipt = {**expected, 'inspection_ref': store.write('raw', observation)}
            publish_freeze(path, _bytes(receipt), conflict='different holdout context receipt already exists')
        return {'status': 'ready', 'end_context_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'inspection_ref': receipt['inspection_ref']}


def verify_slot_context(root, group_id, slot, run_id, registration_sha256, *, capture_commit, run_path, source_profile,
                        registered_at):
    """Replay the archived post-run context without consulting Docker or outcome labels."""
    root, run_path = Path(root).resolve(), Path(run_path).resolve()
    directory = root / 'capture/groups' / group_id
    slot_dir = directory / 'slots' / slot['slot_id']
    expected_run = slot_dir / 'runs' / run_id / 'run.json'
    if (not re.fullmatch('bench-final-[0-9]{2}', group_id) or not re.fullmatch('run-[0-9a-f]{32}', run_id)
            or run_path != expected_run.resolve()
            or any(path.is_symlink() for path in (slot_dir / 'runs', expected_run.parent, expected_run))):
        raise ValueError('holdout context run path differs from its registered slot')
    receipt = json.loads((slot_dir / 'end-context.json').read_bytes())
    if set(receipt) != {'schema_version', 'run_id', 'run_sha256', 'capture_registration_sha256',
                        'group_registration_sha256', 'slot_registration_sha256', 'inspection_ref'}:
        raise ValueError('invalid holdout end-context receipt')
    group = json.loads(_ref(root, {'path': (directory / 'registration.json').relative_to(root).as_posix(),
                                   'sha256': receipt['group_registration_sha256']}))
    registered_slot = json.loads(_ref(root, {'path': (slot_dir / 'registration.json').relative_to(root).as_posix(),
                                             'sha256': receipt['slot_registration_sha256']}))
    run = json.loads(run_path.read_bytes())
    if (receipt['schema_version'] != 'benchmark-holdout-context-end/v1' or receipt['run_id'] != run_id
            or receipt['run_sha256'] != stable_id(run) or receipt['capture_registration_sha256'] != registration_sha256
            or not isinstance(receipt['inspection_ref'], str)
            or not re.fullmatch(r'artifact://raw/[0-9a-f]{64}\.json', receipt['inspection_ref'])
            or group.get('schema_version') != 'benchmark-holdout-group-registration/v1'
            or group.get('group_id') != group_id or group.get('capture_registration_sha256') != registration_sha256
            or group.get('capture_commit') != capture_commit or group.get('compose_project') != holdout_compose_project(registration_sha256, group_id)
            or type(group.get('initial_payment_rows')) is not int or group['initial_payment_rows'] != 0
            or registered_slot.get('schema_version') != 'benchmark-holdout-slot-registration/v1'
            or registered_slot.get('group_id') != group_id or registered_slot.get('slot_id') != slot['slot_id']
            or registered_slot.get('group_registration_sha256') != receipt['group_registration_sha256']
            or registered_slot.get('capture_commit') != capture_commit
            or stable_id(registered_slot.get('recipe')) != stable_id(slot['recipe'])
            or run.get('run_id') != run_id or run.get('session_id') != group_id or run.get('status') != 'completed'
            or run.get('source_commit') != capture_commit or run.get('source_profile') != source_profile
            or run.get('image_digests') != group['context']['images']
            or not utc_time(registered_at) <= utc_time(group['registered_at']) <= utc_time(registered_slot['registered_at'])
                   <= utc_time(run['started_at']) <= utc_time(run['ended_at'])):
        raise ValueError('holdout end-context identity or timing differs')
    observation = ArtifactStore(root).read(receipt['inspection_ref'])
    validate_end_context(group, observation, utc_time(run['ended_at']))
    return receipt
