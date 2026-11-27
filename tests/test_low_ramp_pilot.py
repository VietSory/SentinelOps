from copy import deepcopy
from datetime import datetime, timedelta, timezone

from scripts.verify_low_ramp_pilot import assess
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp


def test_active_coverage_distinguishes_unknown_and_sustained_latency():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    buckets = {str(int((start + timedelta(minutes=minute)).timestamp())):
               {'availability': 'VERIFIED', 'p95_latency_ms': 750.0} for minute in range(7)}
    oracle = {'services': {service: {'buckets': deepcopy(buckets)} for service in BUSINESS_ROUTES}}
    active_start, active_end = timestamp(start + timedelta(minutes=1, seconds=10)), timestamp(start + timedelta(minutes=4, seconds=20))
    result = assess(oracle, active_start, active_end)
    assert all(item['verified_minutes'] == 4 and item['unknown_minutes'] == 0
               and item['latency_episodes'] == 0 for item in result.values())
    target = oracle['services']['payment-service']['buckets']
    for minute in (2, 3):
        target[str(int((start + timedelta(minutes=minute)).timestamp()))]['p95_latency_ms'] = 751.0
    assert assess(oracle, active_start, active_end)['payment-service']['latency_episodes'] == 1
    target[str(int((start + timedelta(minutes=3)).timestamp()))]['availability'] = 'UNKNOWN'
    result = assess(oracle, active_start, active_end)['payment-service']
    assert result['unknown_minutes'] == 1 and result['latency_episodes'] == 0
