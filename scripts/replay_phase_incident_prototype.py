"""Offline checksum-pinned continuous incident-history replay."""
import argparse
import hashlib
import json
from pathlib import Path

import yaml

from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.legacy_incident_replay import replay_incident_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--expected-snapshot-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = (args.run / "database-snapshot.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != args.expected_snapshot_sha256:
        raise ValueError("archived ledger snapshot checksum mismatch")
    report = replay_incident_history(ArtifactStore(args.run / "worker"), json.loads(data),
        yaml.safe_load(Path("configs/topology.yaml").read_text()), json.loads(Path("configs/slo_health_policy.json").read_text()),
        json.loads(Path("configs/rca_policy.json").read_text()))
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2); stream.write("\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
