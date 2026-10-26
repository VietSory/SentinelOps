import argparse
import json
from pathlib import Path

from sentinelops.public_data.benchmark import run_benchmark


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--protocol",type=Path,default=Path("configs/public_smd_benchmark.json"))
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    report=run_benchmark(args.manifest,args.protocol,args.output,project=Path.cwd())
    print(json.dumps(report,indent=2))
