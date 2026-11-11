"""Record an owner decision excluding one incident-hit ADR-016 group; never retry or repair it."""
import argparse
import json
from pathlib import Path

from sentinelops.experiments.local_corpus import ALIBABA_PLAN, load_plan
from sentinelops.experiments.owner_exclusion import exclude_group
from sentinelops.telemetry.store import ArtifactStore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--group', type=int, required=True)
    parser.add_argument('--replacement-group', type=int, required=True)
    parser.add_argument('--decision', required=True, help='The owner decision, verbatim and dated')
    parser.add_argument('--plan', type=Path, default=ALIBABA_PLAN)
    args = parser.parse_args()
    print(json.dumps(exclude_group(ArtifactStore(args.root), load_plan(args.plan), role='development-validation',
                                   ordinal=args.group, replacement_ordinal=args.replacement_group,
                                   decision=args.decision), indent=2))
