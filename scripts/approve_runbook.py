"""Record an explicit on-call decision in the remediation-controller's shared store."""
import argparse
import json

from sentinelops.remediation.approvals import record_approval
from sentinelops.telemetry.store import ArtifactStore


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--store', required=True)
    result.add_argument('--proposal-id', required=True)
    result.add_argument('--approved-by', required=True)
    result.add_argument('--decision', choices=('approve', 'reject'), required=True)
    result.add_argument('--reason', default='')
    result.add_argument('--confirm-oncall-approval', action='store_true', required=True)
    return result


def main():
    args = parser().parse_args()
    try:
        reference = record_approval(ArtifactStore(args.store), proposal_id=args.proposal_id,
            approved_by=args.approved_by, decision=args.decision, reason=args.reason,
            confirm_oncall_approval=args.confirm_oncall_approval)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps({'event': 'runbook_approval', 'approval_ref': reference}))


if __name__ == '__main__':
    main()
