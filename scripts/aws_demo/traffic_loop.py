"""ConfigMap template; render inserts only the existing drive() function."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import signal
from uuid import uuid4

import httpx
from sentinelops.experiments.traffic import PATHS
from sentinelops.telemetry.core import BUSINESS_ROUTES

PAYMENT = 'payment-service'


def clock():
    return datetime.now(timezone.utc)


# EXISTING_DRIVE_FUNCTION


async def entry(service):
    # Each entry has its own driver, so slow Checkout cannot block direct entries.
    urls = {service: f'http://{service}:8000'}
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        while True:
            try:
                counts = await drive(client, urls, clock() + timedelta(seconds=30), False,
                                     dependency_outage=lambda: True)
                print(json.dumps({'service': service, 'counts': counts}), flush=True)
            except (httpx.HTTPError, ValueError) as exc:
                print(json.dumps({'service': service, 'error': type(exc).__name__}), flush=True)
                await asyncio.sleep(1)


async def main():
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    await asyncio.gather(*(entry(service) for service in BUSINESS_ROUTES))


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except asyncio.CancelledError:
        pass
