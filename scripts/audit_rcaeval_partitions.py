"""Audit frozen RCAEval partitions using only publisher-verified index metadata."""
import argparse
import json
from pathlib import Path

from sentinelops.public_data.rcaeval_partitions import audit_partitions, DEFAULT_PARTITIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--partitions", type=Path, default=DEFAULT_PARTITIONS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_partitions(args.manifest, args.partitions, project=Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"cases": report["cases"], "groups": report["groups"], "roles": report["roles"],
                      "holdout_opened": False}, indent=2))


if __name__ == "__main__":
    main()
