"""Replay one captured X-Ray request and original CloudWatch completion snapshots offline."""
import argparse
import json
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.telemetry.xray import normalize_capture

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store",type=Path,required=True)
    parser.add_argument("--capture-ref",required=True)
    parser.add_argument("--completion-capture-ref",action="append",required=True)
    parser.add_argument("--environment",required=True)
    parser.add_argument("--correlation-id",required=True)
    parser.add_argument("--as-of",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if len(args.completion_capture_ref)>1000:parser.error("too many completion captures")
    source=ArtifactStore(args.store)
    capture=source.read(args.capture_ref)
    completions=[source.read(ref) for ref in args.completion_capture_ref]
    try:
        result=normalize_capture(capture,completions,environment=args.environment,
            correlation_id=args.correlation_id,as_of=utc_time(args.as_of))
    except (ValueError,KeyError,TypeError) as exc:
        print(json.dumps({"status":"UNAVAILABLE","error_type":type(exc).__name__,
                          "source_parity":"not-accepted","actions_allowed":False}))
        raise SystemExit(2) from None
    args.output.mkdir(parents=True,exist_ok=False)
    destination=ArtifactStore(args.output)
    for value in [capture,*completions]:destination.write("raw",value)
    ref=destination.write("manifests",result)
    print(json.dumps({"report_ref":ref,"structural_request_validated":True,
                      "source_parity":"not-accepted","actions_allowed":False},indent=2))
