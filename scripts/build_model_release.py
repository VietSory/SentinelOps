"""Operator-only: build a validated serving release after explicit owner decisions.

Run only after the owner has accepted G3 for this exact candidate and B has a
PASS source-parity record from the target environment. Nothing is activated;
select the printed release through SENTINELOPS_MODEL_STORE / _RELEASE_REF.
"""
import argparse
import json
from pathlib import Path

from sentinelops.ml.release import build_model_release
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True, help="Verified unapproved H1 handoff directory")
    parser.add_argument("--expected-id", required=True, help="Candidate ID from the trusted handoff channel")
    parser.add_argument("--v2-store", type=Path, required=True, help="Output store from evaluate_detector_events")
    parser.add_argument("--v2-report-ref", required=True, help="Exact v2 report reference in the owner's decision")
    parser.add_argument("--parity-record", type=Path, required=True, help="B model-source-parity/v1 record JSON")
    parser.add_argument("--parity-verification", type=Path, required=True, help="B verification.json pinned by the parity record")
    parser.add_argument("--approved-by", required=True, help="Owner identity recording the G3 decision")
    parser.add_argument("--decision-note", required=True, help="Owner's G3/activation decision, verbatim")
    parser.add_argument("--confirm-owner-decision", action="store_true", required=True,
                        help="Required acknowledgement that the owner took this decision outside software")
    parser.add_argument("--replay", action="store_true", help="Also reproduce development scores on this host")
    parser.add_argument("--output", type=Path, required=True, help="New trusted serving store directory")
    args = parser.parse_args()
    report = ArtifactStore(args.v2_store).read(args.v2_report_ref)
    result = build_model_release(args.candidate, args.expected_id, v2_report=report, v2_report_ref=args.v2_report_ref,
        parity_record=json.loads(args.parity_record.read_bytes()),
        parity_verification=json.loads(args.parity_verification.read_bytes()),
        approved_by=args.approved_by, decision_note=args.decision_note, output=args.output, replay=args.replay)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
