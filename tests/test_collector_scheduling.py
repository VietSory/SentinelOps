import asyncio
from datetime import datetime,timezone
import json

import pytest

from sentinelops.telemetry.prometheus import BucketNotReady
from sentinelops.telemetry.scheduling import capture_ready_bucket,wait_until_utc
from sentinelops.experiments.diagnostics import failure_context


def test_timer_waking_early_does_not_mean_the_utc_watermark_has_elapsed():
    values=iter([100.,104.,109.,114.,119.,124.,129.2,130.])
    sleeps=[]
    async def sleep(seconds): sleeps.append(seconds)
    asyncio.run(wait_until_utc(130.,now=lambda:next(values),sleep=sleep))
    assert sleeps[-1]==pytest.approx(.8)
    assert all(0<value<=5 for value in sleeps)


def test_large_clock_correction_cannot_wait_forever():
    async def check():
        with pytest.raises(TimeoutError):
            await wait_until_utc(130.,now=lambda:100.,timeout_seconds=.01)
    asyncio.run(check())


def test_capture_rechecks_utc_if_clock_moves_between_wait_and_source_read():
    class Source:
        calls=0
        def capture(self,service,end):
            self.calls+=1
            if self.calls==1: raise BucketNotReady("clock adjustment")
            return {"raw":"captured"}
    source=Source();waits=[]
    async def wait(deadline): waits.append(deadline)
    result=asyncio.run(capture_ready_bucket(source,"payment-service",datetime(2026,10,26,tzinfo=timezone.utc),wait=wait))
    assert result=={"raw":"captured"} and source.calls==len(waits)==2


def test_capture_does_not_retry_arbitrary_validation_errors():
    class Source:
        calls=0
        def capture(self,*args):
            self.calls+=1
            raise ValueError("unaligned bucket")
    async def wait(deadline): pass
    source=Source()
    with pytest.raises(ValueError,match="unaligned"):
        asyncio.run(capture_ready_bucket(source,"payment-service",datetime.now(timezone.utc),wait=wait))
    assert source.calls==1


def test_failure_diagnostics_preserve_location_without_secrets(tmp_path):
    try:
        raise ValueError("postgresql://user:SECRET@private/database")
    except ValueError as exc:
        diagnostic=failure_context(exc,tmp_path)
    assert diagnostic["type"]=="ValueError" and diagnostic["frames"]
    assert "SECRET" not in json.dumps(diagnostic) and "postgresql" not in json.dumps(diagnostic)
