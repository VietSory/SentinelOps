"""Fit only an admitted GAMMA health/precursor corpus; never activates models."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--dataset-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Gate before loading optional numerical libraries.
    from sentinelops.ml.readiness import require_training_ready
    store = ArtifactStore(args.store)
    try:
        require_training_ready(store.read(args.dataset_ref))
        from sentinelops.ml.models import train_gamma
        result = train_gamma(store, args.dataset_ref, args.output)
    except DatasetNotReady as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "models_fitted": 0, "activation_allowed": False}))
        raise SystemExit(2) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
