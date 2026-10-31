"""Record an actual post-run context observation for one nonsealed capture."""
import argparse
from pathlib import Path
from sentinelops.experiments.local_corpus import inspect_completed_capture
from sentinelops.telemetry.store import ArtifactStore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--slot',required=True)
    args=parser.parse_args()
    print(inspect_completed_capture(ArtifactStore(args.root),args.slot))
