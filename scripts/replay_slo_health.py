"""Replay objective health from an immutable captured telemetry manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinelops.incidents.health_replay import replay_health_manifest
from sentinelops.telemetry.store import ArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-store", type=Path, required=True)
    parser.add_argument("--manifest-ref", required=True)
    parser.add_argument("--policy", type=Path, default=Path("configs/slo_health_policy.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay_health_manifest(ArtifactStore(args.source_store), args.manifest_ref,
        json.loads(args.policy.read_text()), args.output, project=Path.cwd())
    print(json.dumps({k: v for k, v in report.items() if k not in ("signal_refs", "final_states")}, indent=2))


if __name__ == "__main__":
    main()
