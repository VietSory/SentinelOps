"""Execute the bounded approved nonfinal capture plan; never train or activate."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from sentinelops.experiments.local_campaign import run_campaign
from sentinelops.telemetry.store import ArtifactStore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--follow-existing',action='store_true',help='Wait for an already registered active slot; never rerun it.')
    parser.add_argument('--plan',type=Path,default=None,help='ADR-015 detector-event plan for the evaluation-only epoch.')
    parser.add_argument('--bundle', type=Path, help='Alibaba v3 bundle to pin before ADR-016 capture.')
    parser.add_argument('--manifest-sha256', help='Trusted manifest SHA-256 for the Alibaba bundle.')
    parser.add_argument('--window-end', type=datetime.fromisoformat,
                        help='Timezone-aware end of host availability; a new group starts only if it fits.')
    args = parser.parse_args()
    if args.window_end is not None and args.window_end.tzinfo is None:
        parser.error('--window-end needs an explicit UTC offset')
    print(json.dumps(run_campaign(ArtifactStore(args.root), follow_existing=args.follow_existing, plan_path=args.plan,
                                  bundle=args.bundle, manifest_sha256=args.manifest_sha256,
                                  window_end=args.window_end), indent=2))
