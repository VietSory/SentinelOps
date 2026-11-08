"""ADR-016: aggregate checksummed Alibaba MSRTMCR archives into per-service HTTP minutes."""
import argparse
from pathlib import Path

from sentinelops.public_data.alibaba import aggregate_directory

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    aggregate_directory(args.raw, args.output)
