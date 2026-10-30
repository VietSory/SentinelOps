"""Reproduce the frozen native development evaluation without opening holdout."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from sentinelops.public_data.rcaeval_evaluation import evaluate_manifest
from sentinelops.public_data.rcaeval_native import digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--profile", type=Path, default=Path("configs/rcaeval_native_profile.json"))
    parser.add_argument("--policy", type=Path, default=Path("configs/rca_policy.json"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip():
        raise ValueError("commit native protocol and code before measuring diagnostic results")
    if args.output.exists():
        raise ValueError("evaluation output already exists; retain immutable prior measurements")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    profile, policy = json.loads(args.profile.read_text()), json.loads(args.policy.read_text())
    report = evaluate_manifest(args.manifest, profile, policy, source_commit=commit)
    repeated = evaluate_manifest(args.manifest, profile, policy, source_commit=commit)
    if report != repeated:
        raise ValueError("repeated native evaluation differs")
    report["repeat_identical"] = True
    report["report_sha256"] = digest(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: report[k] for k in ("status", "denominators", "conditional_qualified_accuracy", "repeat_identical")}, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
