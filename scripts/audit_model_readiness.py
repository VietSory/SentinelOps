"""Report exact committed GAMMA support without opening raw/holdout data."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.readiness import audit_readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("docs/reports/evidence/2026-10-26-gamma-grouped-dataset.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_readiness(json.loads(args.report.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2); stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
