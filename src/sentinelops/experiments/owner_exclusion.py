"""Owner exclusion of one incident-hit acquisition group; never retry, repair or relabel it.

An incident the frozen recovery dispositions do not name (2026-11-11: the host slept
for 7 h 48 min inside alb-04-inventory-abrupt) leaves an epoch unable to finish. The
owner may exclude that whole group once, with a written decision. The group keeps
every source byte, its owned containers are stopped (never removed), and a new ordinal
beyond the reserved minimum replaces it. The decision alone is not enough: a slot that
did not verify must carry an independent host-pause witness in its original client
ledger (consecutive request starts more than PAUSE_SECONDS apart, against a scheduled
spacing of about a second). A group whose every slot verifies, or whose failure has no
such witness, cannot be excluded, so the action cannot drop data based on outcomes.

The commit that records the exclusion becomes the epoch's successor capture source: it
must descend from the epoch source, and replacement groups are captured from it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import subprocess

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import (
    ROLES, PROJECT, acquisition_lock, bind, clean_source, docker, group_name, plan_kinds,
    read_alibaba_epoch, read_binding, registered_sessions, registration_path,
)
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

VERSION = 'local-http-owner-exclusion/v1'
REASON = 'owner-excluded-incident'
PAUSE_SECONDS = 300  # the client schedules a request about every second; a pause is unmistakable


def pause_witness(store, slot):
    """Re-read the slot's original client ledger; report start gaps longer than PAUSE_SECONDS."""
    paths = list((store.root / 'runs' / slot / 'captures').glob('run-*/run.json'))
    if len(paths) != 1 or not re.fullmatch('run-[0-9a-f]{32}', paths[0].parent.name):
        return None
    run = json.loads(paths[0].read_bytes())
    outcomes = ArtifactStore(paths[0].parent / 'truth').read(run['request_outcomes_ref'])['outcomes']
    starts = sorted(utc_time(row['started_at']) for row in outcomes)
    gaps = [{'after': timestamp(a), 'seconds': round((b - a).total_seconds(), 3)}
            for a, b in zip(starts, starts[1:]) if (b - a).total_seconds() > PAUSE_SECONDS]
    return {'run_ref': 'artifact://manifests/' + stable_id(run) + '.json', 'attempts': len(outcomes),
            'pause_seconds': PAUSE_SECONDS, 'gaps': gaps} if gaps else None


def _witnesses(store, status):
    found = {slot: pause_witness(store, slot) for slot, value in status.items() if value != 'SOURCE_VERIFIED'}
    return {slot: value for slot, value in found.items() if value}


def _slot_status(store, plan, session, project, as_of):
    from sentinelops.ml.local_data import verify_capture
    status = {}
    for kind in plan_kinds(plan):
        slot = session['group_id'] + '-' + kind
        if not (store.root / 'runs' / slot / 'result.json').exists():
            status[slot] = 'INCOMPLETE'
            continue
        try:
            verify_capture(store, plan, session, kind, project=project, as_of=as_of)
        except ValueError:
            status[slot] = 'FAILED_VERIFICATION'
        else:
            status[slot] = 'SOURCE_VERIFIED'
    return status


def _descends(project, ancestor, commit):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', ancestor, commit], cwd=project).returncode == 0


def _stop_owned_containers(session):
    ids = [item['container_id'] for item in session['context']['containers'].values()]
    for item in json.loads(docker('inspect', *ids)):
        labels = item['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.project') != session['compose_project']:
            raise ValueError('owner exclusion refuses a container outside the group project')
    running = [item['Id'] for item in json.loads(docker('inspect', *ids)) if item['State']['Running']]
    if running:
        docker('stop', *running)


def exclude_group(store, plan, *, role, ordinal, replacement_ordinal, decision, project=PROJECT):
    """Operator action recording an owner decision; the only state change is closed.json and a stop."""
    from sentinelops.experiments.local_campaign import campaign_lock
    from sentinelops.experiments.local_recovery import _inventory, read_abandonment
    from sentinelops.ml.local_data import _session_identity
    if not isinstance(decision, str) or len(decision.strip()) < 20:
        raise ValueError('owner exclusion needs the written owner decision')
    group, replacement = group_name(plan, role, ordinal), group_name(plan, role, replacement_ordinal)
    if replacement_ordinal <= plan['minimum_groups'][role] or group == replacement:
        raise ValueError('replacement needs an unused ordinal outside the reserved minimum')
    with campaign_lock(store), acquisition_lock(store):
        if (store.root / 'dataset-freeze.json').exists() or (store.root / 'evaluation-freeze.json').exists():
            raise ValueError('cannot exclude a group from a frozen epoch')
        source = clean_source(project)
        _, epoch = read_alibaba_epoch(store, plan)
        if source == epoch['source_commit'] or not _descends(project, epoch['source_commit'], source):
            raise ValueError('owner exclusion runs from a descendant of the epoch source')
        _, session = read_binding(store, registration_path(store, group))
        _session_identity(session, plan)
        closed_path = store.root / 'sessions' / group / 'closed.json'
        if session['role'] != role or session['ordinal'] != ordinal or session['group_id'] != group:
            raise ValueError('owner exclusion session differs from the requested group')
        if closed_path.exists() or registration_path(store, replacement).exists():
            raise ValueError('group is already closed or its replacement has begun')
        for other in registered_sessions(store):
            prior = read_abandonment(store, other, plan, project=project)
            if prior and prior[1]['replacement_group_id'] == replacement:
                raise ValueError('replacement is reserved by another excluded group')
            if prior and prior[1].get('closure_version') == VERSION:
                raise ValueError('the epoch already carries an owner exclusion')
        closed_at = datetime.now(timezone.utc)
        status = _slot_status(store, plan, session, project, closed_at)
        if all(value == 'SOURCE_VERIFIED' for value in status.values()):
            raise ValueError('a fully verified group cannot be excluded')
        witnesses = _witnesses(store, status)
        if not witnesses:
            raise ValueError('owner exclusion needs a host-pause witness in an unverified slot')
        before = _inventory(store, group, plan)
        _stop_owned_containers(session)
        if _inventory(store, group, plan) != before or clean_source(project) != source:
            raise ValueError('source changed during owner exclusion')
        closed = {'closure_version': VERSION, 'group_id': group,
                  'session_ref': 'artifact://manifests/' + stable_id(session) + '.json',
                  'plan_sha256': stable_id(plan), 'closed_at': timestamp(closed_at),
                  'reason': REASON, 'owner_decision': decision.strip(), 'slot_status': status,
                  'host_pause_witness': witnesses,
                  'inventory_ref': store.write('manifests', {'inventory_version': VERSION, 'group_id': group,
                                                             'files': before}),
                  'successor_source_commit': source, 'excluded_whole_group': True, 'source_preserved': True,
                  'replacement_ordinal': replacement_ordinal, 'replacement_group_id': replacement}
        ref = bind(store, closed_path, closed)
        return {'closure_ref': ref, **closed, 'models_fitted': 0, 'activation_allowed': False}


def read_owner_exclusion(store, session, plan, closed, reference, *, project=PROJECT, as_of=None):
    from sentinelops.experiments.local_recovery import _inventory
    from sentinelops.ml.local_data import _session_identity
    _session_identity(session, plan)
    decision = as_of or datetime.now(timezone.utc)
    _, epoch = read_alibaba_epoch(store, plan)
    replacement = group_name(plan, session['role'], closed['replacement_ordinal'])
    if (set(closed) != {'closure_version', 'group_id', 'session_ref', 'plan_sha256', 'closed_at', 'reason',
                        'owner_decision', 'slot_status', 'host_pause_witness', 'inventory_ref',
                        'successor_source_commit',
                        'excluded_whole_group', 'source_preserved', 'replacement_ordinal', 'replacement_group_id'}
        or closed['group_id'] != session['group_id']
        or closed['session_ref'] != 'artifact://manifests/' + stable_id(session) + '.json'
        or closed['plan_sha256'] != stable_id(plan) or closed['reason'] != REASON
        or not isinstance(closed['owner_decision'], str) or len(closed['owner_decision']) < 20
        or closed['excluded_whole_group'] is not True or closed['source_preserved'] is not True
        or closed['replacement_ordinal'] <= plan['minimum_groups'][session['role']]
        or replacement == session['group_id'] or closed['replacement_group_id'] != replacement
        or closed['successor_source_commit'] == epoch['source_commit']
        or not _descends(project, epoch['source_commit'], closed['successor_source_commit'])
        or not utc_time(session['registered_at']) <= utc_time(closed['closed_at']) <= decision):
        raise ValueError('owner exclusion differs from the frozen source/role/budget')
    if store.read(closed['inventory_ref']) != {'inventory_version': VERSION, 'group_id': session['group_id'],
                                               'files': _inventory(store, session['group_id'], plan)}:
        raise ValueError('excluded original source changed after the owner decision')
    status = _slot_status(store, plan, session, project, utc_time(closed['closed_at']))
    if status != closed['slot_status'] or all(value == 'SOURCE_VERIFIED' for value in status.values()):
        raise ValueError('owner exclusion slot status differs from the original source')
    if not closed['host_pause_witness'] or _witnesses(store, status) != closed['host_pause_witness']:
        raise ValueError('owner exclusion host-pause witness differs from the original ledger')
    return reference, closed


def epoch_sources(store, plan, *, project=PROJECT):
    """The epoch source, plus the successor named by its single owner exclusion, if any."""
    _, epoch = read_alibaba_epoch(store, plan)
    sources = {epoch['source_commit']}
    for path in sorted((store.root / 'sessions').glob('*/closed.json')):
        _, closed = read_binding(store, path)
        if closed.get('closure_version') == VERSION:
            if not _descends(project, epoch['source_commit'], closed['successor_source_commit']):
                raise ValueError('owner exclusion successor does not descend from the epoch source')
            sources.add(closed['successor_source_commit'])
    if len(sources) > 2:
        raise ValueError('an epoch carries at most one owner exclusion successor')
    return sources
