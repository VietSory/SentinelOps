import argparse
import json
from pathlib import Path

from sentinelops.public_data.resources import build_resource_windows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path("configs/gamma_resource_profile.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_resource_windows(args.manifest, args.profile, args.output, project=Path.cwd())
    print(json.dumps({key: value for key, value in report.items() if key != "raw_input_sha256"}, indent=2))
