"""Read separately operator-approved images; never generate an approval here.

The selected ledger reference and approver are server configuration, not request
fields. A writer controlling that trusted store/configuration is an operator;
checksums alone are not claimed to authenticate an untrusted remote principal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.remediation.domain import Blocked, Deployment, Pod, Policy, Target, image_digest
from sentinelops.remediation.verification import verify_post_action_health


def deployment_from_projection(value):
    if set(value) != {'observation_version', 'observation'} or value['observation_version'] != 'payment-deployment/v1':
        raise Blocked('unsupported-known-good-deployment-evidence')
    body = dict(value['observation'])
    body['target'] = Target(**body['target'])
    body['pods'] = tuple(Pod(**p) for p in body['pods'])
    return Deployment(**body)


@dataclass(frozen=True)
class KnownGood:
    target: Target
    image: str
    ledger_ref: str
    approved_at: str
    evidence_refs: tuple[str, ...]


class KnownGoodRegistry:
    def __init__(self, store, ledger_ref, *, approved_by, source, environment='local'):
        self.store, self.reference, self.source = store, ledger_ref, source
        self.environment = environment
        record = store.read(ledger_ref)
        validate_payload('payment-known-good.schema.json', record)
        if not approved_by or record['approved_by'] != approved_by:
            raise Blocked('known-good-approver-mismatch')
        targets = [Target(**item['target']).key for item in record['images']]
        if len(set(targets)) != len(targets): raise Blocked('duplicate-known-good-target')
        self.record = record

    async def resolve(self, target: Target, *, incident_at: datetime):
        if self.store.read(self.reference) != self.record:
            raise Blocked('known-good-ledger-changed')
        if utc_time(self.record['approved_at']) >= incident_at:
            raise Blocked('known-good-not-approved-before-incident')
        matching = [item for item in self.record['images'] if item['target'] == asdict(target)]
        if len(matching) != 1: raise Blocked('no-approved-known-good-for-target')
        item = matching[0]
        if item['environment'] != self.environment:
            raise Blocked('known-good-environment-mismatch')
        image_digest(item['image'])
        before, after = [deployment_from_projection(self.store.read(item[k])) for k in ('before_ref', 'after_ref')]
        for observation in (before, after):
            observation.validate(Policy(target))
            if observation.target != target or not observation.ready_for(item['image'], prior_generation=0, old_pods=()):
                raise Blocked('known-good-deployment-not-ready-at-approved-image')
        if (before.generation != after.generation or {p.uid for p in before.pods} != {p.uid for p in after.pods}
            or before.image != after.image):
            raise Blocked('known-good-deployment-changed-during-health-interval')
        health = self.store.read(item['health_ref'])
        actual = await verify_post_action_health(self.source, effective_at=utc_time(health['effective_at']),
            as_of=utc_time(health['as_of']), expected_environment=self.environment, max_batches=10000)
        if (health != actual or health['status'] != 'telemetry_verified'
            or not utc_time(before.observed_at) <= utc_time(health['effective_at'])
            or not utc_time(health['as_of']) <= utc_time(after.observed_at) <= utc_time(self.record['approved_at'])):
            raise Blocked('known-good-health-does-not-reproduce-within-observed-deployment')
        return KnownGood(target, item['image'], self.reference, self.record['approved_at'],
                         tuple(item[k] for k in ('before_ref', 'after_ref', 'health_ref')))
