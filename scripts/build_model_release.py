"""Operator-only: build a validated serving release after explicit owner decisions.

Run only after the owner has accepted G3 for this exact candidate and B has a
PASS source-parity record from the target environment. Nothing is activated;
select the printed release through SENTINELOPS_MODEL_STORE / _RELEASE_REF.
"""
import argparse
import json
from pathlib import Path

from sentinelops.ml.release import build_model_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True, help="Verified unapproved H1 handoff directory")
    parser.add_argument("--expected-id", required=True, help="Candidate ID from the trusted handoff channel")
    parser.add_argument("--v2-report", type=Path, required=True, help="ADR-015 local-model-development/v2 report JSON")
    parser.add_argument("--parity-record", type=Path, required=True, help="B model-source-parity/v1 record JSON")
    parser.add_argument("--approved-by", required=True, help="Owner identity recording the G3 decision")
    parser.add_argument("--decision-note", required=True, help="Owner's G3/activation decision, verbatim")
    parser.add_argument("--confirm-owner-decision", action="store_true", required=True,
                        help="Required acknowledgement that the owner took this decision outside software")
    parser.add_argument("--replay", action="store_true", help="Also reproduce development scores on this host")
    parser.add_argument("--output", type=Path, required=True, help="New trusted serving store directory")
    args = parser.parse_args()
    result = build_model_release(args.candidate, args.expected_id,
        v2_report=json.loads(args.v2_report.read_bytes()), parity_record=json.loads(args.parity_record.read_bytes()),
        approved_by=args.approved_by, decision_note=args.decision_note, output=args.output, replay=args.replay)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
