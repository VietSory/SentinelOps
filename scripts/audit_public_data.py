import argparse
import json
from pathlib import Path

from sentinelops.public_data.audit import audit_rcaeval,audit_smd


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--dataset",choices=["rcaeval","smd"],required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    audit=(audit_rcaeval if args.dataset=="rcaeval" else audit_smd)(args.manifest)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(audit,indent=2,allow_nan=False)+'\n')
    print(json.dumps(audit,indent=2))
