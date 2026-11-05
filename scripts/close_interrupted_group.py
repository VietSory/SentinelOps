"""Explicitly close a host-interrupted whole group and reserve one bounded replacement."""
import argparse
import json
from pathlib import Path

from sentinelops.experiments.local_corpus import load_plan
from sentinelops.experiments.local_recovery import abandon_interrupted_group
from sentinelops.telemetry.store import ArtifactStore

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--role", choices=["train", "calibration", "development-validation"], required=True)
    parser.add_argument("--group", type=int, required=True)
    parser.add_argument("--replacement-group", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(abandon_interrupted_group(ArtifactStore(args.root), load_plan(),
        role=args.role, ordinal=args.group, replacement_ordinal=args.replacement_group), indent=2))
