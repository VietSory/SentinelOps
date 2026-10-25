"""Capture a real read-only local DB observation; DSN is supplied only via env."""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from sentinelops.telemetry.infrastructure import PostgresInfrastructureSource
from sentinelops.telemetry.store import ArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required; credentials are never included in observation output")
    item = asyncio.run(PostgresInfrastructureSource(dsn).capture())
    reference = ArtifactStore(args.output).write("observations", item)
    print(json.dumps({"reference":reference, "observation":item}, indent=2))


if __name__ == "__main__":
    main()
