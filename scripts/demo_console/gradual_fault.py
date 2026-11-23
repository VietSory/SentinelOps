"""Gradual Payment latency through the dev fault endpoint (DEMO_UPGRADE_SPEC WP5, fallback act).

Same shape as the evaluated S3 runs (src/sentinelops/experiments/runner.py): every 30 s the
delay is max_ms * max(0.05, progress) for 1200 s, then the fault is reset to healthy.
Usage: python -m scripts.demo_console.gradual_fault --url http://127.0.0.1:18081
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx


def schedule(*, duration_s=1200, step_s=30, max_ms=1200):
    """(offset seconds, delay ms) for each step; delay never decreases."""
    return [(offset, max(1, int(max_ms * max(.05, offset / duration_s))))
            for offset in range(0, duration_s, step_s)]


def run(url, *, duration_s=1200, step_s=30, max_ms=1200, sleep=time.sleep, client=None):
    endpoint = url.rstrip('/') + '/internal/faults'
    client = client or httpx.Client(timeout=5, trust_env=False)
    started = time.monotonic()
    try:
        for offset, delay in schedule(duration_s=duration_s, step_s=step_s, max_ms=max_ms):
            sleep(max(0., started + offset - time.monotonic()))
            client.post(endpoint, json={'mode': 'latency', 'delay_ms': delay}).raise_for_status()
            print(f'+{offset:4d}s Payment latency fault {delay} ms', flush=True)
        sleep(max(0., started + duration_s - time.monotonic()))
    finally:
        client.post(endpoint, json={'mode': 'healthy', 'delay_ms': 0})
        print('Payment fault reset to healthy', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Payment service origin (port-forward or Service URL)')
    parser.add_argument('--duration-seconds', type=int, default=1200)
    parser.add_argument('--max-ms', type=int, default=1200)
    args = parser.parse_args(argv)
    if not 300 <= args.duration_seconds <= 3600 or not 100 <= args.max_ms <= 5000:
        parser.error('duration or max delay outside supported bounds')
    run(args.url, duration_s=args.duration_seconds, max_ms=args.max_ms)
    return 0


if __name__ == '__main__':
    sys.exit(main())
