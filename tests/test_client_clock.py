"""Observed UTC rollback regressions; monotonic duration never repairs UTC."""
import pytest

from sentinelops.experiments.client_clock import ClientClockDiscontinuity, ClientClockGuard


@pytest.mark.parametrize('start,end,duration', [
    ('2026-11-05T05:10:46.511476Z','2026-11-05T05:10:45.927378Z',465.275),
    ('2026-11-05T05:10:46.500611Z','2026-11-05T05:10:45.939092Z',487.808),
])
def test_original_negative_utc_pair_is_retained_and_failure_latched(start,end,duration):
    row={'started_at':start,'completed_at':end,'client_duration_ms':duration}
    original=dict(row);guard=ClientClockGuard()
    guard.observe(row,'2026-11-05T04:56:42.887045Z')
    assert row==original
    # A later good request cannot clear the failure.
    guard.observe({'started_at':'2026-11-05T05:11:00Z','completed_at':'2026-11-05T05:11:01Z'},'2026-11-05T04:56:42.887045Z')
    with pytest.raises(ClientClockDiscontinuity):guard.check()


def test_positive_utc_does_not_require_cross_clock_equality():
    guard=ClientClockGuard()
    guard.observe({'started_at':'2026-11-05T05:00:00Z','completed_at':'2026-11-05T05:00:00.500Z',
                   'client_duration_ms':499.001},'2026-11-05T04:56:42Z')
    guard.check()


def test_utc_start_before_prospective_run_is_not_admitted():
    guard=ClientClockGuard()
    guard.observe({'started_at':'2026-11-05T04:56:41Z','completed_at':'2026-11-05T04:56:42Z'},
                  '2026-11-05T04:56:42Z')
    with pytest.raises(ClientClockDiscontinuity):guard.check()
