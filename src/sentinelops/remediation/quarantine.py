"""Explicit operator review releases a target lock, never an action/IER outcome.

Two live Deployment observations enclose three raw-verified current healthy
buckets. Original dispatch/cooldown/audit stay immutable. No Kubernetes patch,
automatic review, H4 grant or new-intent producer exists in this module.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import logging
import re

import asyncpg
from opentelemetry import metrics

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.remediation.domain import ActionConflict, Blocked, Policy, Target, fresh, rollback_patch
from sentinelops.remediation.journal import ActionJournal, decode
from sentinelops.remediation.registry import deployment_from_projection
from sentinelops.remediation.verification import validate_current_support
from sentinelops.telemetry.core import stable_id, timestamp

FUNCTION='sentinel_remediation.release_quarantine(text,integer,text,text,text,jsonb)'
logger=logging.getLogger('sentinelops-quarantine-review')


def receipt(row):
    payload=decode(row['payload'])
    validate_payload('quarantine-release.schema.json',payload)
    if (row['integrity'] is not True or payload['action_id']!=row['action_id']
        or payload['action_revision']!=row['action_revision'] or payload['terminal_event_hash']!=row['terminal_event_hash']
        or payload['target_key']!=row['target_key'] or payload['review_ref']!=row['review_ref']
        or row['resolution_id']!=row['review_ref'].removeprefix('artifact://manifests/').removesuffix('.json')):
        raise RuntimeError('quarantine receipt failed integrity verification')
    return {'status':'RELEASED','resolution_id':row['resolution_id'],'action_id':row['action_id'],
            'operator_actor':row['operator_actor'],'recorded_at':timestamp(row['recorded_at']),
            'payload':payload}


RECEIPT_SELECT="SELECT *,payload_sha256=encode(sha256(convert_to(payload::text,'UTF8')),'hex') AS integrity FROM sentinel_remediation.quarantine_releases "


class QuarantineJournal:
    def __init__(self,pool):
        self.pool,self.snapshots=pool,ActionJournal(pool)

    async def check_ready(self):
        async with self.pool.acquire() as conn:
            if await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM sentinel_remediation.schema_migrations')!=[1,2]:
                raise RuntimeError('explicit quarantine migration 2 required')
            allowed=await conn.fetchval("SELECT pg_has_role(current_user,'control_action_resolver','MEMBER') AND has_function_privilege($1,'EXECUTE')",FUNCTION)
            unsafe=await conn.fetchval("""SELECT rolsuper OR rolcreaterole OR rolcreatedb
                OR pg_has_role(current_user,'control_owner','MEMBER')
                OR has_schema_privilege('sentinel_control','CREATE')
                OR has_schema_privilege('sentinel_remediation','CREATE') FROM pg_roles WHERE rolname=current_user""")
            writes=await conn.fetchval("""SELECT coalesce(bool_or(
                has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                OR has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES')),false)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('sentinel_control','sentinel_remediation') AND c.relkind IN ('r','p')""")
            if not allowed or unsafe or writes:
                raise RuntimeError('quarantine operator requires read-only tables and only the guarded release function')

    async def context(self,action_id):
        async with self.pool.acquire() as conn,conn.transaction(isolation='repeatable_read',readonly=True):
            state=await self.snapshots._read(conn,action_id)
            if state is None: raise ActionConflict('action not found')
            target=Target(**state['intent']['policy']['target'])
            active=await conn.fetchval('SELECT active_action FROM sentinel_remediation.targets WHERE target_key=$1',target.key)
            dispatch=await conn.fetchval('SELECT recorded_at FROM sentinel_remediation.dispatches WHERE action_id=$1',action_id)
            now=await conn.fetchval('SELECT clock_timestamp()')
            return {'state':state,'active_action':active,'dispatch_at':dispatch,'now':now}

    async def find(self,*,action_id,revision,event_hash,review_ref,key):
        async with self.pool.acquire() as conn:
            rows=await conn.fetch(RECEIPT_SELECT+'WHERE action_id=$1 OR idempotency_key=$2',action_id,key)
            actor=await conn.fetchval('SELECT session_user')
        if not rows: return None
        if len(rows)!=1: raise ActionConflict('quarantine idempotency conflict')
        row=rows[0]
        if (row['action_id']!=action_id or row['action_revision']!=revision or row['terminal_event_hash']!=event_hash
            or row['review_ref']!=review_ref or row['idempotency_key']!=key or row['operator_actor']!=actor):
            raise ActionConflict('quarantine idempotency conflict')
        return receipt(row)

    async def release(self,*,action_id,revision,event_hash,review_ref,key,payload):
        validate_payload('quarantine-release.schema.json',payload)
        try:
            async with self.pool.acquire() as conn:
                row=decode(await conn.fetchval('SELECT '+FUNCTION.split('(')[0]+'($1,$2,$3,$4,$5,$6::jsonb)',
                    action_id,revision,event_hash,review_ref,key,json.dumps(payload,allow_nan=False)))
        except (asyncpg.SerializationError,asyncpg.UniqueViolationError):
            raise ActionConflict('quarantine state or idempotency changed') from None
        except asyncpg.CheckViolationError:
            raise Blocked('quarantine-proof-stale-premature-or-invalid-at-commit') from None
        return await self.find(action_id=action_id,revision=revision,event_hash=event_hash,review_ref=review_ref,key=key)


class QuarantineReview:
    def __init__(self,journal,kubernetes,recovery,store,target,*,clock=lambda:datetime.now(timezone.utc)):
        if recovery.target!=target:
            raise ValueError('quarantine source/target mismatch')
        original=recovery.source.store.root
        if store.root==original or store.root in original.parents or original in store.root.parents:
            raise ValueError('review store must be separate from original health source')
        self.journal,self.kube,self.recovery,self.store,self.target,self.clock=journal,kubernetes,recovery,store,target,clock
        self.counter=metrics.get_meter('sentinelops-remediation').create_counter('sentinelops_quarantine_review_observations_total')

    def _emit(self,result,operation):
        self.counter.add(1,{'operation':operation,'status':result['status']})
        logger.info(json.dumps({'event':'quarantine_review_observed','operation':operation,'status':result['status'],
                               'action_id':result.get('action_id'),'review_ref':result.get('review_ref')},sort_keys=True))
        return result

    def _scope(self,context):
        state=context['state'];intent=state['intent'];evidence=intent['evidence']
        if (state['phase']!='escalated' or context['active_action']!=state['action_id']
            or context['dispatch_at'] is None or not isinstance(evidence,dict)):
            raise Blocked('action-is-not-an-armed-quarantine')
        if (utc_time(state['facts']['dispatch_started_at'])!=context['dispatch_at']
            or context['now']-context['dispatch_at']<timedelta(seconds=600)):
            raise Blocked('quarantine-dispatch-drain-period-not-complete')
        if (intent['policy']['target']!=asdict(self.target)
            or evidence['health_stream_id']!=self.recovery.source.stream_id
            or evidence['health_policy_sha256']!=stable_id(self.recovery.source.policy)
            or evidence['health_environment']!=self.recovery.environment
            or evidence['known_good']['target']!=asdict(self.target)):
            raise Blocked('quarantine-original-source-or-target-binding-mismatch')
        before=deployment_from_projection({'observation_version':'payment-deployment/v1','observation':evidence['before']})
        policy=Policy(**{**intent['policy'],'target':self.target})
        before.validate(policy)
        image=evidence['known_good']['image']
        rollback_patch(before,image)  # Pure validation; never calls transport.
        return state,before,image,policy

    def _ready(self,current,before,image,policy,*,check_freshness=True):
        current.validate(policy,before_dispatch=False)
        if check_freshness: fresh(current.observed_at,self.clock(),180)
        if (current.generation!=before.generation+1 or current.resource_version==before.resource_version
            or not current.ready_for(image,prior_generation=before.generation,old_pods=[p.uid for p in before.pods])):
            raise Blocked('quarantine-known-good-rollout-not-settled')

    async def open(self,action_id):
        try:
            return self._emit(await self._open(action_id),'open')
        except Blocked:
            self._emit({'status':'BLOCKED','action_id':action_id},'open')
            raise

    async def _open(self,action_id):
        await self.journal.check_ready()
        context=await self.journal.context(action_id)
        state,before,image,policy=self._scope(context)
        current=await self.kube.observe();self._ready(current,before,image,policy)
        snapshot=self.store.write('observations',{'observation_version':'payment-deployment/v1','observation':current.projection()})
        body={'review_version':'manual-quarantine-review/v1','action_id':action_id,'action_revision':state['revision'],
              'terminal_event_hash':state['last_event_hash'],'intent_hash':stable_id(state['intent']),
              'target_key':self.target.key,'opened_at':current.observed_at,'before_ref':snapshot,
              'health_stream_id':self.recovery.source.stream_id,'health_policy_sha256':stable_id(self.recovery.source.policy),
              'environment':self.recovery.environment}
        body['review_id']=stable_id(body)
        validate_payload('quarantine-review.schema.json',body)
        return {'status':'REVIEW_OPENED','review_ref':self.store.write('manifests',body),'review':body,
                'dispatch_authorized':False,'recovery_accepted':False}

    async def release(self,review_ref,*,key):
        try:
            return self._emit(await self._release(review_ref,key=key),'release')
        except Blocked:
            self._emit({'status':'BLOCKED','review_ref':review_ref},'release')
            raise

    async def _release(self,review_ref,*,key):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',key): raise ValueError('invalid quarantine idempotency key')
        await self.journal.check_ready()
        review=self.store.read(review_ref)
        validate_payload('quarantine-review.schema.json',review)
        if review['review_id']!=stable_id({k:v for k,v in review.items() if k!='review_id'}):
            raise Blocked('quarantine-review-integrity-mismatch')
        if review['target_key']!=self.target.key: raise Blocked('quarantine-review-target-mismatch')
        args={'action_id':review['action_id'],'revision':review['action_revision'],'event_hash':review['terminal_event_hash'],
              'review_ref':review_ref,'key':key}
        existing=await self.journal.find(**args)
        if existing: return existing
        context=await self.journal.context(review['action_id'])
        state,before,image,policy=self._scope(context)
        if (review['action_revision']!=state['revision'] or review['terminal_event_hash']!=state['last_event_hash']
            or review['intent_hash']!=stable_id(state['intent']) or review['target_key']!=self.target.key
            or review['health_stream_id']!=self.recovery.source.stream_id
            or review['health_policy_sha256']!=stable_id(self.recovery.source.policy)
            or review['environment']!=self.recovery.environment):
            raise Blocked('quarantine-review-scope-changed')
        fresh(review['opened_at'],self.clock(),600)
        initial=deployment_from_projection(self.store.read(review['before_ref']))
        if initial.observed_at!=review['opened_at']: raise Blocked('quarantine-review-opening-clock-mismatch')
        self._ready(initial,before,image,policy,check_freshness=False)
        current=await self.kube.observe();self._ready(current,before,image,policy)
        if (current.generation!=initial.generation or current.image!=initial.image
            or {(p.uid,p.image_id) for p in current.pods}!={(p.uid,p.image_id) for p in initial.pods}):
            raise Blocked('quarantine-rollout-changed-during-review')
        cutoff=self.clock()
        verdict=await self.recovery.verify(effective_at=utc_time(review['opened_at']),now=cutoff,target=self.target)
        if verdict['status'] in ('pending','inconclusive'):
            return {'status':verdict['status'].upper(),'review_ref':review_ref,'released':False}
        if verdict['status']!='telemetry_verified': raise Blocked('unknown-quarantine-health-verdict')
        ends=validate_current_support(verdict,source=self.recovery.source,environment=self.recovery.environment,
                                      effective_at=review['opened_at'],as_of=cutoff)
        final=await self.kube.observe();self._ready(final,before,image,policy)
        if (final.generation!=initial.generation or final.image!=initial.image
            or {(p.uid,p.image_id) for p in final.pods}!={(p.uid,p.image_id) for p in initial.pods}):
            raise Blocked('quarantine-rollout-changed-during-review')
        completed=self.clock()
        fresh(review['opened_at'],completed,600)
        expected=datetime.fromtimestamp(int((completed-timedelta(seconds=30)).timestamp())//60*60,timezone.utc)
        if ends[-1]!=expected: return {'status':'PENDING','review_ref':review_ref,'released':False}
        after_ref=self.store.write('observations',{'observation_version':'payment-deployment/v1','observation':final.projection()})
        payload={'release_version':'manual-quarantine-release/v1','review_ref':review_ref,
            'action_id':state['action_id'],'action_revision':state['revision'],'terminal_event_hash':state['last_event_hash'],
            'target_key':self.target.key,'opened_at':review['opened_at'],'observed_at':timestamp(completed),
            'verification_as_of':timestamp(cutoff),'last_bucket_end':timestamp(ends[-1]),
            'before_ref':review['before_ref'],'after_ref':after_ref,'health_ref':self.store.write('manifests',verdict),
            'dispatch_authorized':False,'recovery_accepted':False,'ier_mutated':False}
        return await self.journal.release(**args,payload=payload)
