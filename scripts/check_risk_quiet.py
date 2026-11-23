"""Check a healthy window had no early-warning episode and live scoring (DEMO_UPGRADE_SPEC R0.2).

Usage: python -m scripts.check_risk_quiet --prometheus http://127.0.0.1:9090 --minutes 60
"""
from __future__ import annotations

import argparse
import sys

import httpx

SERVICES = ('checkout-service', 'payment-service', 'inventory-service')
MAX_AGE_SECONDS = 180


class Prometheus:
    def __init__(self, endpoint: str):
        self.client = httpx.Client(base_url=endpoint.rstrip('/'), timeout=10, trust_env=False)

    def query(self, expression: str) -> float | None:
        body = self.client.get('/api/v1/query', params={'query': expression}).json()
        if body.get('status') != 'success':
            return None
        result = body['data']['result']
        if len(result) != 1:
            return None
        return float(result[0]['value'][1])


def check(prometheus, *, minutes: int = 60) -> list[str]:
    problems = []
    required_scores = minutes - 5
    for service in SERVICES:
        selector = f'{{service="{service}"}}'
        episode = prometheus.query(f'max_over_time(sentinelops_risk_episode_active{selector}[{minutes}m])')
        changes = prometheus.query(f'changes(sentinelops_risk_last_scored_unixtime{selector}[{minutes}m])')
        age = prometheus.query(f'time() - sentinelops_risk_last_scored_unixtime{selector}')
        if episode is None or changes is None or age is None:
            problems.append(f'{service}: missing risk metrics')
            continue
        if episode != 0:
            problems.append(f'{service}: warning episode during the healthy window')
        if changes < required_scores:
            problems.append(f'{service}: scored only {changes:.0f} buckets, need {required_scores}')
        if age >= MAX_AGE_SECONDS:
            problems.append(f'{service}: risk scoring stale for {age:.0f} s')
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prometheus', required=True)
    parser.add_argument('--minutes', type=int, default=60)
    args = parser.parse_args(argv)
    found = check(Prometheus(args.prometheus), minutes=args.minutes)
    for line in found:
        print('FAIL ' + line)
    print('RESULT: ' + ('FAIL' if found else 'PASS'))
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
