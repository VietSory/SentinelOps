"""HTTP review of controller artifacts. Only approvals.py may publish decisions.

The mounted store is a trusted operator boundary, never a client-selected path.
This adapter has no executor, Kubernetes client or action-writer connection.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

from fastapi import Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from sentinelops.api_http import ApiError
from sentinelops.contracts import utc_time
from sentinelops.remediation import approvals
from sentinelops.remediation.domain import Blocked, image_digest
from sentinelops.remediation.pod_memory import RUNBOOK as MEMORY_RUNBOOK, recovery_change, review_change, read_state
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

IDENTIFIER = re.compile(r'[0-9a-f]{64}')


def review_images(proposal):
    """Images must be part of the immutable proposal the human is approving."""
    try:
        return {key: image_digest(proposal.get(key)) for key in ('current_image', 'rollback_image')}
    except Blocked:
        return None


class ProposalStore:
    def __init__(self, root):
        self.store = ArtifactStore(root)

    def _ready(self):
        # A missing mount must not silently become another approval store.
        if not self.store.root.is_dir():
            raise OSError('controller volume is unavailable')

    def proposals(self):
        self._ready()
        values = {}
        for path in (self.store.root / 'manifests').glob('*.json'):
            value = self.store.read('artifact://manifests/' + path.name)
            if value.get('proposal_version') != approvals.PROPOSAL_VERSION:
                continue
            identifier = value['proposal_id']
            if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in values:
                raise ValueError('conflicting or invalid proposal')
            values[identifier] = value
        return sorted(values.values(), key=lambda p: (p['created_at'], p['proposal_id']), reverse=True)

    def get(self, identifier):
        self._ready()
        values = [p for p in self.proposals() if p['proposal_id'] == identifier]
        return values[0] if values else None

    def manual_renewable(self, proposal):
        """A finished zero-action parent may authorize one separate recovery."""
        if recovery_change(proposal) is None:
            return False
        identifier = proposal['proposal_id']
        action = read_state(self.store, identifier)
        return bool(action and action.get('phase') in ('expired', 'blocked')
            and action.get('finalized') is True and action.get('dispatches') == 0
            and datetime.now(timezone.utc) >= utc_time(proposal['created_at'])
            and all(approvals.read_slot(self.store, kind, identifier) is None
                    for kind in ('approval', 'used', 'manual-memory-recovery')))

    def pending_manual_child(self, proposal, current_revision):
        """Resume only an interrupted approval of this one persisted child."""
        slot = approvals.read_slot(self.store, 'manual-memory-recovery', proposal['proposal_id'])
        if slot is None:
            return None
        child = self.store.read(slot['proposal_ref'])
        if (child['proposal_id'] != slot['manual_recovery_proposal_id']
            or child.get('manual_recovery_of') != proposal['proposal_id']
            or child.get('origin_proposal_sha256') != stable_id(proposal)
            or child.get('origin_incident_id') != proposal['incident_id']
            or child.get('origin_incident_revision') != current_revision
            or child.get('incident_id') != 'INC-OOM-MANUAL-' + child['proposal_id']
            or child.get('incident_revision') != 1 or recovery_change(child) != recovery_change(proposal)
            or child.get('target') != proposal.get('target')):
            raise ApiError(409, 'proposal_not_pending', 'Persisted recovery origin changed')
        action = read_state(self.store, child['proposal_id'])
        now = datetime.now(timezone.utc)
        if (utc_time(child['created_at']) <= now < utc_time(child['expires_at'])
            and (not action or action.get('phase') == 'pending')
            and all(approvals.read_slot(self.store, kind, child['proposal_id']) is None
                    for kind in ('approval', 'used', 'outcome'))):
            return child
        return None

    def _renew(self, proposal, current_revision):
        # Caller holds a shared-volume flock. Reuse an interrupted publication
        # before creating the one immutable parent->child identity slot.
        if not self.manual_renewable(proposal) or type(current_revision) is not int or current_revision < 1:
            raise ApiError(409, 'proposal_not_pending', 'This parent cannot authorize another recovery')
        identifier = proposal['proposal_id']
        children = [p for p in self.proposals() if p.get('manual_recovery_of') == identifier]
        if len(children) > 1:
            raise ValueError('conflicting recovery children')
        if children:
            child = children[0]
            if (child.get('origin_proposal_sha256') != stable_id(proposal)
                or child.get('origin_incident_revision') != current_revision
                or recovery_change(child) != recovery_change(proposal)):
                raise ApiError(409, 'proposal_not_pending', 'Recovery origin changed; reload before deciding')
        else:
            now = datetime.now(timezone.utc)
            child_id = stable_id({'approval_mode': approvals.MANUAL_MEMORY_RECOVERY,
                'manual_recovery_of': identifier, 'origin_proposal_sha256': stable_id(proposal),
                'origin_incident_revision': current_revision})
            child = {'proposal_version': approvals.PROPOSAL_VERSION, 'proposal_id': child_id,
                'executor_runbook': MEMORY_RUNBOOK, 'runbook_id': 'pod-oom-memory-limit',
                'incident_id': 'INC-OOM-MANUAL-' + child_id, 'incident_revision': 1,
                'created_at': timestamp(now), 'expires_at': timestamp(now + timedelta(minutes=15)),
                'target': deepcopy(proposal['target']), 'current_image': proposal['current_image'],
                'resource_change': deepcopy(proposal['resource_change']),
                'manual_recovery_of': identifier, 'origin_proposal_sha256': stable_id(proposal),
                'origin_proposal_ref': self.store.write('manifests', proposal),
                'origin_incident_id': proposal['incident_id'], 'origin_incident_revision': current_revision,
                'decision': 'oncall', 'symptoms': {'severity': 'high'}, 'demo_mode': True,
                'G6_accepted': False, 'q09_eligible': False}
        child_ref = self.store.write('manifests', child)
        approvals.create_slot(self.store, 'manual-memory-recovery', identifier,
            {'proposal_id': identifier, 'manual_recovery_proposal_id': child['proposal_id'],
             'proposal_ref': child_ref, 'origin_proposal_sha256': stable_id(proposal)})
        return child

    def view(self, proposal):
        identifier = proposal['proposal_id']
        receipt = approvals.read_slot(self.store, 'approval', identifier)
        used = approvals.read_slot(self.store, 'used', identifier)
        outcome = approvals.read_slot(self.store, 'outcome', identifier)
        now = datetime.now(timezone.utc)
        memory = proposal.get('executor_runbook') == MEMORY_RUNBOOK
        action = read_state(self.store, identifier) if memory else None
        status = (action['phase'] if memory and action and action['phase'] != 'pending' else
                  'engineer' if outcome or receipt and receipt['decision'] == 'reject' else
                  'consumed' if used else 'approved' if receipt else
                  'expired' if now >= utc_time(proposal['expires_at']) else
                  'not_yet_valid' if now < utc_time(proposal['created_at']) else 'pending')
        pending_manual = (memory and recovery_change(proposal) is not None and not receipt and not used and not outcome
            and (not action or action['phase'] == 'pending')
            and utc_time(proposal['created_at']) <= now < utc_time(proposal['expires_at']))
        return {'proposal': proposal, 'proposal_sha256': stable_id(proposal),
                'status': status, 'approval': receipt, 'outcome': outcome,
                'review_images': review_images(proposal),
                **({'review_change': review_change(proposal), 'recovery_change': recovery_change(proposal),
                    'manual_recovery_available': bool(pending_manual or self.manual_renewable(proposal)),
                    'memory_action': action,
                    **({'manual_recovery_of': proposal['manual_recovery_of']} if 'manual_recovery_of' in proposal else {})}
                   if memory else {})}

    def decide(self, identifier, expected_hash, actor, decision, reason, approval_mode=None,
               current_revision=None):
        if approval_mode == approvals.MANUAL_MEMORY_RECOVERY:
            import fcntl
            self._ready()
            with (self.store.root / 'manual-memory-recovery.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                return self._decide(identifier, expected_hash, actor, decision, reason,
                                    approval_mode, current_revision)
        return self._decide(identifier, expected_hash, actor, decision, reason, approval_mode, current_revision)

    def _decide(self, identifier, expected_hash, actor, decision, reason, approval_mode, current_revision):
        proposal = self.get(identifier)
        if proposal is None:
            raise ApiError(404, 'proposal_not_found', 'Runbook proposal was not found')
        if stable_id(proposal) != expected_hash:
            raise ApiError(412, 'proposal_changed', 'Reload the proposal before deciding')
        memory = proposal.get('executor_runbook') == MEMORY_RUNBOOK
        manual = approval_mode == approvals.MANUAL_MEMORY_RECOVERY
        if approval_mode is not None and (not manual or not memory or decision != 'approve'):
            raise ApiError(422, 'invalid_approval_mode', 'Manual memory recovery is only a bounded memory approval')
        complete = ((recovery_change(proposal) if manual else review_change(proposal)) is not None
                    if memory else review_images(proposal) is not None)
        if decision == 'approve' and not complete:
            raise ApiError(409, 'proposal_review_incomplete', 'Proposal has no complete pinned change evidence')
        if memory:
            action = read_state(self.store, identifier)
            if action and action['phase'] != 'pending':
                child = self.pending_manual_child(proposal, current_revision) if manual else None
                if child or manual and self.manual_renewable(proposal):
                    proposal = child or self._renew(proposal, current_revision)
                    identifier = proposal['proposal_id']
                else:
                    raise ApiError(409, 'proposal_not_pending', 'Proposal was already handled by the controller')
        try:
            reference = approvals.record_approval(self.store, proposal_id=identifier,
                approved_by=actor, decision=decision, reason=reason, confirm_oncall_approval=True,
                approval_mode=approval_mode)
        except ValueError:
            raise ApiError(409, 'proposal_not_pending', 'Proposal expired, was handled, or already has a decision') from None
        return {**self.view(proposal), 'approval_ref': reference}


def install_routes(app, settings, *, authorize_operator, authorize_decider, require_repository, call):
    store = ProposalStore(settings.runbook_proposal_store) if settings.runbook_proposal_store else None

    async def storage(operation, *args):
        if store is None:
            raise ApiError(503, 'proposal_store_unavailable', 'Controller proposal storage is not configured')
        try:
            return await run_in_threadpool(getattr(store, operation), *args)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ApiError(503, 'proposal_store_unavailable', 'Controller proposal storage could not be verified') from None

    def valid_id(identifier):
        if not IDENTIFIER.fullmatch(identifier):
            raise ApiError(422, 'invalid_proposal_id', 'Proposal ID must be a SHA-256 identity')

    async def enriched(proposal, repo):
        result = await storage('view', proposal)
        snapshot = await call(repo.get(proposal['incident_id'], proposal['incident_revision']))
        current = await call(repo.get(proposal['incident_id']))
        result.update(incident=snapshot['payload'], current_revision=current['revision'])
        if current['revision'] != proposal['incident_revision'] and result['status'] == 'pending':
            result['status'] = 'stale'
            if 'manual_recovery_available' in result:
                result['manual_recovery_available'] = False
        return result

    @app.get('/v1/runbook-proposals')
    async def list_proposals(incident_id: str | None = Query(None, max_length=256),
                             limit: int = Query(50, ge=1, le=100),
                             actor=Depends(authorize_operator), repo=Depends(require_repository)):
        proposals = await storage('proposals')
        selected = [p for p in proposals if incident_id is None or p['incident_id'] == incident_id]
        return {'items': [await enriched(p, repo) for p in selected[:limit]],
                'has_more': len(selected) > limit}

    @app.get('/v1/runbook-proposals/{proposal_id}')
    async def get_proposal(proposal_id: str, response: Response,
                           actor=Depends(authorize_operator), repo=Depends(require_repository)):
        valid_id(proposal_id)
        proposal = await storage('get', proposal_id)
        if proposal is None:
            raise ApiError(404, 'proposal_not_found', 'Runbook proposal was not found')
        result = await enriched(proposal, repo)
        response.headers['ETag'] = '"' + result['proposal_sha256'] + '"'
        return result

    @app.post('/v1/runbook-proposals/{proposal_id}/decision', status_code=201)
    async def decision(proposal_id: str, body: dict, request: Request, response: Response,
                       actor=Depends(authorize_decider), repo=Depends(require_repository)):
        valid_id(proposal_id)
        # The optional mode selects one fixed recovery policy, never patch inputs.
        if (set(body) not in ({'decision', 'reason'}, {'decision', 'reason', 'approval_mode'})
            or body['decision'] not in ('approve', 'reject')
            or not isinstance(body['reason'], str) or not 1 <= len(body['reason'].strip()) <= 2000):
            raise ApiError(422, 'invalid_decision', 'Supply only decision approve/reject and a reason (1–2000 characters)')
        if 'approval_mode' in body and (body['approval_mode'] != approvals.MANUAL_MEMORY_RECOVERY
                                       or body['decision'] != 'approve'):
            raise ApiError(422, 'invalid_approval_mode', 'Unknown or incompatible approval mode')
        expected = request.headers.get('if-match')
        if expected is None:
            raise ApiError(428, 'proposal_hash_required', 'Supply the quoted proposal SHA-256 in If-Match')
        if not re.fullmatch(r'"[0-9a-f]{64}"', expected):
            raise ApiError(422, 'invalid_proposal_hash', 'If-Match must contain one quoted proposal SHA-256')
        proposal = await storage('get', proposal_id)
        if proposal is None:
            raise ApiError(404, 'proposal_not_found', 'Runbook proposal was not found')
        if stable_id(proposal) != expected[1:-1]:
            raise ApiError(412, 'proposal_changed', 'Reload the proposal before deciding')
        current = await call(repo.get(proposal['incident_id']))
        renewable = (body.get('approval_mode') == approvals.MANUAL_MEMORY_RECOVERY
                     and await storage('manual_renewable', proposal))
        resuming = (body.get('approval_mode') == approvals.MANUAL_MEMORY_RECOVERY
                    and await storage('pending_manual_child', proposal, current['revision']))
        if current['revision'] != proposal['incident_revision'] and not (renewable or resuming):
            raise ApiError(409, 'proposal_stale', 'Incident revision changed; reload before deciding')
        # Authenticated identity is server-owned; no approved_by field is accepted.
        result = await storage('decide', proposal_id, expected[1:-1], actor, body['decision'], body['reason'].strip(),
                               body.get('approval_mode'), current['revision'])
        response.headers['ETag'] = '"' + result['proposal_sha256'] + '"'
        return result
