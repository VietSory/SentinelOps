"""Materialize an unapproved portable H1 candidate, with separate runtime/audit trees."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.handoff import export_model_candidate
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--dataset-ref", required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--evaluation-store", type=Path, required=True)
    parser.add_argument("--evaluation-ref", required=True)
    parser.add_argument("--training-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export_model_candidate(ArtifactStore(args.store), args.dataset_ref, args.models,
            ArtifactStore(args.evaluation_store), args.evaluation_ref,
            training_manifest_sha256=args.training_manifest_sha256, output=args.output)
    except DatasetNotReady as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "activation_allowed": False,
                          "holdout_opened": False}))
        raise SystemExit(2) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
