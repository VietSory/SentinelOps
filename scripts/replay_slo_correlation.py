"""Correlate objective episodes from one immutable health replay report."""
import argparse
import json
from pathlib import Path

import yaml

from sentinelops.incidents.correlation_replay import replay_correlation
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-store", type=Path, required=True)
    parser.add_argument("--report-ref", required=True)
    parser.add_argument("--topology", type=Path, default=Path("configs/topology.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay_correlation(ArtifactStore(args.health_store), args.report_ref,
        yaml.safe_load(args.topology.read_text()), args.output, project=Path.cwd())
    print(json.dumps({k: v for k, v in report.items() if k != "input_signal_refs"}, indent=2))


if __name__ == "__main__":
    main()
