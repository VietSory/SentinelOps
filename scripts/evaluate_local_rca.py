"""Replay all source-admitted prospective development RCA trials; final remains closed."""
import argparse
import json
from pathlib import Path

from sentinelops.incidents.local_evaluation import evaluate_local_rca
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--dataset-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = evaluate_local_rca(ArtifactStore(args.store), args.dataset_ref, args.output)
    except DatasetNotReady as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc), 'full_G4_accepted': False,
                          'holdout_opened': False, 'actions_allowed': False}))
        raise SystemExit(2) from None
    print(json.dumps({k: v for k, v in result.items() if k != 'trials'}, indent=2))


if __name__ == '__main__':
    main()
