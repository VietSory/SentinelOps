"""Prospective local capture registration under owner-approved ADR-014.

The original experiment runner supplies raw metrics, logs, traces and a separate
request-outcome oracle. This layer fixes role/group/config/source identity before
traffic and never certifies health from a registration or completed-run flag.
"""
from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess

from sentinelops.contracts import utc_time
from sentinelops.experiments.runner import ExperimentConfig, execute, image_identity
from sentinelops.public_data.policy import load_training_policy, policy_hash, require_current_training_source
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_SET, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE, SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore

ROLES = ('train', 'calibration', 'development-validation', 'sealed-final')
KINDS = ('healthy', 'payment-gradual', 'inventory-gradual')
# ADR-015: evaluation-only abrupt detector events. Never a fitting source.
DETECTOR_PLAN_VERSION = 'local-http-detector-events-plan/v1'
DETECTOR_KINDS = ('healthy', 'payment-abrupt', 'inventory-abrupt')
DETECTOR_ROLES = ('development-validation',)
ALIBABA_PLAN_VERSION = 'local-http-alibaba-evaluation/v1'
ALIBABA_KINDS = (*DETECTOR_KINDS, 'payment-gradual', 'inventory-gradual')
PROJECT = Path(__file__).resolve().parents[3]
DEFAULT_PLAN = PROJECT / 'configs/local_http_capture_plan.json'
DETECTOR_PLAN = PROJECT / 'configs/local_http_detector_events_plan.json'
ALIBABA_PLAN = PROJECT / 'configs/alibaba_local_evaluation.json'
ALIBABA_BINDING = 'alibaba-epoch.json'
SOURCE_SERVICES = (*sorted(BUSINESS_ROUTES), 'postgres', 'prometheus', 'otel-collector', 'jaeger')
SLOT_PATTERN = re.compile(r'(?:(?:train|cal|dev)-[0-9]{2}-(?:healthy|payment-gradual|inventory-gradual)'
                          r'|det-[0-9]{2}-(?:healthy|payment-abrupt|inventory-abrupt)'
                          r'|(?:ali-0[1-5]|alb-0[1-7])-(?:healthy|payment-abrupt|inventory-abrupt|payment-gradual|inventory-gradual))')


def is_detector_plan(plan):
    return plan.get('plan_version') == DETECTOR_PLAN_VERSION


def is_alibaba_plan(plan):
    return plan.get('plan_version') == ALIBABA_PLAN_VERSION


def plan_kinds(plan):
    if is_alibaba_plan(plan):
        return ALIBABA_KINDS
    return DETECTOR_KINDS if is_detector_plan(plan) else KINDS


def plan_roles(plan):
    """Acquisition roles in chronological order; sealed-final is never captured."""
    return DETECTOR_ROLES if is_detector_plan(plan) or is_alibaba_plan(plan) else ROLES


def _load_detector_plan(plan):
    policy = load_training_policy()
    require_current_training_source('local_http', policy)
    expected = {'plan_version': DETECTOR_PLAN_VERSION, 'authorizing_adr': 'ADR-015',
        'policy_sha256': policy_hash(policy), 'source_profile': SOURCE_PROFILE, 'feature_set_version': FEATURE_SET,
        'environment': 'local', 'oracle_protocol': 'server-completion-client-ledger/v1',
        'evaluation_protocol': 'local-model-development/v2', 'roles_in_order': list(DETECTOR_ROLES),
        'group_prefix': 'det', 'kinds': list(DETECTOR_KINDS),
        'healthy_active_seconds': {'development-validation': 900}, 'abrupt_active_seconds': 600,
        'abrupt_fault_mode_by_ordinal_parity': {'odd': 'latency', 'even': 'error'},
        'warmup_seconds': 300, 'recovery_seconds': 360, 'entry_rps': 1.0, 'maximum_delay_ms': 1200,
        'fitting_allowed': False, 'sealed_final_capture_allowed': False, 'previous_capture_promotion_allowed': False}
    allowed = set(expected) | {'seed_base', 'minimum_groups', 'maximum_groups', 'group_isolation'}
    if set(plan) != allowed or any(plan.get(k) != v or type(plan.get(k)) is not type(v) for k, v in expected.items()):
        raise ValueError('detector-event plan differs from the ADR-015 accepted protocol')
    if (type(plan['seed_base']) is not int or plan['seed_base'] < 0
        or set(plan['minimum_groups']) != set(DETECTOR_ROLES) or set(plan['maximum_groups']) != set(DETECTOR_ROLES)
        or type(plan['minimum_groups']['development-validation']) is not int
        or type(plan['maximum_groups']['development-validation']) is not int
        or not 5 <= plan['minimum_groups']['development-validation'] <= plan['maximum_groups']['development-validation'] <= 10):
        raise ValueError('detector-event group plan weakens the accepted bounds')
    for kind in DETECTOR_KINDS:
        for ordinal in (1, 2):
            recipe(plan, 'development-validation', ordinal, kind).validate()
    return plan


def load_plan(path=DEFAULT_PLAN):
    plan = json.loads(Path(path).read_text())
    if is_alibaba_plan(plan):
        if (plan != json.loads(ALIBABA_PLAN.read_bytes()) or plan['source_profile'] != SOURCE_PROFILE_V2
            or plan['fitting_allowed'] is not False or plan['sealed_final_capture_allowed'] is not False
            or plan['previous_capture_promotion_allowed'] is not False):
            raise ValueError('ADR-016 acquisition requires the committed evaluation-only plan')
        return plan
    if is_detector_plan(plan):
        return _load_detector_plan(plan)
    policy = load_training_policy()
    require_current_training_source('local_http', policy)
    if (plan.get('plan_version') != 'local-http-prospective-plan/v1'
        or plan.get('policy_sha256') != policy_hash(policy)
        or plan.get('source_profile') != SOURCE_PROFILE or plan.get('feature_set_version') != FEATURE_SET
        or plan.get('roles_in_order') != list(ROLES) or plan.get('environment') != 'local'
        or plan.get('oracle_protocol') != 'server-completion-client-ledger/v1'
        or plan.get('label_protocol') != 'verified-pre-slo-300s/v1'
        or plan.get('sealed_final_capture_allowed') is not False
        or plan.get('previous_capture_promotion_allowed') is not False):
        raise ValueError('prospective plan differs from approved source/roles/oracle protocol')
    for role in ROLES:
        if (type(plan['minimum_groups'][role]) is not int or type(plan['maximum_groups'][role]) is not int
            or not 1 <= plan['minimum_groups'][role] <= plan['maximum_groups'][role] <= 20):
            raise ValueError('invalid bounded acquisition group plan')
    if any(plan['minimum_groups'][r] < n for r, n in (('train', 3), ('calibration', 2), ('development-validation', 5), ('sealed-final', 5))):
        raise ValueError('prospective group plan weakens the accepted minimum')
    # Validate every actual runner recipe before any Docker/source access.
    for role in ROLES[:-1]:
        for kind in KINDS:
            recipe(plan, role, 1, kind).validate()
    return plan


def group_name(plan, role, ordinal):
    if is_detector_plan(plan) or is_alibaba_plan(plan):
        if (role not in DETECTOR_ROLES or type(ordinal) is not int
            or not 1 <= ordinal <= plan['maximum_groups'][role]):
            raise ValueError('unregistered detector-event role/group ordinal')
        return f"{plan['group_prefix']}-{ordinal:02d}"
    if role not in ROLES or type(ordinal) is not int or not 1 <= ordinal <= plan['maximum_groups'][role]:
        raise ValueError('unregistered acquisition role/group ordinal')
    if role == 'sealed-final':
        raise ValueError('sealed-final capture/access requires a later owner-authorized final protocol')
    short = {'train': 'train', 'calibration': 'cal', 'development-validation': 'dev'}[role]
    return f'{short}-{ordinal:02d}'


def recipe(plan, role, ordinal, kind):
    group_name(plan, role, ordinal)
    if is_detector_plan(plan) or is_alibaba_plan(plan):
        kinds = plan_kinds(plan)
        if kind not in kinds:
            raise ValueError('unregistered detector-event capture kind')
        healthy = kind == 'healthy'
        gradual = kind.endswith('-gradual')
        mode = plan['abrupt_fault_mode_by_ordinal_parity']['odd' if ordinal % 2 else 'even']
        return ExperimentConfig('S0' if healthy else 'S3' if gradual else 'S1', 'validation',
            seed=plan['seed_base'] + ordinal * 10 + kinds.index(kind), rps=plan['entry_rps'],
            warmup_seconds=plan['warmup_seconds'],
            active_seconds=(plan['healthy_active_seconds'][role] if healthy
                            else plan['gradual_active_seconds'] if gradual else plan['abrupt_active_seconds']),
            recovery_seconds=plan['recovery_seconds'], fault_mode='latency' if healthy or gradual else mode,
            maximum_delay_ms=plan['maximum_delay_ms'],
            target_service='inventory-service' if kind.startswith('inventory-') else 'payment-service',
            all_entry_traffic=True)
    if kind not in KINDS:
        raise ValueError('unregistered prospective capture kind')
    offset = ROLES.index(role) * 1000 + ordinal * 10 + KINDS.index(kind)
    return ExperimentConfig('S0' if kind == 'healthy' else 'S3', 'train' if role == 'train' else 'validation',
        seed=plan['seed_base'] + offset, rps=plan['entry_rps'], warmup_seconds=plan['warmup_seconds'],
        active_seconds=plan['healthy_active_seconds'][role] if kind == 'healthy' else plan['gradual_active_seconds'],
        recovery_seconds=plan['recovery_seconds'], maximum_delay_ms=plan['maximum_delay_ms'],
        target_service='inventory-service' if kind == 'inventory-gradual' else 'payment-service', all_entry_traffic=True)


def clean_source(project):
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=project).strip():
        raise ValueError('commit prospective source/plan before registration or traffic')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()


def read_binding(store, path):
    # Bindings are hard links to immutable content-addressed objects. Verify both
    # names on each read, rather than trusting arbitrary JSON at a familiar path.
    if path.is_symlink():
        raise ValueError('prospective binding cannot be a symlink')
    value = json.loads(path.read_bytes())
    reference = 'artifact://manifests/' + stable_id(value) + '.json'
    if store.read(reference) != value:
        raise ValueError('prospective binding differs from immutable manifest')
    return reference, value


def bind(store, path, value):
    reference = store.write('manifests', value)
    path.parent.mkdir(parents=True, exist_ok=True)
    source = store.root / reference.removeprefix('artifact://')
    os.link(source, path)  # exclusive and atomic, never overwrite/re-admit
    return reference


def read_alibaba_epoch(store, plan):
    try:
        reference, value = read_binding(store, store.root / ALIBABA_BINDING)
    except FileNotFoundError as exc:
        raise ValueError('ADR-016 requires a candidate pinned before capture') from exc
    if (set(value) != {'epoch_version', 'plan_sha256', 'source_commit', 'model_version',
                       'manifest_sha256', 'registered_at'}
        or value['epoch_version'] != 'alibaba-local-epoch/v1' or value['plan_sha256'] != stable_id(plan)
        or not all(isinstance(value[k], str) and re.fullmatch(f'[0-9a-f]{{{length}}}', value[k])
                   for k, length in (('source_commit', 40), ('model_version', 64), ('manifest_sha256', 64)))):
        raise ValueError('ADR-016 epoch identity differs from its pinned plan or candidate')
    utc_time(value['registered_at'])
    return reference, value


@contextmanager
def acquisition_lock(store):
    """One workload or freeze per corpus; an interrupted process releases it."""
    store.root.mkdir(parents=True, exist_ok=True)
    with (store.root / '.capture.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('another acquisition/freeze owns this corpus') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def configure_compose(project, root, group):
    name = 'sentinelops-corpus-' + group
    directory = root / 'contexts' / group
    directory.mkdir(parents=True, exist_ok=True)
    override = directory / 'ports.yml'
    body = 'services:\n  postgres:\n    ports: !override\n      - "127.0.0.1:55435:5432"\n'
    if override.exists() and override.read_text() != body:
        raise ValueError('prospective Compose override changed')
    if not override.exists():
        with override.open('x') as stream:
            stream.write(body)
    os.environ['COMPOSE_PROJECT_NAME'] = name
    os.environ['COMPOSE_FILE'] = str(project / 'docker-compose.yml') + ':' + str(override)
    os.environ['DATABASE_URL'] = 'postgresql://sentinel:sentinel@127.0.0.1:55435/sentinelops'
    return name


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=180)


def docker_clock(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Docker source creation clock lacks an explicit timezone')
    return timestamp(parsed.astimezone(timezone.utc))


def volume_created_within(value, lower, upper):
    # Docker volume CreatedAt has whole-second precision. Its creation can be
    # later within that second than the provisioning intent. Keep the upper
    # bound strict and leave container clocks and preexisting-resource checks
    # unchanged; no source timestamp is rewritten.
    point = utc_time(value)
    earliest = lower.replace(microsecond=0) if point.microsecond == 0 else lower
    return earliest <= point <= upper


def context_snapshot(project, compose_project):
    identifiers = docker('compose', 'ps', '-q').split()
    if not identifiers:
        raise ValueError('owned prospective testbed is not running')
    containers = json.loads(docker('inspect', *identifiers))
    values = {}
    for item in containers:
        labels = item['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.project') != compose_project or not item['State']['Running']:
            raise ValueError('prospective context includes an unowned/stopped container')
        service = labels['com.docker.compose.service']
        values[service] = {'container_id': item['Id'], 'image': item['Image'],
                           'created_at': docker_clock(item['Created']), 'created_at_source': item['Created']}
    if set(values) != set(SOURCE_SERVICES):
        raise ValueError('prospective context omits or adds a source service')
    config = json.loads(docker('compose', 'config', '--format', 'json'))
    mounts = next(c for c in containers if c['Config']['Labels']['com.docker.compose.service'] == 'postgres')['Mounts']
    volumes = [m['Name'] for m in mounts if m.get('Type') == 'volume' and m['Destination'] == '/var/lib/postgresql/data']
    if len(volumes) != 1:
        raise ValueError('prospective PostgreSQL requires one owned fresh volume')
    volume = json.loads(docker('volume', 'inspect', volumes[0]))[0]
    if volume.get('Labels', {}).get('com.docker.compose.project') != compose_project:
        raise ValueError('prospective database volume belongs to another project')
    return {'containers': values, 'compose_config_sha256': stable_id(config),
            'database_volume': {'name': volume['Name'], 'created_at': docker_clock(volume['CreatedAt']),
                                'created_at_source': volume['CreatedAt']},
            'host_boot_sha256': stable_id(Path('/proc/sys/kernel/random/boot_id').read_text().strip()),
            'images': image_identity(project)}


def registration_path(store, group):
    return store.root / 'sessions' / group / 'registration.json'


def registered_sessions(store):
    return [read_binding(store, path)[1] for path in sorted((store.root / 'sessions').glob('*/registration.json'))]


def require_role_order(store, plan, role):
    sessions = registered_sessions(store)
    if is_detector_plan(plan) or is_alibaba_plan(plan):
        # One evaluation-only epoch per store; never mixed with fitting groups.
        if any(s['plan_sha256'] != stable_id(plan) for s in sessions):
            raise ValueError('detector-event epoch cannot share a store with another plan')
        if role not in DETECTOR_ROLES:
            raise ValueError('detector-event epoch has only a development-validation role')
        return
    if any(s['group_id'].startswith(('det-', 'ali-', 'alb-')) for s in sessions):
        raise ValueError('fitting corpus cannot share a store with a detector-event epoch')
    index = ROLES.index(role)
    if any(ROLES.index(s['role']) > index for s in sessions):
        raise ValueError('cannot acquire earlier-role data after a later role has begun')
    if index:
        from sentinelops.experiments.local_recovery import read_abandonment
        earlier = [s for s in sessions if s['role'] == ROLES[index-1]
                   and read_abandonment(store, s, plan) is None]
        if len(earlier) < plan['minimum_groups'][ROLES[index-1]]:
            raise ValueError('previous acquisition role has insufficient registered groups')
        for session in earlier:
            for kind in KINDS:
                path = store.root / 'runs' / (session['group_id'] + '-' + kind) / 'result.json'
                if not path.exists() or read_binding(store, path)[1]['status'] != 'completed':
                    raise ValueError('previous acquisition role has incomplete/failed source slots')


def close_completed_sessions(store, *, except_group, plan=None):
    plan = load_plan() if plan is None else plan
    for session in registered_sessions(store):
        group = session['group_id']
        if group == except_group:
            continue
        closure = store.root / 'sessions' / group / 'closed.json'
        if closure.exists():
            from sentinelops.experiments.local_recovery import read_abandonment
            read_abandonment(store, session, plan)
            continue
        for kind in plan_kinds(plan):
            result = store.root / 'runs' / (group + '-' + kind) / 'result.json'
            if not result.exists() or read_binding(store, result)[1]['status'] != 'completed':
                raise ValueError('finish the active acquisition group before preparing another context')
        expected_project = 'sentinelops-corpus-' + group
        if session['compose_project'] != expected_project:
            raise ValueError('registered acquisition project identity differs')
        ids = [v['container_id'] for v in session['context']['containers'].values()]
        current = json.loads(docker('inspect', *ids))
        if any(c['Config'].get('Labels', {}).get('com.docker.compose.project') != expected_project for c in current):
            raise ValueError('refuse to stop an unowned acquisition context')
        subprocess.run(['docker', 'stop', *ids], check=True, timeout=180)
        bind(store, closure, {'closure_version': 'local-http-session-closed/v1', 'group_id': group,
                             'closed_at': timestamp(datetime.now(timezone.utc)), 'volumes_and_source_preserved': True})


def register_session(store, plan, *, role, ordinal, project=PROJECT, prepare=False):
    group = group_name(plan, role, ordinal)  # reject final before source/context reads
    if (store.root / 'dataset-freeze.json').exists() or (store.root / 'evaluation-freeze.json').exists():
        raise ValueError('prospective dataset is frozen; new acquisition needs a new corpus epoch')
    source = clean_source(project)
    epoch_ref = None
    if is_alibaba_plan(plan):
        epoch_ref, epoch = read_alibaba_epoch(store, plan)
        if epoch['source_commit'] != source:
            raise ValueError('ADR-016 capture differs from the preregistered plan and source')
    require_role_order(store, plan, role)
    path = registration_path(store, group)
    if (path.parent / 'closed.json').exists():
        raise ValueError('closed acquisition group cannot receive new traffic')
    compose_project = configure_compose(project, store.root, group)
    if path.exists():
        reference, saved = read_binding(store, path)
        if (saved['plan_sha256'] != stable_id(plan) or saved['source_commit'] != source
            or saved.get('evaluation_epoch_ref') != epoch_ref):
            raise ValueError('session is frozen to another plan/source checkout')
        current = context_snapshot(project, compose_project)
        if current != saved['context']:
            raise ValueError('prospective source context changed within an acquisition group')
        return reference, saved
    if not prepare:
        raise ValueError('new acquisition session needs explicit --prepare before traffic')
    close_completed_sessions(store, except_group=group, plan=plan)
    # Persist provisioning intent before Docker creation. A retry can resume its
    # own partial startup, but cannot appropriate an older project or volume.
    intent_path = store.root / 'contexts' / group / 'provisioning.json'
    if intent_path.exists():
        _, intent = read_binding(store, intent_path)
        if (intent['source_commit'] != source or intent['plan_sha256'] != stable_id(plan)
            or intent['compose_project'] != compose_project):
            raise ValueError('partial prospective startup belongs to another source/plan')
        created = utc_time(intent['created_at'])
    else:
        if docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + compose_project).strip():
            raise ValueError('unregistered prospective project already exists; preserve and investigate it')
        if docker('volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + compose_project).strip():
            raise ValueError('unregistered prospective volume already exists; no automatic deletion')
        created = datetime.now(timezone.utc)
        bind(store, intent_path, {'provisioning_version': 'local-http-context/v1', 'source_commit': source,
                                 'plan_sha256': stable_id(plan), 'compose_project': compose_project,
                                 'created_at': timestamp(created)})
    # Start only the registered source services. Operator consoles such as
    # Grafana are not acquisition sources and would change the context scope.
    subprocess.run(['docker', 'compose', 'up', '--build', '-d', '--wait', '--wait-timeout', '180', *SOURCE_SERVICES],
                   cwd=project, check=True, timeout=600)
    context = context_snapshot(project, compose_project)
    inspected_at = datetime.now(timezone.utc)
    clocks = [utc_time(v['created_at']) for v in context['containers'].values()]
    if (any(not created <= point <= inspected_at for point in clocks)
        or not volume_created_within(context['database_volume']['created_at'], created, inspected_at)):
        raise ValueError('prospective source creation clocks escape the registered provisioning interval')
    # Read the actual initial state before any workload. Starts/IDs alone do not
    # certify independence; the verifier additionally keeps each whole group.
    initial = docker('compose', 'exec', '-T', 'postgres', 'psql', '-U', 'sentinel', '-d', 'sentinelops',
                     '-At', '-v', 'ON_ERROR_STOP=1', '-c', 'SELECT count(*) FROM payments;').strip()
    if initial != '0':
        raise ValueError('prospective source is not a fresh payment database')
    plan_ref = store.write('manifests', plan)
    value = {'registration_version': 'local-http-session/v1', 'policy_sha256': plan['policy_sha256'],
        'plan_ref': plan_ref, 'plan_sha256': stable_id(plan), 'role': role, 'ordinal': ordinal, 'group_id': group,
        'source_commit': source, 'registered_at': timestamp(datetime.now(timezone.utc)),
        'context_created_after': timestamp(created), 'compose_project': compose_project,
        'context': context, 'initial_payment_rows': 0, 'fitting_allowed_by_registration': False}
    if epoch_ref is not None:
        value['evaluation_epoch_ref'] = epoch_ref
    return bind(store, path, value), value


async def capture_slot(store, plan, *, role, ordinal, kind, project=PROJECT, prepare=False):
    with acquisition_lock(store):
        return await _capture_slot(store, plan, role=role, ordinal=ordinal, kind=kind, project=project, prepare=prepare)


async def _capture_slot(store, plan, *, role, ordinal, kind, project=PROJECT, prepare=False):
    config = recipe(plan, role, ordinal, kind)
    reference, registration = register_session(store, plan, role=role, ordinal=ordinal, project=project, prepare=prepare)
    slot = registration['group_id'] + '-' + kind
    root = store.root / 'runs' / slot
    root.mkdir(parents=True, exist_ok=False)
    value = {'capture_registration_version': 'local-http-capture/v1', 'session_ref': reference, 'slot_id': slot,
             'registered_at': timestamp(datetime.now(timezone.utc)), 'source_commit': registration['source_commit'],
             'recipe': asdict(config), 'policy_sha256': plan['policy_sha256']}
    slot_ref = bind(store, root / 'registration.json', value)
    # A completed old experiment cannot be supplied here. The runner generates a
    # new run after this registration and records its source/clock/images.
    options = {'source_profile': SOURCE_PROFILE_V2} if is_alibaba_plan(plan) else {}
    captured = await execute(config, project=project, output=root / 'captures',
                             session_id=registration['group_id'], **options)
    run = json.loads((captured / 'run.json').read_text())
    if (run['source_commit'] != registration['source_commit'] or run['image_digests'] != registration['context']['images']
        or utc_time(run['started_at']) < utc_time(value['registered_at'])):
        raise ValueError('captured run differs from prospective source/registration')
    receipt = {'capture_result_version': 'local-http-capture-result/v1', 'registration_ref': slot_ref,
               'run_directory': str(captured.relative_to(store.root)), 'run_sha256': stable_id(run),
               'completed_at': timestamp(datetime.now(timezone.utc)), 'status': run['status'],
               'health_or_precursor_certified': False}
    bind(store, root / 'result.json', receipt)
    return receipt


def validate_end_context(session, observation, run_end):
    if (observation.get('kind') != 'local-corpus-context-inspection/v1'
        or observation.get('compose_project') != session['compose_project']
        or set(observation.get('containers', {})) != set(session['context']['containers'])):
        raise ValueError('end-context observation has another source identity/scope')
    if utc_time(observation['collected_at']) < run_end:
        raise ValueError('end-context observation precedes workload completion')
    for service, expected in session['context']['containers'].items():
        item = observation['containers'][service]
        for field in ('created_at','started_at','finished_at'):
            if item[field] != docker_clock(item[field + '_source']):
                raise ValueError('end-context source clock conversion differs')
        if (item['container_id'] != expected['container_id'] or item['image'] != expected['image']
            or utc_time(item['created_at']) != utc_time(expected['created_at'])
            or type(item.get('restart_count')) is not int or item['restart_count'] < 0
            or type(item.get('running')) is not bool):
            raise ValueError('end-context container identity/state differs')
        if not utc_time(expected['created_at']) <= utc_time(item['started_at']) <= utc_time(session['registered_at']):
            raise ValueError('container process restarted after prospective registration')
        if not item['running'] and utc_time(item['finished_at']) < run_end:
            raise ValueError('source container stopped before workload completion')


def inspect_completed_capture(store, slot, *, project=PROJECT):
    """Capture a real post-run Docker observation; never infer a past receipt.

    This observer also supports early capture-protocol v1 runs: current Docker
    process start clocks must prove no restart after their registration. Missing
    resources or clock/state evidence fails rather than minting a certificate.
    """
    if not SLOT_PATTERN.fullmatch(slot):
        raise ValueError('end-context observer rejects unregistered/sealed source slots')
    source = clean_source(project)
    root = store.root / 'runs' / slot
    result_ref, result = read_binding(store, root/'result.json')
    _, registration = read_binding(store, root/'registration.json')
    session = store.read(registration['session_ref'])
    if slot.removeprefix(session['group_id'] + '-') not in plan_kinds(store.read(session['plan_ref'])):
        raise ValueError('end-context slot kind differs from its registered plan')
    if result['status'] != 'completed':
        raise ValueError('cannot observe a completed context for unfinished source evidence')
    from pathlib import PurePosixPath
    parts = PurePosixPath(result['run_directory']).parts
    if len(parts) != 4 or parts[:3] != ('runs',slot,'captures') or not re.fullmatch('run-[0-9a-f]{32}',parts[3]):
        raise ValueError('end-context source path differs from its registered slot')
    captured = store.root.joinpath(*parts)
    if any(p.is_symlink() for p in (root, root/'captures', captured)):
        raise ValueError('end-context observer refuses source symlinks')
    run = json.loads((captured/'run.json').read_bytes())
    if stable_id(run) != result['run_sha256']:
        raise ValueError('end-context source run changed after capture')
    previous = root/'end-context.json'
    if previous.exists():
        reference, receipt = read_binding(store, previous)
        if (receipt.get('context_receipt_version') != 'local-http-context-end/v1'
            or receipt.get('source_result_ref') != result_ref or receipt.get('session_ref') != registration['session_ref']):
            raise ValueError('stored end-context receipt belongs to another source')
        validate_end_context(session, store.read(receipt['inspection_ref']), utc_time(run['ended_at']))
        return reference
    ids = [item['container_id'] for item in session['context']['containers'].values()]
    actual = json.loads(docker('inspect', *ids))
    projected = {}
    for item in actual:
        labels = item['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.project') != session['compose_project']:
            raise ValueError('end-context observer encountered an unowned source')
        service = labels['com.docker.compose.service']
        if service in projected:
            raise ValueError('end-context observer duplicated a source service')
        projected[service] = {'container_id':item['Id'],'image':item['Image'],
            'created_at':docker_clock(item['Created']), 'started_at':docker_clock(item['State']['StartedAt']),
            'finished_at':docker_clock(item['State']['FinishedAt']), 'running':item['State']['Running'],
            'created_at_source':item['Created'], 'started_at_source':item['State']['StartedAt'],
            'finished_at_source':item['State']['FinishedAt'],
            'restart_count':item['RestartCount']}
    import hashlib
    if clean_source(project) != source:
        raise ValueError('context observer source changed during inspection')
    observed = {'kind':'local-corpus-context-inspection/v1','compose_project':session['compose_project'],
                'containers':projected,'collected_at':timestamp(datetime.now(timezone.utc)),'observer_source_commit':source}
    observed['observer_code_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    validate_end_context(session, observed, utc_time(run['ended_at']))
    raw_ref = store.write('raw', observed)
    return bind(store, root/'end-context.json', {'context_receipt_version':'local-http-context-end/v1',
        'source_result_ref':result_ref,'session_ref':registration['session_ref'],'inspection_ref':raw_ref})
