"""Explicitly exclude an original clock-invalid whole group; never rewrite or retry it."""
import argparse
import json
from pathlib import Path

from sentinelops.experiments.clock_recovery import abandon_clock_invalid_group
from sentinelops.experiments.local_corpus import load_plan
from sentinelops.telemetry.store import ArtifactStore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--role', choices=['train', 'calibration', 'development-validation'], required=True)
    parser.add_argument('--group', type=int, required=True)
    parser.add_argument('--replacement-group', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(abandon_clock_invalid_group(ArtifactStore(args.root), load_plan(), role=args.role,
        ordinal=args.group, replacement_ordinal=args.replacement_group), indent=2))
