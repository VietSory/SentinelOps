"""Download audited public data; never execute third-party dataset code."""
import argparse
import json
from pathlib import Path

import httpx

from sentinelops.public_data.downloads import download_rcaeval,download_smd


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--dataset",choices=["rcaeval","smd"],required=True)
    parser.add_argument("--case",action="append")
    parser.add_argument("--machine")
    parser.add_argument("--output",type=Path,default=Path("data/raw/public"))
    args=parser.parse_args()
    catalog=json.loads(Path("configs/public_datasets.json").read_text())
    try:
        with httpx.Client(timeout=60,follow_redirects=True,trust_env=False) as client:
            if args.dataset=="rcaeval":
                result=download_rcaeval(client,catalog["rcaeval"],args.output,cases=args.case)
            else:
                result=download_smd(client,catalog["smd"],args.output,machine=args.machine)
    except httpx.HTTPError as exc:
        raise SystemExit("Publisher download unavailable: "+type(exc).__name__) from None
    print(json.dumps(result,indent=2))


if __name__=="__main__": main()
