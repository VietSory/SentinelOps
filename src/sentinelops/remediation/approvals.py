"""Immutable local on-call receipts with atomic, create-only identity slots.

The shared controller store is operator-controlled. This records explicit human
approval, not an authentication service; only on-call may write this volume.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id, timestamp

PROPOSAL_VERSION = 'runbook-proposal/v1'
APPROVAL_VERSION = 'runbook-approval/v1'
MANUAL_MEMORY_RECOVERY = 'manual_memory_recovery'


def _slot(store, kind, proposal_id):
    if kind not in ('approval', 'used', 'outcome', 'manual-memory-recovery') or not re.fullmatch(r'[0-9a-f]{64}', proposal_id):
        raise ValueError('invalid approval slot')
    return store.root / 'runbook-approvals' / kind / (proposal_id + '.json')


def read_slot(store, kind, proposal_id):
    path = _slot(store, kind, proposal_id)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    value = json.loads(data)
    # Always verify against the content-addressed artifact too.
    ref = 'artifact://manifests/' + stable_id(value) + '.json'
    if store.read(ref) != value:
        raise ValueError('approval slot differs from immutable artifact')
    return value


def create_slot(store, kind, proposal_id, value):
    """Publish one immutable receipt per identity; concurrent writers cannot win twice."""
    path = _slot(store, kind, proposal_id)
    ref = store.write('manifests', value)
    path.parent.mkdir(parents=True, exist_ok=True)
    source = store.root / 'manifests' / ref.rsplit('/', 1)[1]
    try:
        os.link(source, path)
    except FileExistsError:
        raise ValueError(f'{kind} already recorded for proposal') from None
    # ArtifactStore fsyncs the file before publication. Persist the directory
    # entry on Linux as well, so a lost reply cannot reopen a consumed approval.
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return ref


def find_proposal(store, proposal_id):
    _slot(store, 'approval', proposal_id)
    found = []
    for path in sorted((store.root / 'manifests').glob('*.json')):
        value = store.read('artifact://manifests/' + path.name)
        if value.get('proposal_version') == PROPOSAL_VERSION and value.get('proposal_id') == proposal_id:
            found.append(value)
    if len(found) != 1:
        raise ValueError('proposal must resolve uniquely')
    return found[0]


def record_approval(store, *, proposal_id, approved_by, decision, reason='',
                    confirm_oncall_approval=False, now=None, approval_mode=None):
    if (confirm_oncall_approval is not True or not isinstance(approved_by, str)
        or not approved_by.strip() or decision not in ('approve', 'reject')
        or not isinstance(reason, str)):
        raise ValueError('explicit on-call confirmation, identity and decision required')
    now = now if now is not None else datetime.now(timezone.utc)
    proposal = find_proposal(store, proposal_id)
    if approval_mode is not None:
        from sentinelops.remediation.pod_memory import recovery_change
        if (approval_mode != MANUAL_MEMORY_RECOVERY or decision != 'approve'
            or recovery_change(proposal) is None):
            raise ValueError('manual recovery requires the bounded memory approval')
    if not utc_time(proposal['created_at']) <= now < utc_time(proposal['expires_at']):
        raise ValueError('proposal expired or not yet valid')
    if read_slot(store, 'used', proposal_id) or read_slot(store, 'outcome', proposal_id):
        raise ValueError('proposal already handled')
    value = {'approval_version': APPROVAL_VERSION, 'proposal_id': proposal_id,
             'proposal_sha256': stable_id(proposal), 'approved_by': approved_by.strip(),
             'approved_at': timestamp(now), 'decision': decision, 'reason': reason}
    if approval_mode is not None:
        value['approval_mode'] = approval_mode
    return create_slot(store, 'approval', proposal_id, value)
