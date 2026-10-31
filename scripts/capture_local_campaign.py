"""Execute the bounded approved nonfinal capture plan; never train or activate."""
import argparse
import json
from pathlib import Path
from sentinelops.experiments.local_campaign import run_campaign
from sentinelops.telemetry.store import ArtifactStore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--follow-existing',action='store_true',help='Wait for an already registered active slot; never rerun it.')
    args = parser.parse_args()
    print(json.dumps(run_campaign(ArtifactStore(args.root),follow_existing=args.follow_existing),indent=2))
