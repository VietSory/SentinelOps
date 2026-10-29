"""Reject isolated mutations of a real completed native capture; keep originals immutable."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from sentinelops.contracts import utc_time
from sentinelops.incidents.rca import evaluate_rca, validate_result
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.incident_replay import MAX_OBJECT_BYTES, replay, require


CASES = ("external-snapshot-checksum", "external-health-snapshot-checksum", "hash-valid-IER-mutation",
         "coherent-math-but-fabricated-RCA", "receipt-version-mismatch", "missing-source-group",
         "raw-byte-corruption", "duplicate-source-step", "audit-actor-mismatch", "outbox-lifecycle-mismatch")


def check(root: Path) -> dict:
    watched = [root / "database-snapshot.json", root / "incident-consumer/database-snapshot.json",
               root / "incident-consumer/acceptance.json", *sorted((root / "worker").rglob("*.json")),
               *sorted((root / "incident-consumer/objects").rglob("*.json"))]
    if (root / "acceptance.json").exists():
        watched.append(root / "acceptance.json")
    require(len(watched) <= 10000 and all(not p.is_symlink() and p.stat().st_size <= MAX_OBJECT_BYTES for p in watched),
            "negative capture probe exceeds selected file budget or contains symlinks")
    require(sum(p.stat().st_size for p in watched) <= 256 * 1024 * 1024, "negative capture probe exceeds total byte budget")
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    positive = replay(root)
    checks = []
    for name in CASES:
        with TemporaryDirectory(prefix="sentinelops-incident-negative-") as temporary:
            target = Path(temporary)
            for original in watched:
                copied = target / original.relative_to(root)
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, copied)
            snapshot_path = target / "incident-consumer/database-snapshot.json"
            snapshot = json.loads(snapshot_path.read_text())
            job = snapshot["jobs"][0]
            tables = snapshot[job["job_id"]]
            if name in ("external-snapshot-checksum", "hash-valid-IER-mutation"):
                first = tables["incident_versions"][0]
                body = json.loads(first["payload"])
                body["severity"] = "critical" if body["severity"] != "critical" else "medium"
                first["payload"] = json.dumps(body)
            elif name == "external-health-snapshot-checksum":
                path = target / "database-snapshot.json"
                path.write_bytes(path.read_bytes() + b"\n")
            elif name == "coherent-math-but-fabricated-RCA":
                store = ArtifactStore(target / "incident-consumer/objects")
                evidence = tables["incident_job_evidence"][0]
                result = store.read(evidence["result_ref"])
                cards = [{"service": c["service"], "components": deepcopy(c["components"])} for c in result["candidates"]]
                chosen = next(c for c in cards if c["service"] != "rds"
                              and c["components"]["dependency_trace_relevance"]["value"] is not None)
                card = chosen["components"]["dependency_trace_relevance"]
                card["value"] = 0. if card["value"] else 1.
                forged = evaluate_rca(result["correlation"], result["group_id"], result["evidence"], cards,
                    result["policy"], as_of=utc_time(result["as_of"]),
                    evidence_availability=result["evidence_availability"], source_refs=result["source_refs"])
                validate_result(forged)
                require(forged != result, "negative RCA probe must actually change the selected result")
                evidence["result_ref"] = store.write("manifests", forged)
            elif name == "receipt-version-mismatch":
                tables["incident_command_receipts"][0]["revision"] += 10
            elif name == "missing-source-group":
                snapshot["jobs"] = snapshot["jobs"][1:]
            elif name == "raw-byte-corruption":
                source = ArtifactStore(target / "worker")
                health = json.loads((target / "database-snapshot.json").read_text())
                batch = next(b for row in health["batches"] if (b := source.read(row["manifest_ref"]))["current_raw_refs"])
                path = source.root / batch["current_raw_refs"][0].removeprefix("artifact://")
                path.write_text("{}")
            elif name == "duplicate-source-step":
                snapshot["incident_consumer_steps"].append(deepcopy(snapshot["incident_consumer_steps"][0]))
            elif name == "audit-actor-mismatch":
                tables["incident_events"][0]["actor"] = "unrelated-actor"
            elif name == "outbox-lifecycle-mismatch":
                row = tables["outbox"][0]
                body = json.loads(row["body"])
                body["lifecycle_stage"] = "CLOSED" if body["lifecycle_stage"] != "CLOSED" else "OPEN"
                row["body"] = json.dumps(body)
            data = json.dumps(snapshot, indent=2) + "\n"
            snapshot_path.write_text(data)
            path = target / "incident-consumer/acceptance.json"
            accepted = json.loads(path.read_text())
            accepted["database_snapshot_sha256"] = hashlib.sha256(data.encode()).hexdigest()
            path.write_text(json.dumps(accepted) + "\n")
            selected = ({"expected_incident_snapshot_sha256": positive["incident_snapshot_sha256"]}
                        if name == "external-snapshot-checksum" else
                        {"expected_health_snapshot_sha256": positive["health_snapshot_sha256"]}
                        if name == "external-health-snapshot-checksum" else {})
            try:
                replay(target, **selected)
            except (ValueError, AssertionError) as exc:
                checks.append({"case": name, "status": "REJECTED", "error_type": type(exc).__name__})
            else:
                raise ValueError("mutated real capture accepted: " + name)
    require(before == {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched},
            "negative probe changed the original capture")
    return {"status": "PASS", "scope": "offline-integrity-negative-probe-on-isolated-real-native-capture-copies",
        "positive_batches": positive["batches"], "positive_incidents": len(positive["jobs"]), "checks": checks,
        "original_files_unchanged": True, "actions_allowed": False, "not_a_new_live_or_model_trial": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "negative probe output must be exclusive")
    result = check(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("CAPTURED INCIDENT NEGATIVE INTEGRITY: PASS " + str(args.output))


if __name__ == "__main__":
    main()
