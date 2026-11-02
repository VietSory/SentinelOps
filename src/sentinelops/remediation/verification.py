"""Read-only post-action SLO verification over a raw-verified health ledger.

The caller must supply an actual action-effective clock from a durable executor.
This module verifies telemetry only: it neither proves a Kubernetes rollout nor
accepts recovery, writes an IER, or grants permission to execute an action.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.workers.health_source import HealthLedgerReader, at
from sentinelops.remediation.domain import Blocked

REQUIRED_BUCKETS = 3
TIMEOUT_SECONDS = 600


def validate_current_support(verdict, *, source, environment, effective_at, as_of):
    """Shared consumer checks; a telemetry verdict is never dispatch/recovery authority."""
    try:
        if (verdict['verification_version']!='post-action-current-slo/v1'
            or verdict['health_stream_id']!=source.stream_id
            or verdict['health_policy_sha256']!=stable_id(source.policy)
            or verdict['effective_at']!=effective_at or verdict['as_of']!=timestamp(as_of)
            or verdict['environment']!=environment or verdict['required_buckets']!=3
            or len(verdict['support'])!=3
            or any(verdict[k] is not False for k in ('action_dispatch_authorized','deployment_identity_verified','recovery_accepted'))):
            raise Blocked('invalid-recovery-verdict')
        ends=[utc_time(b['bucket_end']) for b in verdict['support']]
        if (ends[0]-timedelta(seconds=60)<utc_time(effective_at)
            or ends[1]-ends[0]!=timedelta(seconds=60) or ends[2]-ends[1]!=timedelta(seconds=60)
            or len({b['signal_id'] for b in verdict['support']})!=3
            or any(not re.fullmatch(r'[0-9a-f]{64}',b['signal_id'])
                or not re.fullmatch(r'artifact://manifests/[0-9a-f]{64}\.json',b['batch_ref'])
                or not re.fullmatch(r'artifact://observations/[0-9a-f]{64}\.json',b['observation_ref'])
                or not utc_time(b['bucket_end'])<=utc_time(b['recorded_at'])<=as_of for b in verdict['support'])):
            raise Blocked('invalid-current-recovery-support')
        return ends
    except (KeyError,TypeError,AttributeError,ValueError) as exc:
        if isinstance(exc,Blocked): raise
        raise Blocked('invalid-recovery-verdict') from None


async def verify_post_action_health(
    source: HealthLedgerReader, *, effective_at: datetime, as_of: datetime,
    expected_environment: str, max_batches: int = 128, require_current: bool = False,
) -> dict:
    """Reproduce a bounded, deterministic verification at an explicit cutoff.

Always begin at the source origin: no caller-supplied cursor/streak can skip raw
verification or carry healthy pre-action observations into the result. A future
controller may persist a separately verified checkpoint; this v1 does not.
Database/artifact failures propagate so an executor cannot mistake them for
healthy observations. Exceeding the read budget is explicitly inconclusive.
"""
    # timestamp rejects naive/non-UTC clocks rather than silently converting.
    effective = utc_time(timestamp(effective_at))
    cutoff = utc_time(timestamp(as_of))
    if cutoff < effective or not isinstance(expected_environment, str) or not expected_environment:
        raise ValueError('verification requires an explicit environment and a cutoff after action effectiveness')
    if type(max_batches) is not int or not 1 <= max_batches <= 10000:
        raise ValueError('verification history budget must be an integer from 1 to 10000')
    if type(require_current) is not bool:
        raise ValueError('current verification must be an explicit boolean')
    deadline = effective + timedelta(seconds=TIMEOUT_SECONDS)
    available_by = min(cutoff, deadline)
    policy_hash = stable_id(source.policy)
    support, checkpoint, last_end = [], None, None
    reason = 'awaiting-post-action-telemetry'

    def result(status, why):
        return {
            'verification_version': 'post-action-current-slo/v1' if require_current else 'post-action-slo/v1',
            'service': 'payment-service', 'health_stream_id': source.stream_id,
            'health_policy_sha256': policy_hash, 'environment': expected_environment,
            'effective_at': timestamp(effective), 'as_of': timestamp(cutoff),
            'deadline': timestamp(deadline), 'required_buckets': REQUIRED_BUCKETS,
            'status': status, 'reason': why, 'support': list(support),
            'action_dispatch_authorized': False, 'deployment_identity_verified': False,
            'recovery_accepted': False,
        }

    for _ in range(max_batches):
        verified = await source.next(checkpoint)
        if verified is None:
            break
        batch, row = verified['batch'], verified['accepted_row']
        if (verified['raw_health_lineage_verified'] is not True
            or batch['stream_id'] != source.stream_id
            or batch['identity']['environment'] != expected_environment
            or batch['identity']['policy_sha256'] != policy_hash
            or stable_id(source.policy) != policy_hash):
            raise ValueError('verification source environment, policy or raw lineage changed')
        end = utc_time(batch['bucket_end'])
        if last_end is not None and end != last_end + timedelta(seconds=60):
            raise ValueError('verification source skipped, duplicated or reordered a bucket')
        # Receipt time, not just event time, determines whether evidence was
        # available to this decision. Late evidence cannot cure a timed-out run.
        if end > available_by or at(row['recorded_at']) > available_by:
            break
        checkpoint = {'last_bucket_end': batch['bucket_end'],
            'last_batch_ref': row['manifest_ref'], 'service_states': verified['service_states'],
            'source': {'health_stream_id': source.stream_id}}
        last_end = end
        if end - timedelta(seconds=60) < effective:
            # Exclude the entire bucket that overlaps the action, even when
            # its health decision or recovery_confirmed event came afterwards.
            continue
        signal = verified['service_states']['payment-service']
        if (signal['availability'] != 'ELIGIBLE'
            or signal['health'] not in ('healthy', 'recovering')
            or signal['clear_streak'] < 1):
            support.clear()
            reason = 'post-action-telemetry-unknown-or-breaching'
            continue
        support.append({'bucket_end': batch['bucket_end'],
            'batch_ref': row['manifest_ref'], 'signal_id': signal['signal_id'],
            'observation_ref': signal['observation_ref'],
            'recorded_at': timestamp(at(row['recorded_at']))})
        support[:] = support[-REQUIRED_BUCKETS:]
        if len(support) == REQUIRED_BUCKETS:
            if not require_current:
                return result('telemetry_verified', 'three-consecutive-eligible-post-action-buckets')
    else:
        return result('inconclusive', 'verified-history-budget-exhausted')
    if require_current:
        # This source profile has a verified 30-second watermark. A dead worker
        # cannot certify current recovery from an old healthy prefix. Continue
        # through the cutoff so a later UNKNOWN/breach invalidates early success.
        expected = datetime.fromtimestamp(int((available_by - timedelta(seconds=30)).timestamp()) // 60 * 60, timezone.utc)
        if last_end == expected and len(support) == REQUIRED_BUCKETS:
            return result('telemetry_verified', 'three-current-eligible-post-action-buckets')
        if last_end != expected:
            reason = 'awaiting-current-accepted-health-bucket'
    return result('inconclusive' if cutoff >= deadline else 'pending', reason)
