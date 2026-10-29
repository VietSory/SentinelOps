"""Revalidate a transported, completed native incident journal without services."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinelops.workers.incident_replay import replay
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-health-snapshot-sha256")
    parser.add_argument("--expected-incident-snapshot-sha256")
    parser.add_argument("--model-store", type=Path)
    parser.add_argument("--model-release-ref")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("incident replay output must be exclusive")
    if bool(args.model_store) != bool(args.model_release_ref):
        raise ValueError("model replay needs both trusted store and release reference")
    registry = ModelRegistry.load(ArtifactStore(args.model_store), args.model_release_ref) if args.model_store else None
    result = replay(args.run, expected_health_snapshot_sha256=args.expected_health_snapshot_sha256,
                    expected_incident_snapshot_sha256=args.expected_incident_snapshot_sha256, model_registry=registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("CAPTURED INCIDENT LEDGER REPLAY: PASS " + str(args.output))


if __name__ == "__main__":
    main()
