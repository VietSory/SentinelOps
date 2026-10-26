import argparse
import json
from pathlib import Path

from sentinelops.public_data.benchmark import run_benchmark
from sentinelops.public_data.policy import require_historical_smd


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--protocol",type=Path,default=Path("configs/public_smd_benchmark.json"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--historical-reproduction",action="store_true",
                        help="Explicitly reproduce the excluded historical machine-1-1 experiment")
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text())
    require_historical_smd(manifest["selection"][0],acknowledged=args.historical_reproduction)
    report=run_benchmark(args.manifest,args.protocol,args.output,project=Path.cwd(),historical_reproduction=args.historical_reproduction)
    print(json.dumps(report,indent=2))
