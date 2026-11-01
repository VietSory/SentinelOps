"""H4 boundary: current native RCA is evidence, never an action permission.

A future approved H4 producer can implement the internal Assessment provider.
Only software tests inject a qualified fixture provider today. No HTTP payload,
LLM response, explanation or client boolean can select/replace this provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.remediation.domain import Blocked, Target, fresh
from sentinelops.telemetry.core import timestamp


@dataclass(frozen=True)
class Change:
    target: Target
    before_image: str
    after_image: str
    observed_at: str
    available_at: str
    source_ref: str
    fact_id: str


@dataclass(frozen=True)
class Assessment:
    incident_id: str
    revision: int
    lifecycle: str
    availability: str
    latest_health_at: str
    result_ref: str
    rca: dict
    change: Change | None
    dispatch_authority: bool = False
    missing_reason: str = 'native-h4-contract-not-activated'


class NativeH4Authority:
    """Actually reproduce native evidence, while preserving its authority limit."""
    def __init__(self, journal):
        self.journal = journal
        self.health_stream_id = journal.source.stream_id

    async def assess(self, request, target, *, now):
        async with self.journal.pool.acquire() as conn:
            job_id = await conn.fetchval('''SELECT job_id FROM sentinel_control.incident_jobs
                WHERE incident_id=$1 AND consumer_id=$2''', request.incident_id, self.journal.consumer_id)
        if job_id is None: raise Blocked('incident-has-no-verified-native-job')
        _, accepted, derived = await self.journal.verified_evidence(job_id)
        snapshot = await self.journal.repository.get(request.incident_id)
        _, state = await self.journal.checkpoint()
        result = derived['rca']
        return Assessment(request.incident_id, snapshot['revision'], snapshot['context']['lifecycle_stage'],
            snapshot['context']['availability'], state['last_bucket_end'], accepted['result_ref'], result, None,
            dispatch_authority=False,
            missing_reason='rca-abstained' if result['abstained'] else 'native-h4-contract-not-activated')


def qualify(assessment, request, before, known_good, policy, rca_policy, *, now: datetime):
    if assessment.dispatch_authority is not True:
        raise Blocked(assessment.missing_reason)
    result = assessment.rca
    validate_payload('rca-result.schema.json', result)
    if result['policy'] != rca_policy:
        raise Blocked('rca-policy-mismatch')
    if (assessment.incident_id != request.incident_id or assessment.revision != request.incident_revision
        or request.incident_id != 'INC-' + result['group_id'] or assessment.lifecycle != 'RCA_READY'
        or assessment.availability != 'ELIGIBLE'):
        raise Blocked('incident-revision-lifecycle-or-availability-mismatch')
    if result['abstained'] or not result['ranking'] or result['ranking'][0] != 'payment-service':
        raise Blocked('rca-not-qualified-payment-root')
    fresh(result['as_of'], now, policy.freshness_seconds)
    fresh(assessment.latest_health_at, now, policy.freshness_seconds)
    fresh(before.observed_at, now, policy.freshness_seconds)
    group = next(g for g in result['correlation']['groups'] if g['group_id'] == result['group_id'])
    onset = utc_time(group['first_onset_at'])
    change = assessment.change
    if (change is None or change.target != policy.target or change.before_image != known_good.image
        or change.after_image != before.image or known_good.target != policy.target
        or not timedelta(0) < onset - utc_time(change.observed_at) <= timedelta(seconds=policy.change_window_seconds)
        or not utc_time(change.observed_at) <= utc_time(change.available_at) <= now
        or not utc_time(known_good.approved_at) <= utc_time(change.observed_at)):
        raise Blocked('no-matching-pre-incident-deployment-change')
    candidate = next(c for c in result['candidates'] if c['service'] == 'payment-service')
    support = candidate['components']['recent_change_support']
    facts = {f['id']:f for f in result['evidence']}
    fact = facts.get(change.fact_id)
    if (support['value'] is None or support['value'] <= 0 or change.fact_id not in support['evidence_refs']
        or fact is None or fact['type'] != 'change' or fact['service'] != 'payment-service'
        or fact['source_ref'] != change.source_ref or fact['observed_at'] != change.observed_at
        or result['evidence_availability'][change.fact_id] != change.available_at):
        raise Blocked('deployment-change-does-not-resolve-rca-evidence')
    timestamp(now)
