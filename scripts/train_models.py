"""Fit only source-admitted current data; historical GAMMA stays blocked."""
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
        report = store.read(args.dataset_ref)
        if report.get("dataset_version") == "local-http-admission/v1":
            from sentinelops.ml.corpus import load_training_rows
            load_training_rows(store, report)  # original-source gate before numerical imports
            from sentinelops.ml.models import train_local_http
            result = train_local_http(store, args.dataset_ref, args.output)
        else:
            require_training_ready(report)
            from sentinelops.ml.models import train_gamma
            result = train_gamma(store, args.dataset_ref, args.output)
    except DatasetNotReady as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "models_fitted": 0, "activation_allowed": False}))
        raise SystemExit(2) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
