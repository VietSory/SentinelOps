"""Collect unapproved Payment image evidence using read-only local credentials."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

import asyncpg

from scripts.run_remediation_worker import validate_configuration
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.image_review import ImageEvidenceReview, require_evidence_reader
from sentinelops.remediation.kubernetes import LocalKubernetes
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream


async def run(args):
    dsn = os.getenv('SENTINELOPS_IMAGE_REVIEW_DATABASE_URL', '')
    target = validate_configuration(args, dsn)
    policy = json.loads(args.health_policy.read_text())
    pool = kube = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=0, max_size=2, command_timeout=5, timeout=5,
                                        server_settings={'default_transaction_read_only': 'on'})
        await require_evidence_reader(pool)
        async with pool.acquire() as conn:
            stream = await conn.fetchrow('SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1', args.health_stream)
        if stream is None:
            raise Blocked('selected-health-source-unavailable')
        identity = validate_stream(stream, policy)
        if (identity['environment'] != 'local'
            or identity['artifact_root_hash'] != stable_id({'root': str(args.source_store.resolve())})):
            raise Blocked('image-review-source-root-or-environment-mismatch')
        source = HealthLedgerReader(pool, ArtifactStore(args.source_store), args.health_stream, policy)
        kube = LocalKubernetes(args.server, target, token_file=args.token_file, ca_file=args.ca_file)
        review = ImageEvidenceReview(source, kube, ArtifactStore(args.review_store), target)
        result = await review.open() if args.operation == 'open' else await review.complete(args.review_ref)
        print(json.dumps(result, sort_keys=True), flush=True)
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
    operations = parser.add_subparsers(dest='operation', required=True)
    operations.add_parser('open')
    complete = operations.add_parser('complete')
    complete.add_argument('--review-ref', required=True)
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
