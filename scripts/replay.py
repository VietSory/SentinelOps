"""Foundation replay entry.

The replay contract is frozen early so later detector/RCA evaluations can consume the
same external scenario format. At Foundation stage this command validates JSONL
telemetry only; model inference is added in the ML milestone.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinelops.contracts import validate_payload


def validate_jsonl(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                validate_payload("telemetry.schema.json", payload)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"line {line_no}: {exc}") from exc
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", help="JSONL file containing one TelemetryPoint per line")
    args = parser.parse_args()

    path = Path(args.scenario)
    try:
        count = validate_jsonl(path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Replay input accepted: {count} telemetry points")


if __name__ == "__main__":
    main()
