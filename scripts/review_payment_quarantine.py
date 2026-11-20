"""Explicit operator review; never patches Kubernetes, creates an action or accepts recovery."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import asyncpg

from sentinelops.remediation.domain import Blocked, Target
from sentinelops.remediation.executor import LedgerRecovery
from sentinelops.remediation.kubernetes import LocalKubernetes, configured_server
from sentinelops.remediation.quarantine import QuarantineJournal, QuarantineReview
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream


def validate_configuration(args,dsn):
    selected=urlsplit(dsn)
    local=(selected.hostname=='127.0.0.1' and bool(selected.port) and not selected.query)
    remote=(bool(selected.hostname) and parse_qs(selected.query)=={'sslmode':['verify-full']})
    if (selected.scheme not in ('postgres','postgresql') or not selected.username or not selected.password
        or selected.path!='/sentinelops_control' or selected.fragment or not (local or remote)):
        raise ValueError('explicit quarantine database with local transport or sslmode=verify-full required')
    target=Target(**json.loads(args.target.read_text()))
    configured_server(args.server)
    environment=os.getenv('SENTINELOPS_ENV','local')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,31}',environment):
        raise ValueError('invalid quarantine environment')
    if not re.fullmatch(r'[0-9a-f]{64}',args.health_stream) or not args.source_store.is_dir():
        raise ValueError('selected quarantine health source is unavailable')
    return target,environment


async def run(args):
    dsn=os.getenv('SENTINELOPS_QUARANTINE_DATABASE_URL','')
    target,environment=validate_configuration(args,dsn)
    pool=kube=None
    try:
        pool=await asyncpg.create_pool(dsn,min_size=0,max_size=4,command_timeout=5,timeout=5)
        journal=QuarantineJournal(pool);await journal.check_ready()
        policy=json.loads(args.health_policy.read_text())
        async with pool.acquire() as conn:
            stream=await conn.fetchrow('SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1',args.health_stream)
        if stream is None: raise ValueError('selected accepted health source is unavailable')
        identity=validate_stream(stream,policy)
        if identity['environment']!=environment or identity['artifact_root_hash']!=stable_id({'root':str(args.source_store.resolve())}):
            raise ValueError('quarantine source/root/environment binding mismatch')
        source=HealthLedgerReader(pool,ArtifactStore(args.source_store),args.health_stream,policy)
        kube=LocalKubernetes(args.server,target,token_file=args.token_file,ca_file=args.ca_file)
        await kube.require_observer()
        review=QuarantineReview(journal,kube,LedgerRecovery(source,target,environment=environment),
            ArtifactStore(args.review_store),target,superseding_generation=args.superseding_generation)
        if args.operation=='open':
            if not re.fullmatch(r'[0-9a-f]{64}',args.action_id): raise ValueError('invalid action identity')
            result=await review.open(args.action_id)
        else:
            while True:
                result=await review.release(args.review_ref,key=args.idempotency_key)
                if not args.wait or result['status']!='PENDING':
                    break
                await asyncio.sleep(5)
        print(json.dumps(result,sort_keys=True),flush=True)
    finally:
        try:
            if kube is not None: await kube.close()
        finally:
            if pool is not None: await pool.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('health-stream','server'):
        parser.add_argument('--'+flag,required=True)
    for flag in ('target','source-store','review-store','token-file','ca-file'):
        parser.add_argument('--'+flag,type=Path,required=True)
    parser.add_argument('--health-policy',type=Path,default=Path('configs/slo_health_policy.json'))
    parser.add_argument('--superseding-generation',type=int,
        help='exact later generation to review after the original rollback is proven in the audit')
    operations=parser.add_subparsers(dest='operation',required=True)
    opening=operations.add_parser('open');opening.add_argument('--action-id',required=True)
    release=operations.add_parser('release');release.add_argument('--review-ref',required=True)
    release.add_argument('--idempotency-key',required=True)
    release.add_argument('--wait',action='store_true',help='retry pending telemetry in the same process until the review expires')
    args=parser.parse_args()
    try:
        asyncio.run(run(args))
    except Blocked as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc),'released':False}),flush=True)
        raise SystemExit(2) from None
    except Exception as exc:
        logging.error(json.dumps({'event':'quarantine_review_failed','error_type':type(exc).__name__}))
        raise SystemExit('quarantine review failed: '+type(exc).__name__) from None


if __name__=='__main__': main()
