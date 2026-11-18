"""Collect unapproved Payment image evidence; local or explicit in-cluster worker mode."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import re

import asyncpg

from sentinelops.remediation.domain import Blocked, Target
from sentinelops.remediation.image_review import ImageEvidenceReview, require_evidence_reader
from sentinelops.remediation.kubernetes import LocalKubernetes, configured_server
from sentinelops.telemetry.deployment_changes import DeploymentChangeReview
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream
from sentinelops.workers.source_policy import SourcePolicy


class WorkerObserver:
    """Observation-only interface for the explicitly selected payment-rollback SA.

    Unlike the local quarantine reader, this SA can patch Payment. Verify that
    scope, but expose no mutation method to ImageEvidenceReview.
    """
    def __init__(self, kube):
        self._kube = kube

    async def require_observer(self):
        target = self._kube.target
        checks = [('get', target.deployment, True), ('patch', target.deployment, True),
                  ('list', None, False), ('update', target.deployment, False),
                  ('delete', target.deployment, False), ('create', target.deployment, False),
                  ('deletecollection', None, False),
                  ('patch', 'checkout-service', False), ('patch', 'inventory-service', False)]
        for verb, name, expected in checks:
            attributes = {'namespace': target.namespace, 'group': 'apps',
                          'resource': 'deployments', 'verb': verb}
            if name:
                attributes['name'] = name
            response = await self._kube._request('POST', '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
                expected_status=201, json={'apiVersion': 'authorization.k8s.io/v1',
                    'kind': 'SelfSubjectAccessReview', 'spec': {'resourceAttributes': attributes}})
            status = response.get('status', {})
            if status.get('allowed') is not expected or status.get('evaluationError'):
                raise Blocked('image-review-worker-kubernetes-scope-mismatch')

    async def observe(self):
        return await self._kube.observe()


async def require_worker_reader(pool):
    """Worker credential is broader than SELECT-only; keep every session read-only."""
    async with pool.acquire() as conn:
        valid = await conn.fetchval("""SELECT current_user='control_writer'
            AND current_setting('default_transaction_read_only')='on'
            AND NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)
            AND NOT pg_has_role(current_user,'control_owner','MEMBER')
            AND NOT has_schema_privilege(current_user,'sentinel_control','CREATE')
            AND NOT has_schema_privilege(current_user,'sentinel_remediation','USAGE')
            AND (SELECT bool_and(has_table_privilege(t,'SELECT')) FROM unnest(ARRAY[
                'sentinel_control.health_streams','sentinel_control.health_batches',
                'sentinel_control.health_signals']) AS t)
            FROM pg_roles WHERE rolname=current_user""")
        if valid is not True:
            raise Blocked('image-review-worker-database-scope-mismatch')


async def collect(review, *, emit, sleep=asyncio.sleep):
    """Bounded open/complete sequence, below the review's ten minute expiry."""
    opened = await review.open()
    emit(opened)
    for attempt in range(39):
        result = await review.complete(opened['review_ref'])
        emit(result)
        if result['status'] == 'AWAITING_OPERATOR_REVIEW':
            return result
        if result['status'] not in ('PENDING', 'INCONCLUSIVE'):
            raise Blocked('image-review-unexpected-completion-status')
        if attempt < 38:
            await sleep(15)
    raise Blocked('image-review-evidence-timeout')


async def run(args):
    dsn = os.getenv('SENTINELOPS_IMAGE_REVIEW_DATABASE_URL', '')
    worker = args.in_cluster_worker
    options = {}
    environment = 'local'
    if worker:
        if args.operation not in ('open', 'complete', 'collect'):
            raise ValueError('worker review only collects Payment image evidence')
        if args.server != 'https://kubernetes.default.svc':
            raise ValueError('worker review requires the in-cluster Kubernetes endpoint')
        configured_server(args.server)
        environment = os.getenv('SENTINELOPS_ENV', '')
        if environment != 'dev':
            raise ValueError('worker review requires the explicit dev environment')
        if not re.fullmatch(r'[0-9a-f]{64}', args.health_stream) or not args.source_store.is_dir():
            raise ValueError('selected health stream and original source store are required')
        target = Target(**json.loads(args.target.read_text()))
        options = SourcePolicy(name='in-cluster', remote_control_database=dsn,
            control_database_user='control_writer',
            control_database_password_file=Path('/credentials/database-password')).database_options(dsn, worker='health')
    else:
        # The control-plane image does not ship the local reconciliation CLI.
        from scripts.run_remediation_worker import validate_configuration
        target = validate_configuration(args, dsn)
    policy = json.loads(args.health_policy.read_text())
    pool = kube = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=0, max_size=2, command_timeout=5, timeout=5,
                                        server_settings={'default_transaction_read_only': 'on'}, **options)
        await (require_worker_reader(pool) if worker else require_evidence_reader(pool))
        async with pool.acquire() as conn:
            stream = await conn.fetchrow('SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1', args.health_stream)
        if stream is None:
            raise Blocked('selected-health-source-unavailable')
        identity = validate_stream(stream, policy)
        if (identity['environment'] != environment
            or identity['artifact_root_hash'] != stable_id({'root': str(args.source_store.resolve())})):
            raise Blocked('image-review-source-root-or-environment-mismatch')
        source = HealthLedgerReader(pool, ArtifactStore(args.source_store), args.health_stream, policy)
        kube = LocalKubernetes(args.server, target, token_file=args.token_file, ca_file=args.ca_file)
        review_type = DeploymentChangeReview if args.operation in ('open-change', 'complete-change') else ImageEvidenceReview
        review = review_type(source, WorkerObserver(kube) if worker else kube,
                             ArtifactStore(args.review_store), target, environment=environment)
        def emit(result):
            print(json.dumps(result, sort_keys=True), flush=True)
        if args.operation == 'collect':
            await asyncio.wait_for(collect(review, emit=emit), timeout=580)
        else:
            result = await review.open() if args.operation in ('open', 'open-change') else await review.complete(args.review_ref)
            emit(result)
    finally:
        try:
            if kube is not None:
                await kube.close()
        finally:
            if pool is not None:
                await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('health-stream', 'server'):
        parser.add_argument('--' + flag, required=True)
    for flag in ('target', 'source-store', 'review-store', 'token-file', 'ca-file'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--health-policy', type=Path, default=Path('configs/slo_health_policy.json'))
    parser.add_argument('--in-cluster-worker', action='store_true',
                        help='Explicit dev worker DB and payment-rollback credentials; observation only')
    operations = parser.add_subparsers(dest='operation', required=True)
    operations.add_parser('open')
    operations.add_parser('collect', help='Open then poll complete for at most 580 seconds; never approve')
    complete = operations.add_parser('complete')
    complete.add_argument('--review-ref', required=True)
    operations.add_parser('open-change')
    change = operations.add_parser('complete-change')
    change.add_argument('--review-ref', required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    try:
        asyncio.run(run(args))
    except Blocked as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc), 'known_good_approved': False,
                          'action_dispatch_authorized': False}), flush=True)
        raise SystemExit(2) from None
    except Exception as exc:
        logging.error(json.dumps({'event': 'payment_image_review_failed', 'error_type': type(exc).__name__}))
        raise SystemExit('image evidence review failed: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
