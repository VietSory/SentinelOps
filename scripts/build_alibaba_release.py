"""Operator-only: build a serving release for the ADR-016 Alibaba bundle.

Run only after the owner has accepted G3 for this exact bundle and a PASS
`alibaba-parity-criteria/v1` record exists for the target environment. Nothing
is activated; select the printed release through SENTINELOPS_MODEL_STORE / _RELEASE_REF.
"""
import argparse
import json
from pathlib import Path

from sentinelops.ml.alibaba_release import build_alibaba_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True, help='From a separate trusted channel')
    parser.add_argument('--controls', type=Path, required=True, help='alibaba-relative-controls/v1 controls.json')
    parser.add_argument('--parity-record', type=Path, required=True)
    parser.add_argument('--parity-verification', type=Path, required=True)
    parser.add_argument('--approved-by', required=True)
    parser.add_argument('--decision-note', required=True, help="Owner's G3 decision, verbatim")
    parser.add_argument('--confirm-owner-decision', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_bytes())
    print(json.dumps(build_alibaba_release(
        args.bundle, args.manifest_sha256, controls=read(args.controls), parity_record=read(args.parity_record),
        parity_verification=read(args.parity_verification), approved_by=args.approved_by,
        decision_note=args.decision_note, output=args.output), indent=2))


if __name__ == '__main__':
    main()
