"""Check demo round receipts against the frozen live acceptance (DEMO_UPGRADE_SPEC R0.1).

Usage: python -m scripts.check_demo_round receipt.json [...] [--require N]
Timings are recomputed from the receipt clocks; stored derived values are not trusted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sentinelops.contracts import utc_time

MIN_LEAD_SECONDS = 180
MAX_DEPLOY_TO_BREACH_SECONDS = 600


def problems(value: dict) -> list[str]:
    found = []

    def need(name, ok):
        if not ok:
            found.append(name)

    def seconds(later, earlier):
        try:
            return (utc_time(later) - utc_time(earlier)).total_seconds()
        except (TypeError, ValueError, KeyError):
            return None

    proposal = value.get('proposal') or {}
    warning = value.get('warning') or {}
    action = value.get('action') or {}
    notifications = value.get('notifications') or {}
    need('receipt_version', value.get('receipt_version') == 'runbook-demo-round/v2')
    need('scenario', value.get('scenario') == 'gradual-regression')
    need('proposal_version', proposal.get('proposal_version') == 'runbook-proposal/v2')
    need('expires_at', 'expires_at' not in proposal)
    need('warning', warning.get('service') == 'payment-service' and bool(warning.get('episode_started_at')))
    lead = seconds(value.get('first_breach_at'), warning.get('episode_started_at'))
    need('lead_seconds', lead is not None and lead >= MIN_LEAD_SECONDS)
    deploy = seconds(value.get('first_breach_at'), value.get('deploy_started_at'))
    need('deploy_to_breach_seconds', deploy is not None and 0 < deploy <= MAX_DEPLOY_TO_BREACH_SECONDS)
    need('rca_top1', value.get('rca_top1') == 'payment-service')
    need('action', action.get('phase') == 'recovered')
    need('telemetry_verified', action.get('telemetry_verified') is True)
    need('dispatch_count', value.get('dispatch_count') == 1)
    need('notifications', all(isinstance(notifications.get(kind), int) and notifications[kind] >= 1
                              for kind in ('early_warning', 'proposal_pending', 'outcome')))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipts', nargs='+', type=Path)
    parser.add_argument('--require', type=int)
    args = parser.parse_args(argv)
    values, failed = [], False
    for path in args.receipts:
        value = json.loads(path.read_text())
        values.append(value)
        found = problems(value)
        print(f'{path}: ' + ('PASS' if not found else 'FAIL ' + ', '.join(found)))
        failed |= bool(found)
    if args.require is not None:
        invocations = {v.get('runner_invocation_id') for v in values}
        rounds = sorted(v.get('round') for v in values if isinstance(v.get('round'), int))
        if (len(values) != args.require or len(invocations) != 1 or None in invocations
                or rounds != list(range(1, args.require + 1))):
            print(f'FAIL round: need rounds 1..{args.require} from one runner invocation')
            failed = True
    print('RESULT: ' + ('FAIL' if failed else 'PASS'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
