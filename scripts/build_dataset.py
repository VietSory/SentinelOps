"""Freeze offline experiment outputs; pilot data never silently passes G2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.experiments.datasets import assemble_dataset
from sentinelops.experiments.labels import SLOConfig, measure_slo
from sentinelops.experiments.verification import verify_source_replay
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.telemetry.store import ArtifactStore


def load_run(path: Path) -> dict:
    run = json.loads((path/"run.json").read_text())
    validate_payload("experiment-run.schema.json", run)
    if run["status"] != "completed" or run["dirty_worktree"]:
        raise ValueError("dataset input requires a completed clean-source run")
    truth_reference = "artifact://manifests/"+stable_id(run)+".json"
    truth_store = ArtifactStore(path/"truth")
    if truth_store.read(truth_reference) != run:
        raise ValueError("run pointer differs from immutable truth snapshot")
    source_store = ArtifactStore(path/"serving")
    manifest = source_store.read(run["source_manifest_ref"])
    if manifest["source_profile"] != run["source_profile"] or manifest["environment"] != run["environment"]:
        raise ValueError("source manifest does not match its experiment")
    records = source_store.read(run["logs_ref"])["records"]
    slo = SLOConfig(**run["parameters"]["slo"])
    for service in BUSINESS_ROUTES:
        measured = measure_slo(records,service,slo,start_at=utc_time(run["started_at"]),end_at=utc_time(run["ended_at"]))
        expected = {key:measured[key] for key in ("first_violation_at","confirmed_at","request_count")}
        if run["slo_by_service"][service] != expected:
            raise ValueError("recorded labels do not match independent server logs")
    features = verify_source_replay(source_store,manifest,run)
    return {"run":run,"features":features,"truth_ref":f"experiment-truth://{run['run_id']}/{stable_id(run)}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--allow-pilot", action="store_true")
    args = parser.parse_args()
    try:
        inputs = [load_run(path) for path in args.run]
        result = assemble_dataset(inputs,ArtifactStore(args.output),dataset_id=args.dataset_id,allow_pilot=args.allow_pilot)
    except (ValueError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
