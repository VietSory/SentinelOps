"""Collect image evidence for operator review; never approve an image or action.

Matching a health stream to a physical Kubernetes workload remains a separate
review. Equal service names and healthy SLO buckets cannot establish that binding.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import logging

from opentelemetry import metrics

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.remediation.domain import Blocked, Policy, fresh
from sentinelops.remediation.registry import deployment_from_projection
from sentinelops.remediation.verification import validate_current_support, verify_post_action_health
from sentinelops.telemetry.core import stable_id, timestamp

logger = logging.getLogger('sentinelops-image-review')


async def require_evidence_reader(pool):
    """Actual privileges, not a role name or read-only connection flag."""
    async with pool.acquire() as conn:
        unsafe = await conn.fetchval("""SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls
            OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname IN
                ('sentinel_control','sentinel_remediation') AND
                (pg_has_role(current_user,n.nspowner,'MEMBER') OR has_schema_privilege(n.oid,'CREATE')))
            FROM pg_roles WHERE rolname=current_user""")
        writes = await conn.fetchval("""SELECT coalesce(bool_or(
            has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
            OR has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES')),false)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('sentinel_control','sentinel_remediation') AND c.relkind IN ('r','p')""")
        functions = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN
            ('sentinel_control','sentinel_remediation') AND p.prosecdef AND has_function_privilege(p.oid,'EXECUTE'))""")
        reads = await conn.fetchval("""SELECT bool_and(has_table_privilege(t,'SELECT')) FROM unnest(ARRAY[
            'sentinel_control.health_streams','sentinel_control.health_batches',
            'sentinel_control.health_signals']) AS t""")
        if unsafe or writes or functions or not reads:
            raise Blocked('image-review-requires-select-only-health-credential')


class ImageEvidenceReview:
    def __init__(self, source, kubernetes, store, target, *, environment='local',
                 clock=lambda: datetime.now(timezone.utc)):
        original = source.store.root
        if store.root == original or store.root in original.parents or original in store.root.parents:
            raise ValueError('image evidence store must be separate from original health source')
        self.source, self.kube, self.store = source, kubernetes, store
        self.target, self.environment, self.clock = target, environment, clock
        self.counter = metrics.get_meter('sentinelops-remediation').create_counter(
            'sentinelops_image_review_observations_total')

    def _emit(self, operation, status):
        self.counter.add(1, {'operation': operation, 'status': status})
        logger.info(json.dumps({'event': 'payment_image_review_observed', 'operation': operation, 'status': status}))

    def _ready(self, observation, *, current=True):
        observation.validate(Policy(self.target))
        if current:
            fresh(observation.observed_at, self.clock(), 180)
        if (observation.observed_generation != observation.generation
            or not observation.ready_for(observation.image, prior_generation=0, old_pods=())):
            raise Blocked('image-review-deployment-not-settled')

    @staticmethod
    def _unchanged(before, after):
        if (before.target != after.target or before.generation != after.generation
            or before.container_index != after.container_index or before.image != after.image
            or {(p.uid, p.image_id) for p in before.pods} != {(p.uid, p.image_id) for p in after.pods}):
            raise Blocked('image-review-deployment-changed')

    def _snapshot(self, observation):
        # Same projection/ref kind consumed by the existing KnownGoodRegistry.
        return self.store.write('manifests', {'observation_version': 'payment-deployment/v1',
                                            'observation': observation.projection()})

    async def open(self):
        await self.kube.require_observer()
        before = await self.kube.observe()
        self._ready(before)
        record = {'review_version': 'payment-image-review/v1', 'target': before.projection()['target'],
                  'image': before.image, 'opened_at': before.observed_at, 'before_ref': self._snapshot(before),
                  'health_stream_id': self.source.stream_id, 'health_policy_sha256': stable_id(self.source.policy),
                  'source_root_sha256': stable_id({'root': str(self.source.store.root)}),
                  'environment': self.environment, 'known_good_approved': False,
                  'physical_binding_verified': False, 'action_dispatch_authorized': False}
        validate_payload('payment-image-review.schema.json', record)
        reference = self.store.write('manifests', record)
        self._emit('open', 'REVIEW_OPENED')
        return {'status': 'REVIEW_OPENED', 'review_ref': reference, 'review': record}

    async def complete(self, review_ref):
        record = self.store.read(review_ref)
        validate_payload('payment-image-review.schema.json', record)
        if (record['target'] != asdict(self.target) or record['environment'] != self.environment
            or record['health_stream_id'] != self.source.stream_id
            or record['health_policy_sha256'] != stable_id(self.source.policy)
            or record['source_root_sha256'] != stable_id({'root': str(self.source.store.root)})):
            raise Blocked('image-review-source-or-target-mismatch')
        fresh(record['opened_at'], self.clock(), 600)
        before = deployment_from_projection(self.store.read(record['before_ref']))
        self._ready(before, current=False)
        if before.observed_at != record['opened_at'] or before.image != record['image']:
            raise Blocked('image-review-opening-evidence-mismatch')
        await self.kube.require_observer()
        current = await self.kube.observe()
        self._ready(current)
        self._unchanged(before, current)
        cutoff = self.clock()
        verdict = await verify_post_action_health(self.source, effective_at=utc_time(record['opened_at']),
            as_of=cutoff, expected_environment=self.environment, max_batches=10000, require_current=True)
        if verdict['status'] != 'telemetry_verified':
            self._emit('complete', verdict['status'].upper())
            return {'status': verdict['status'].upper(), 'reason': verdict['reason'], 'review_ref': review_ref,
                    'known_good_approved': False, 'action_dispatch_authorized': False}
        ends = validate_current_support(verdict, source=self.source, environment=self.environment,
                                        effective_at=record['opened_at'], as_of=cutoff)
        after = await self.kube.observe()
        self._ready(after)
        self._unchanged(before, after)
        completed = self.clock()
        fresh(record['opened_at'], completed, 600)
        if not cutoff <= utc_time(after.observed_at) <= completed:
            raise Blocked('image-review-final-observation-clock-mismatch')
        expected = datetime.fromtimestamp(int((completed - timedelta(seconds=30)).timestamp()) // 60 * 60, timezone.utc)
        if ends[-1] != expected:
            self._emit('complete', 'PENDING')
            return {'status': 'PENDING', 'reason': 'health-watermark-advanced', 'review_ref': review_ref,
                    'known_good_approved': False, 'action_dispatch_authorized': False}
        evidence = {'evidence_version': 'payment-image-evidence/v1', 'review_ref': review_ref,
                    'target': record['target'], 'image': record['image'], 'environment': self.environment,
                    'health_stream_id': self.source.stream_id, 'health_policy_sha256': record['health_policy_sha256'],
                    'opened_at': record['opened_at'], 'completed_at': timestamp(completed),
                    'before_ref': record['before_ref'], 'after_ref': self._snapshot(after),
                    'health_ref': self.store.write('manifests', verdict), 'known_good_approved': False,
                    'physical_binding_verified': False, 'action_dispatch_authorized': False}
        validate_payload('payment-image-evidence.schema.json', evidence)
        ref = self.store.write('manifests', evidence)
        self._emit('complete', 'AWAITING_OPERATOR_REVIEW')
        return {'status': 'AWAITING_OPERATOR_REVIEW', 'evidence_ref': ref, 'evidence': evidence}
