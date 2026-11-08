"""Operator-only: record a known-good Payment image from collected evidence.

Run only after an operator has inspected the `AWAITING_OPERATOR_REVIEW` evidence
from `review_payment_image complete`. The ledger approves the image for
`payment-rollback-known-good/v1` and nothing else; it must predate any bad
change it will be used against. Configure the executor with the printed ref and
the same approver identity.
"""
import argparse
import json
from pathlib import Path

from sentinelops.remediation.approval import build_known_good_ledger
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-store', type=Path, required=True)
    parser.add_argument('--evidence-ref', required=True)
    parser.add_argument('--approved-by', required=True)
    parser.add_argument('--environment', default='local')
    parser.add_argument('--confirm-operator-approval', action='store_true', required=True)
    args = parser.parse_args()
    reference, record = build_known_good_ledger(ArtifactStore(args.review_store), args.evidence_ref,
                                                approved_by=args.approved_by, environment=args.environment)
    print(json.dumps({'ledger_ref': reference, 'approved_at': record['approved_at'],
                      'image': record['images'][0]['image'], 'action_dispatch_authorized': False}, indent=2))


if __name__ == '__main__':
    main()
