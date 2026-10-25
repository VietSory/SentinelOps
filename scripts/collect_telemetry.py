"""Capture completed metric buckets and replay them into immutable artefacts.

Historical queries are tagged with their actual collection availability; this
command never invents historical online emission or alert latency.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import BUSINESS_ROUTES
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import PrometheusSource
from sentinelops.telemetry.store import ArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--minutes", type=int, default=6)
    parser.add_argument("--end", help="UTC-aligned last bucket end; defaults to latest past 30s watermark")
    parser.add_argument("--prometheus", default="http://127.0.0.1:9090")
    parser.add_argument("--replay-manifest", help="Replay a manifest ref from the output store without networking")
    args = parser.parse_args()
    store = ArtifactStore(args.output)
    if args.replay_manifest:
        old = store.read(args.replay_manifest)
        captures = [store.read(reference) for reference in old["raw_refs"]]
    else:
        if not 1 <= args.minutes <= 1440:
            raise SystemExit("--minutes must be between 1 and 1440")
        now = datetime.now(timezone.utc)
        end = utc_time(args.end) if args.end else datetime.fromtimestamp(int((now-timedelta(seconds=30)).timestamp())//60*60, timezone.utc)
        source = PrometheusSource(args.prometheus)
        captures = []
        try:
            for offset in reversed(range(args.minutes)):
                bucket_end = end - timedelta(minutes=offset)
                for service in BUSINESS_ROUTES:
                    capture = source.capture(service, bucket_end)
                    store.write("raw", capture)  # Preserve progress if a later source query fails.
                    captures.append(capture)
        finally:
            source.close()
    result = process_captures(captures, store)
    print(json.dumps(result, indent=2))
    if result["quarantine_refs"]:
        raise SystemExit("one or more source captures were quarantined")


if __name__ == "__main__":
    main()
