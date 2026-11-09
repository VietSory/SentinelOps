"""ADR-016: package a train_alibaba fit into a checksum-pinned sentinelops-model/v3 bundle."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.alibaba_bundle import package_fit

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package_fit(args.fit, args.output), indent=2))
