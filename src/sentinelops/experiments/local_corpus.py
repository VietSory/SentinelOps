"""Prospective local capture registration under owner-approved ADR-014.

The original experiment runner supplies raw metrics, logs, traces and a separate
request-outcome oracle. This layer fixes role/group/config/source identity before
traffic and never certifies health from a registration or completed-run flag.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

from sentinelops.contracts import utc_time
from sentinelops.experiments.runner import ExperimentConfig, execute, image_identity
from sentinelops.public_data.policy import load_training_policy, policy_hash, require_current_training_source
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_SET, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE
from sentinelops.telemetry.store import ArtifactStore

ROLES = ('train', 'calibration', 'development-validation', 'sealed-final')
KINDS = ('healthy', 'payment-gradual', 'inventory-gradual')
PROJECT = Path(__file__).resolve().parents[3]
DEFAULT_PLAN = PROJECT / 'configs/local_http_capture_plan.json'


def load_plan(path=DEFAULT_PLAN):
    plan = json.loads(Path(path).read_text())
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
    if role not in ROLES or type(ordinal) is not int or not 1 <= ordinal <= plan['maximum_groups'][role]:
        raise ValueError('unregistered acquisition role/group ordinal')
    if role == 'sealed-final':
        raise ValueError('sealed-final capture/access requires a later owner-authorized final protocol')
    short = {'train': 'train', 'calibration': 'cal', 'development-validation': 'dev'}[role]
    return f'{short}-{ordinal:02d}'


def recipe(plan, role, ordinal, kind):
    group_name(plan, role, ordinal)
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
    required = set(BUSINESS_ROUTES) | {'postgres', 'prometheus', 'otel-collector', 'jaeger'}
    if set(values) != required:
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
    index = ROLES.index(role)
    if any(ROLES.index(s['role']) > index for s in sessions):
        raise ValueError('cannot acquire earlier-role data after a later role has begun')
    if index:
        earlier = [s for s in sessions if s['role'] == ROLES[index-1]]
        if len(earlier) < plan['minimum_groups'][ROLES[index-1]]:
            raise ValueError('previous acquisition role has insufficient registered groups')
        for session in earlier:
            for kind in KINDS:
                path = store.root / 'runs' / (session['group_id'] + '-' + kind) / 'result.json'
                if not path.exists() or read_binding(store, path)[1]['status'] != 'completed':
                    raise ValueError('previous acquisition role has incomplete/failed source slots')


def close_completed_sessions(store, *, except_group):
    for session in registered_sessions(store):
        group = session['group_id']
        if group == except_group:
            continue
        closure = store.root / 'sessions' / group / 'closed.json'
        if closure.exists():
            read_binding(store, closure)
            continue
        for kind in KINDS:
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
    source = clean_source(project)
    require_role_order(store, plan, role)
    path = registration_path(store, group)
    compose_project = configure_compose(project, store.root, group)
    if path.exists():
        reference, saved = read_binding(store, path)
        if saved['plan_sha256'] != stable_id(plan) or saved['source_commit'] != source:
            raise ValueError('session is frozen to another plan/source checkout')
        current = context_snapshot(project, compose_project)
        if current != saved['context']:
            raise ValueError('prospective source context changed within an acquisition group')
        return reference, saved
    if not prepare:
        raise ValueError('new acquisition session needs explicit --prepare before traffic')
    close_completed_sessions(store, except_group=group)
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
    subprocess.run(['docker', 'compose', 'up', '--build', '-d', '--wait', '--wait-timeout', '180'],
                   cwd=project, check=True, timeout=600)
    context = context_snapshot(project, compose_project)
    inspected_at = datetime.now(timezone.utc)
    clocks = [utc_time(v['created_at']) for v in context['containers'].values()]
    clocks.append(utc_time(context['database_volume']['created_at']))
    if any(not created <= point <= inspected_at for point in clocks):
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
    return bind(store, path, value), value


async def capture_slot(store, plan, *, role, ordinal, kind, project=PROJECT, prepare=False):
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
    captured = await execute(config, project=project, output=root / 'captures', session_id=registration['group_id'])
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
