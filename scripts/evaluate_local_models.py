"""Compare a trusted fitted local candidate on source-admitted development groups."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.local_evaluation import evaluate_local_models
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--dataset-ref", required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--training-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = evaluate_local_models(ArtifactStore(args.store), args.dataset_ref, args.models,
            training_manifest_sha256=args.training_manifest_sha256, output=args.output)
    except DatasetNotReady as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "models_fitted": 0, "activation_allowed": False, "holdout_opened": False, "G3_accepted": False}))
        raise SystemExit(2) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
