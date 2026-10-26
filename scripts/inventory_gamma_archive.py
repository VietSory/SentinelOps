import argparse
import json
from pathlib import Path

import httpx

from sentinelops.public_data.gamma_archive import freeze_archive_catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/raw/public"))
    parser.add_argument("--file-lock", type=Path)
    parser.add_argument("--result-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
            result = freeze_archive_catalog(client, args.root,
                                            expected_lock=json.loads(args.file_lock.read_text()) if args.file_lock else None,
                                            project=Path.cwd())
    except httpx.HTTPError as exc:
        raise SystemExit("GAMMA archive unavailable: " + type(exc).__name__) from None
    args.result_file.parent.mkdir(parents=True, exist_ok=True)
    with args.result_file.open("x") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
