"""Acquire a bounded GAMMA v1 capability-audit scope; never provider code."""
import argparse
import json
from pathlib import Path

import httpx

from sentinelops.public_data.gamma import download_gamma


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", action="append")
    parser.add_argument("--output", type=Path, default=Path("data/raw/public"))
    parser.add_argument("--max-bytes", type=int, default=256_000_000)
    parser.add_argument("--max-wire-bytes", type=int, default=128_000_000)
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--file-lock", type=Path,
                        help="Project-frozen file hashes for the exact selected acquisition scope")
    parser.add_argument("--include-phase-evidence", action="store_true")
    parser.add_argument("--phase-extension", action="store_true",
                        help="Retain all base hashes; acquire only additional native VM phase logs")
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
            result = download_gamma(client, args.output, experiments=args.experiment, max_bytes=args.max_bytes,
                                    max_total_wire_bytes=args.max_wire_bytes, max_pages=args.max_pages,
                                    file_lock=json.loads(args.file_lock.read_text()) if args.file_lock else None,
                                    include_phase_evidence=args.include_phase_evidence, phase_extension=args.phase_extension)
    except httpx.HTTPError as exc:
        raise SystemExit("GAMMA publisher unavailable: " + type(exc).__name__) from None
    summary = {key: value for key, value in result.items() if key != "files"}
    if args.result_file:
        args.result_file.parent.mkdir(parents=True, exist_ok=True)
        with args.result_file.open("x") as stream:
            json.dump(summary, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
