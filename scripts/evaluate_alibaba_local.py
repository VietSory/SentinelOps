"""Freeze and score a complete ADR-016 v2 evaluation epoch once."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.alibaba_local_evaluation import evaluate_alibaba_local
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_alibaba_local(ArtifactStore(args.root), args.bundle, args.manifest_sha256, args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
