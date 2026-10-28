"""Prepare an immutable replay intent, or persist that intent to a local control DB."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

from sentinelops.incidents.rca_producer import prepare_intent, publish_intent
from sentinelops.incidents.repository import IncidentRepository
from sentinelops.telemetry.store import ArtifactStore


async def publish(args,store,policy):
    dsn = os.environ.get("CONTROL_DATABASE_URL","")
    parsed = urlparse(dsn)
    if parsed.hostname not in ("127.0.0.1","localhost") or parsed.path != "/sentinelops_control" or parsed.username != "control_writer":
        raise ValueError("producer requires the dedicated local control database and nonowner writer")
    pool = await asyncpg.create_pool(dsn,min_size=1,max_size=4,command_timeout=10)
    try:
        report = await publish_intent(IncidentRepository(pool),store,args.intent_ref,policy=policy)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({"intent_ref":args.intent_ref,"persisted_incidents":len(report["incidents"]),
            "actions_allowed":False,"output":str(args.output)}))
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--policy",type=Path,default=Path("configs/rca_policy.json"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-report-ref",help="verify the report and freeze an actual emission clock; no DB write")
    mode.add_argument("--intent-ref",help="reuse this exact immutable intent on every retry")
    parser.add_argument("--output",type=Path,default=Path("artifacts/incidents/rca-publish.json"))
    args = parser.parse_args()
    store = ArtifactStore(args.bundle);policy = json.loads(args.policy.read_text())
    try:
        if args.prepare_report_ref:
            print(json.dumps({"intent_ref":prepare_intent(store,args.prepare_report_ref,policy=policy)}))
        else:
            asyncio.run(publish(args,store,policy))
    except Exception as exc:
        # Never echo DSNs or server connection diagnostics into CLI output.
        raise SystemExit("Local RCA producer failed closed: "+type(exc).__name__) from None


if __name__ == "__main__":main()
