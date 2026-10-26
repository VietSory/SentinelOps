"""Publish compact checksum-backed results even if artifact transport is down."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from sentinelops.telemetry.store import ArtifactStore
from sentinelops.contracts import utc_time


def summarize(path: Path) -> dict:
    report = json.loads(path.read_text())
    manifest = report["manifest"]
    store = ArtifactStore(path.parent)
    recorded = store.read(manifest["manifest_ref"])
    if recorded != {key:value for key,value in manifest.items() if key != "manifest_ref"}:
        raise ValueError("report manifest differs from its immutable snapshot")
    capture_clocks = {}
    for reference in manifest["raw_refs"] + report["evidence_refs"]:
        raw = store.read(reference)
        if reference in report["evidence_refs"]:
            clock = raw.get("collected_at")
            if clock is not None:
                utc_time(clock)
            capture_clocks[reference] = clock
    observations = [store.read(ref) for ref in manifest["observation_refs"]]
    features = [store.read(ref) for ref in manifest["feature_refs"]]
    return {
        "status": report["status"], "scope": report["scope"],
        "source_profile": manifest["source_profile"], "manifest_ref": manifest["manifest_ref"],
        "commit_at_start": report.get("commit_at_start"),
        "commit_at_report": report.get("commit_at_report", report.get("commit")),
        "observation_count": len(observations),
        "observation_quality": dict(Counter(item["quality"]["status"] for item in observations)),
        "eligible_features_by_service": dict(Counter(item["service"] for item in features if item["payload"] is not None)),
        "quarantine_count": len(manifest["quarantine_refs"]),
        "evidence_capture_clocks": capture_clocks,
        "workload_outcomes": report["details"]["counters"], "evidence_refs": report["evidence_refs"],
        "raw_archive_required_for_release": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    summary = summarize(args.report)
    encoded = json.dumps(summary, indent=2, sort_keys=True)
    print("STEP4_EVIDENCE_SUMMARY\n"+encoded)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write("## Phase 4 live verification\n\n```json\n"+encoded+"\n```\n\n")
            stream.write("Raw capture transport is reported separately. An unavailable archive is not a complete release evidence gate.\n")


if __name__ == "__main__":
    main()
