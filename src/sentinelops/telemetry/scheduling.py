"""Wait on UTC source readiness without assuming monotonic and UTC clocks agree."""
import asyncio
import math
import time

from sentinelops.telemetry.prometheus import BucketNotReady


async def wait_until_utc(deadline: float,*,now=None,sleep=None,timeout_seconds: float = 120):
    now,sleep = now or time.time,sleep or asyncio.sleep
    if not math.isfinite(deadline) or not 0<timeout_seconds<=120:
        raise ValueError("invalid bounded UTC wait")
    async with asyncio.timeout(timeout_seconds):
        while True:
            current=now()
            if not math.isfinite(current):
                raise ValueError("UTC clock returned a nonfinite value")
            remaining=deadline-current
            if remaining<=0:
                return
            # asyncio timers use a monotonic clock. Recheck actual UTC after
            # every bounded sleep instead of treating a timer wakeup as proof.
            await sleep(min(5.,remaining))


async def capture_ready_bucket(source,service,end,*,store=None,wait=None):
    wait=wait or wait_until_utc
    def capture_and_store():
        raw=source.capture(service,end)
        if store is not None:
            store.write("raw",raw)
        return raw
    for attempt in range(3):
        await wait(end.timestamp()+30)
        try:
            return await asyncio.to_thread(capture_and_store)
        except BucketNotReady:
            if attempt==2:
                raise RuntimeError("UTC source readiness changed repeatedly") from None
