"""Register and capture one approved prospective local corpus slot; never fit."""
import argparse
import asyncio
import json
from pathlib import Path

from sentinelops.experiments.local_corpus import capture_slot, load_plan
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('artifacts/local-http-corpus-v1'))
    parser.add_argument('--plan', type=Path, default=Path('configs/local_http_capture_plan.json'))
    parser.add_argument('--role', choices=['train','calibration','development-validation','sealed-final'], required=True)
    parser.add_argument('--group', type=int, required=True)
    parser.add_argument('--kind', choices=['healthy','payment-gradual','inventory-gradual'], required=True)
    parser.add_argument('--prepare', action='store_true', help='Create an unused owned Compose context before registration.')
    args = parser.parse_args()
    result = asyncio.run(capture_slot(ArtifactStore(args.root), load_plan(args.plan), role=args.role,
                                    ordinal=args.group, kind=args.kind, prepare=args.prepare))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
