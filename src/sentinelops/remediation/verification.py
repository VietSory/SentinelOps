"""Read-only post-action SLO verification over a raw-verified health ledger.

The caller must supply an actual action-effective clock from a durable executor.
This module verifies telemetry only: it neither proves a Kubernetes rollout nor
accepts recovery, writes an IER, or grants permission to execute an action.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.workers.health_source import HealthLedgerReader, at

REQUIRED_BUCKETS = 3
TIMEOUT_SECONDS = 600


async def verify_post_action_health(
    source: HealthLedgerReader, *, effective_at: datetime, as_of: datetime,
    expected_environment: str, max_batches: int = 128,
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
    deadline = effective + timedelta(seconds=TIMEOUT_SECONDS)
    available_by = min(cutoff, deadline)
    policy_hash = stable_id(source.policy)
    support, checkpoint, last_end = [], None, None
    reason = 'awaiting-post-action-telemetry'

    def result(status, why):
        return {
            'verification_version': 'post-action-slo/v1',
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
        if len(support) == REQUIRED_BUCKETS:
            return result('telemetry_verified', 'three-consecutive-eligible-post-action-buckets')
    else:
        return result('inconclusive', 'verified-history-budget-exhausted')
    return result('inconclusive' if cutoff >= deadline else 'pending', reason)
