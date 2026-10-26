import argparse
import json
from pathlib import Path

import httpx

from sentinelops.public_data.cohort import download_cohort


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/raw/public"))
    parser.add_argument("--scope", type=Path, default=Path("configs/gamma_resource_cohort_scope.json"))
    parser.add_argument("--catalog-lock", type=Path, default=Path("configs/gamma_v1_archive_catalog.json"))
    parser.add_argument("--file-tree-lock", type=Path)
    parser.add_argument("--result-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
            result = download_cohort(client, args.root, json.loads(args.scope.read_text()),
                json.loads(args.catalog_lock.read_text()),
                expected_tree=json.loads(args.file_tree_lock.read_text()) if args.file_tree_lock else None,
                project=Path.cwd())
    except httpx.HTTPError as exc:
        raise SystemExit("GAMMA cohort archive unavailable: " + type(exc).__name__) from None
    summary = {key: value for key, value in result.items() if key not in ("bundles", "case_role_counts")}
    args.result_file.parent.mkdir(parents=True, exist_ok=True)
    with args.result_file.open("x") as stream:
        stream.write(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "scope"}, indent=2))
