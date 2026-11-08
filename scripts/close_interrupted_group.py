"""Explicitly close a host-interrupted whole group and reserve one bounded replacement."""
import argparse
import json
from pathlib import Path

from sentinelops.experiments.local_corpus import DEFAULT_PLAN, load_plan
from sentinelops.experiments.local_recovery import abandon_interrupted_group
from sentinelops.telemetry.store import ArtifactStore

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--role", choices=["train", "calibration", "development-validation"], required=True)
    parser.add_argument("--group", type=int, required=True)
    parser.add_argument("--replacement-group", type=int, required=True)
    parser.add_argument('--plan', type=Path, default=DEFAULT_PLAN, help='Detector-event epochs pass their ADR-015 plan.')
    args = parser.parse_args()
    print(json.dumps(abandon_interrupted_group(ArtifactStore(args.root), load_plan(args.plan),
        role=args.role, ordinal=args.group, replacement_ordinal=args.replacement_group), indent=2))
