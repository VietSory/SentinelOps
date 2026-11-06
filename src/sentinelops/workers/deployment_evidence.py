"""Join real bounded image transitions to UID-bound Kubernetes RCA evidence.

No experiment schedule or client payload supplies a deployment fact. Observation
intervals stay intervals; existing model, RCA and action gates remain authoritative.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sentinelops.contracts import utc_time
from sentinelops.incidents.rca import evaluate_rca
from sentinelops.incidents.rca_replay import Evidence, card
from sentinelops.remediation.domain import Target
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.deployment_changes import verify_change
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.incident_evidence import now

MAX_MANIFESTS = 1024


def _source(profile, identity, policy):
    root = Path(profile['health_source_root']).resolve()
    if (stable_id({'root':str(root)}) != identity['artifact_root_hash']
        or stable_id(policy) != identity['policy_sha256']):
        raise ValueError('deployment evidence health root or policy differs from accepted source')
    return SimpleNamespace(stream_id=stable_id(identity),policy=policy,store=ArtifactStore(root))


def _matching(store, reference, *, source, target, inventory, onset, bucket_end, as_of):
    verified = verify_change(store,reference,source=source,target=target,as_of=as_of)
    lower,upper = verified['change_interval'];after=verified['after'];pod=inventory['payment-service']
    if (after.image != pod['image'] or after.generation != pod['generation']
        or after.target.deployment_uid != pod['deployment_uid']
        or not onset-timedelta(seconds=600) <= lower <= upper < onset
        or utc_time(bucket_end)-timedelta(seconds=60) < upper):
        return None
    return verified


class DeploymentIncidentEvidence:
    requires_source_state = True

    def __init__(self, collector, source, changes):
        if collector.profile['profile_version'] != 'kubernetes-incident-evidence/v1':
            raise ValueError('deployment evidence requires the explicit Kubernetes source')
        if changes.root == source.store.root or changes.root in source.store.root.parents or source.store.root in changes.root.parents:
            raise ValueError('deployment events must remain separate from original health source')
        self.collector,self.source,self.changes=collector,source,changes
        self.profile={**collector.profile,'health_source_root':str(source.store.root),
                      'deployment_change_root_sha256':stable_id({'root':str(changes.root)})}

    async def verify_source(self,identity):return await self.collector.verify_source(identity)
    async def close(self):await self.collector.close()

    async def capture(self,store,*,source_state_ref,**kwargs):
        captured=store.read(await self.collector.capture(store,source_state_ref=source_state_ref,**kwargs))
        inventory=captured['source_containers'];target=Target(**self.profile['target'])
        identity=kwargs['health_identity'];source=_source(self.profile,identity,self.source.policy)
        state=store.read(source_state_ref);batch=store.read(state['last_batch_ref'])
        # Bounded local immutable feed. Invalid/corrupt matching receipts are not
        # silently repaired or accepted; operator must resolve the source issue.
        paths=[]
        for path in (self.changes.root/'manifests').glob('*.json'):
            paths.append(path)
            if len(paths)>MAX_MANIFESTS:raise ValueError('deployment event feed exceeds bounded manifest budget')
        selected=[];decision=now();onset=utc_time(kwargs['group']['first_onset_at'])
        for path in paths:
            ref='artifact://manifests/'+path.name;event=self.changes.read(ref)
            if event.get('record_version')!='payment-deployment-change/v1':continue
            if any(event.get(k)!=v for k,v in {'health_stream_id':source.stream_id,'target':vars(target)}.items()):continue
            # Old events are outside this incident; future receipts never qualify.
            if utc_time(event['recorded_at'])>decision:raise ValueError('deployment change receipt is in the future')
            if not onset-timedelta(seconds=600)<=utc_time(event['change_not_before'])<onset:continue
            actual=_matching(self.changes,ref,source=source,target=target,inventory=inventory,
                onset=onset,bucket_end=batch['bucket_end'],as_of=decision)
            if actual is not None:selected.append((ref,event,actual))
        # Multiple observations of the same exact API generation are not extra
        # support. Select the narrowest verified interval deterministically.
        selected.sort(key=lambda x:((x[2]['change_interval'][1]-x[2]['change_interval'][0]).total_seconds(),x[0]))
        refs=[]
        if selected:
            reference,event,_=selected[0]
            for ref in (event['before_ref'],event['after_ref'],event['baseline_ref'],reference):
                if store.write('manifests',self.changes.read(ref))!=ref:raise ValueError('deployment source changed while archiving')
            refs=[reference]
        captured.update(capture_version='continuous-incident-evidence/v5',profile=self.profile,
                        deployment_changes=refs,analyzed_at=timestamp(now()))
        return store.write('manifests',captured)


def verify_deployment_evidence(store,capture,identity,health_policy,group):
    references=capture['deployment_changes']
    if not isinstance(references,list) or len(references)>1 or len(set(references))!=len(references):
        raise ValueError('deployment evidence exceeds one observed Payment transition')
    if not references:return []
    source=_source(capture['profile'],identity,health_policy);target=Target(**capture['profile']['target'])
    state=store.read(capture['physical_binding']['source_state_ref']);batch=store.read(state['last_batch_ref'])
    verified=[]
    for ref in references:
        item=_matching(store,ref,source=source,target=target,inventory=capture['source_containers'],
            onset=utc_time(group['first_onset_at']),bucket_end=batch['bucket_end'],as_of=utc_time(capture['analyzed_at']))
        if item is None:raise ValueError('deployment change does not resolve the physical incident source')
        verified.append(item)
    return verified


def add_deployment_support(store,derived,changes,*,policy,as_of):
    if not changes:return derived
    result=derived['rca'];group=next(g for g in result['correlation']['groups'] if g['group_id']==result['group_id'])
    if not any(c['service']=='payment-service' for c in result['candidates']):return derived
    actual=changes[0];lower,upper=actual['change_interval'];reference=actual['source_ref']
    allowed=set(result['source_refs'])|{reference}
    evidence=Evidence(group,allowed)
    fact_id=evidence.add('change','payment-service',timestamp(upper),reference,
        f"API-observed Payment image transition {actual['before'].image} -> {actual['after'].image}; "
        f"mutation bounded in [{timestamp(lower)}, {timestamp(upper)}]; timestamp is the interval upper bound, "
        'not an exact deployment time or proof of causality',timestamp(actual['available_at']))
    cards=[{'service':c['service'],'components':deepcopy(c['components'])} for c in result['candidates']]
    next(c for c in cards if c['service']=='payment-service')['components']['recent_change_support']=card(
        1.,'observed-deployment/v1',[fact_id])
    updated=evaluate_rca(result['correlation'],result['group_id'],result['evidence']+list(evidence.facts.values()),
        cards,policy,as_of=as_of,evidence_availability={**result['evidence_availability'],**evidence.available},
        source_refs=sorted(allowed))
    change={'target':vars(actual['after'].target),'before_image':actual['before'].image,
        'after_image':actual['after'].image,'after_generation':actual['after'].generation,
        'observed_at':timestamp(upper),'not_before':timestamp(lower),'available_at':timestamp(actual['available_at']),
        'source_ref':reference,'fact_id':fact_id}
    return {**derived,'rca':updated,'rca_ref':store.write('manifests',updated),'deployment_change':change}
