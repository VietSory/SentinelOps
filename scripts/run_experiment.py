"""Run a bounded local S0/S1/S3 experiment with a predeclared data split."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import signal

from sentinelops.experiments.runner import ExperimentConfig, execute


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--scenario",choices=["S0","S1","S3"],required=True)
    parser.add_argument("--split",choices=["train","validation","test"],required=True)
    parser.add_argument("--output",type=Path,default=Path("artifacts/experiments"))
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--rps",type=float,default=3.)
    parser.add_argument("--warmup-seconds",type=int,default=300)
    parser.add_argument("--active-seconds",type=int,default=300)
    parser.add_argument("--recovery-seconds",type=int,default=180)
    parser.add_argument("--fault-mode",choices=["latency","error"],default="latency")
    parser.add_argument("--maximum-delay-ms",type=int,default=1200)
    parser.add_argument("--session-id")
    parser.add_argument("--allow-dirty",action="store_true")
    args=parser.parse_args()
    config=ExperimentConfig(args.scenario,args.split,args.seed,args.rps,args.warmup_seconds,args.active_seconds,
                            args.recovery_seconds,args.fault_mode,args.maximum_delay_ms)
    def terminate(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    try:
        asyncio.run(execute(config,project=Path.cwd(),output=args.output,session_id=args.session_id,allow_dirty=args.allow_dirty))
    except (ValueError,RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
