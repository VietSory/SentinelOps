import argparse
import json
from pathlib import Path

from sentinelops.public_data.phases import audit_phase_labels


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_phase_labels(args.manifest, args.resources, args.output, project=Path.cwd())
    print(json.dumps(report, indent=2))
