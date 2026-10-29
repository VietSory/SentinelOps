"""Validate the incident source reader on an imported real ledger in an owned DB.

This is captured-source integration, not a new live incident/RCA evaluation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import yaml

from scripts.check_health_worker_live import target, require_port
from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, validate_stream
from sentinelops.workers.incident_state import advance_groups


async def check(args):
    if args.output.exists():
        raise ValueError("source integration output must be exclusive")
    blob = args.snapshot.read_bytes()
    if hashlib.sha256(blob).hexdigest() != args.expected_snapshot_sha256:
        raise ValueError("captured ledger differs from the selected trusted snapshot hash")
    captured = json.loads(blob)
    if captured.get("snapshot_version") != "health-ledger-snapshot/v1":
        raise ValueError("unsupported captured ledger")
    policy = json.loads(Path("configs/slo_health_policy.json").read_text())
    topology = yaml.safe_load(Path("configs/topology.yaml").read_text())
    stream = captured["stream"]
    identity = validate_stream(stream, policy)
    store = ArtifactStore(args.source_store)
    if identity["artifact_root_hash"] != stable_id({"root": str(store.root)}):
        raise ValueError("selected original source store differs from captured stream identity")
    parsed = urlparse(os.environ.get("CONTROL_DATABASE_URL", ""))
    if (parsed.scheme not in ("postgres", "postgresql") or parsed.hostname not in ("localhost", "127.0.0.1")
        or parsed.username != "control_writer" or parsed.path != "/sentinelops_control"):
        raise ValueError("source integration requires an explicit local control_writer sandbox")
    require_port(target(args.container, args.project, "control-postgres"), "5432/tcp", parsed.port)
    hashes = {str(p.relative_to(store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in store.root.rglob("*.json")}
    pool = await asyncpg.create_pool(os.environ["CONTROL_DATABASE_URL"], min_size=1, max_size=1, command_timeout=10)
    try:
        async with pool.acquire() as conn, conn.transaction():
            for name in ("incidents", "health_streams", "health_batches", "health_signals"):
                # Names are fixed literals, never supplied by the caller.
                if await conn.fetchval("SELECT count(*) FROM sentinel_control." + name):
                    raise ValueError("captured-source fixture requires an empty owned database")
            await conn.execute("""INSERT INTO sentinel_control.health_streams
                (stream_id,identity,initial_bucket_end,last_bucket_end,last_batch_ref)
                VALUES($1,$2::jsonb,$3,$4,$5)""", stream["stream_id"], json.dumps(stream["identity"]),
                utc_time(stream["initial_bucket_end"]), utc_time(stream["last_bucket_end"]), stream["last_batch_ref"])
            for row in captured["batches"]:
                await conn.execute("""INSERT INTO sentinel_control.health_batches
                    (stream_id,bucket_end,manifest_ref,evaluated_at,recorded_at) VALUES($1,$2,$3,$4,$5)""",
                    row["stream_id"], utc_time(row["bucket_end"]), row["manifest_ref"],
                    utc_time(row["evaluated_at"]), utc_time(row["recorded_at"]))
            for row in captured["signals"]:
                await conn.execute("""INSERT INTO sentinel_control.health_signals
                    (stream_id,bucket_end,service,signal_id,signal_ref,payload) VALUES($1,$2,$3,$4,$5,$6::jsonb)""",
                    row["stream_id"], utc_time(row["bucket_end"]), row["service"], row["signal_id"],
                    row["signal_ref"], json.dumps(row["payload"]))
        reader = HealthLedgerReader(pool, store, stream["stream_id"], policy)
        state, scopes, count = None, [], 0
        while (verified := await reader.next(state)) is not None:
            assert count < len(captured["batches"])
            assert verified["batch"]["bucket_end"] == captured["batches"][count]["bucket_end"]
            assert verified["batch"]["evaluated_at"] == captured["batches"][count]["evaluated_at"]
            state, closed = advance_groups(state, verified["batch"], verified["signal_objects"], topology, policy)
            scopes.extend(closed)
            count += 1
        assert count == len(captured["batches"]) and state["last_batch_ref"] == stream["last_batch_ref"]
        changed = {**state, "source": {**state["source"], "health_stream_id": "f" * 64}}
        try:
            await reader.next(changed)
            raise AssertionError("source reader accepted a foreign consumer checkpoint")
        except ValueError:
            pass
        try:
            await HealthLedgerReader(pool, ArtifactStore(args.output.parent / "wrong-source-root"), stream["stream_id"], policy).next()
            raise AssertionError("source reader accepted a relocated live source without migration")
        except ValueError:
            pass
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM sentinel_control.incidents") == 0
            assert await conn.fetchval("SELECT count(*) FROM sentinel_control.health_batches") == count
            assert await conn.fetchval("SELECT count(*) FROM sentinel_control.health_signals") == len(captured["signals"])
        assert hashes == {str(p.relative_to(store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in store.root.rglob("*.json")}
        report = {"status": "PASS", "mode": "captured-real-ledger-isolated-postgres-source-reader-integration",
            "database_project": args.project, "snapshot_sha256": args.expected_snapshot_sha256,
            "batches": count, "signals": len(captured["signals"]), "closed_scopes": len(scopes),
            "group_members": [[g["affected_services"] for g in s["correlation"]["groups"]] for s in scopes],
            "actual_recorded_health_clocks_preserved": True, "source_files_unchanged": True,
            "foreign_checkpoint_and_root_rejected": True, "incidents_written": 0,
            "live_incident_worker_verified": False, "actions_allowed": False,
            "implementation_sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (
                "src/sentinelops/workers/health_source.py", "src/sentinelops/workers/incident_state.py",
                "src/sentinelops/incidents/correlation.py", "scripts/check_incident_source.py")}}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as f: json.dump(report, f, indent=2); f.write("\n")
        print("CAPTURED HEALTH SOURCE POSTGRES INTEGRATION: PASS " + str(args.output))
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-snapshot-sha256", required=True)
    parser.add_argument("--source-store", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(check(args))
    except Exception as exc:
        raise SystemExit("captured source integration failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
