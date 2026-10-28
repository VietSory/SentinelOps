"""Revalidate a captured real health ledger and immutable objects without a database."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path

from scripts.check_health_worker_live import verify
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.telemetry.core import stable_id


async def replay(root: Path, *, expected_snapshot_sha256: str | None = None) -> dict:
    blob = (root / "database-snapshot.json").read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if expected_snapshot_sha256 is not None and digest != expected_snapshot_sha256:
        raise ValueError("exported health ledger checksum differs from the expected artifact")
    snapshot = json.loads(blob)
    metadata_path = root / "acceptance.json" if (root / "acceptance.json").exists() else root / "running.json"
    metadata = json.loads(metadata_path.read_text())
    identifier = metadata["stream_id"]
    if snapshot.get("snapshot_version") != "health-ledger-snapshot/v1" or snapshot["stream"]["stream_id"] != identifier:
        raise ValueError("unsupported or mismatched captured health ledger")
    if (not snapshot["batches"] or stable_id(snapshot["stream"]["identity"]) != identifier
        or snapshot["stream"]["last_bucket_end"] != snapshot["batches"][-1]["bucket_end"]
        or snapshot["stream"]["last_batch_ref"] != snapshot["batches"][-1]["manifest_ref"]):
        raise ValueError("captured health ledger source/checkpoint identity differs")
    for kind in ("batches", "signals"):
        if any(row["stream_id"] != identifier for row in snapshot[kind]):
            raise ValueError("captured health ledger mixes source streams")
    def decoded(row):
        return {key: datetime.fromisoformat(value.replace("Z", "+00:00"))
                if key in ("bucket_end", "evaluated_at", "recorded_at") and value is not None
                else json.dumps(value, allow_nan=False) if key == "payload" else value for key, value in row.items()}
    class CapturedConnection:
        async def fetch(self, query, *args):
            if args != (identifier,): raise ValueError("replay query differs from captured source")
            if "health_batches" in query: kind = "batches"
            elif "health_signals" in query: kind = "signals"
            else: raise ValueError("unsupported replay query")
            return [decoded(row) for row in snapshot[kind]]
    class Acquire:
        async def __aenter__(self): return CapturedConnection()
        async def __aexit__(self, *args): pass
    class CapturedLedger:
        def acquire(self): return Acquire()
    result = await verify(CapturedLedger(), ArtifactStore(root / "worker"), identifier,
                          sorted(root.glob("worker-*.log")), metadata["interventions"])
    return {"status": "PASS", "mode": "offline-captured-real-ledger-not-database-integration",
            "data_source_commit": metadata["source_commit"], "snapshot_sha256": digest,
            "verification": result, "model_outputs_available": False, "actions_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--expected-snapshot-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(replay(args.run, expected_snapshot_sha256=args.expected_snapshot_sha256))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("CAPTURED HEALTH LEDGER REPLAY: PASS " + str(args.output))


if __name__ == "__main__":
    main()
