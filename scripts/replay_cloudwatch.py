"""Replay captured CloudWatch HTTP RED buckets offline; never call AWS or approve parity."""
import argparse
import json
from pathlib import Path
from sentinelops.telemetry.cloudwatch import process_captures
from sentinelops.telemetry.store import ArtifactStore

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store",type=Path,required=True)
    parser.add_argument("--capture-ref",action="append",required=True)
    parser.add_argument("--environment",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if len(args.capture_ref)>1000:
        parser.error("offline replay is limited to 1000 capture refs")
    source=ArtifactStore(args.store)
    captures=[source.read(ref) for ref in args.capture_ref]
    args.output.mkdir(parents=True,exist_ok=False)
    result=process_captures(captures,ArtifactStore(args.output),environment=args.environment)
    print(json.dumps({"manifest_ref":result["manifest_ref"],"observations":result["observation_count"],
        "eligible_features":result["eligible_feature_count"],"quarantined":len(result["quarantine_refs"]),
        "source_parity":"not-accepted","activation_allowed":False},indent=2))
