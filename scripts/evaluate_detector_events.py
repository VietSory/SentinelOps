"""ADR-015 protocol v2: evaluate the unchanged candidate on abrupt detector events."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.detector_evaluation import evaluate_detector_events
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True, help="Frozen fitting corpus of the candidate.")
    parser.add_argument("--dataset-ref", required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--training-manifest-sha256", required=True)
    parser.add_argument("--detector-store", type=Path, required=True, help="Frozen ADR-015 detector-event epoch.")
    parser.add_argument("--detector-report-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = evaluate_detector_events(ArtifactStore(args.store), args.dataset_ref, args.models,
            training_manifest_sha256=args.training_manifest_sha256, detector_store=ArtifactStore(args.detector_store),
            detector_report_ref=args.detector_report_ref, output=args.output)
    except DatasetNotReady as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "activation_allowed": False,
                          "holdout_opened": False, "G3_accepted": False}))
        raise SystemExit(2) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
