import asyncio
from datetime import datetime,timedelta,timezone

import pytest

from scripts import check_rca_producer as driver


def test_fresh_acceptance_waits_for_actual_time_instead_of_backdating_intent(monkeypatch):
    cutoff=datetime(2026,10,28,tzinfo=timezone.utc)
    clock=[cutoff-timedelta(seconds=25)]
    sleeps=[]
    class Clock:
        @classmethod
        def now(cls,zone):return clock[0]
    async def advance(seconds):
        sleeps.append(seconds);clock[0]+=timedelta(seconds=seconds)
    monkeypatch.setattr(driver,'datetime',Clock)
    monkeypatch.setattr(driver.asyncio,'sleep',advance)
    asyncio.run(driver.wait_for_cutoff(cutoff))
    assert clock[0]>=cutoff and sum(sleeps)==25 and max(sleeps)<=10


def test_unexpected_far_future_cutoff_fails_without_unbounded_wait(monkeypatch):
    async def no_wait(seconds):raise AssertionError('unexpected wait')
    monkeypatch.setattr(driver.asyncio,'sleep',no_wait)
    with pytest.raises(ValueError,match='bounded acceptance wait'):
        asyncio.run(driver.wait_for_cutoff(datetime.now(timezone.utc)+timedelta(seconds=120)))
