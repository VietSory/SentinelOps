"""Evaluate only frozen RCAEval development cases using native supported evidence."""
import argparse
import json
from pathlib import Path
import subprocess

from sentinelops.public_data.rcaeval_evaluation import evaluate_development


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/rcaeval_development_evidence.json"))
    parser.add_argument("--policy", type=Path, default=Path("configs/rca_policy.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ValueError("commit native evidence adapter/protocol before development scoring")
    result = evaluate_development(args.manifest, json.loads(args.config.read_text()), json.loads(args.policy.read_text()))
    result["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2); stream.write("\n")
    print(json.dumps(result["metrics"], indent=2))


if __name__ == "__main__":
    main()
