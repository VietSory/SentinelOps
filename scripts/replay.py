"""Foundation replay entry.

The replay contract is frozen early so later detector/RCA evaluations can consume the
same external scenario format. At Foundation stage this command validates JSONL
telemetry only; model inference is added in the ML milestone.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts" / "telemetry.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", help="JSONL file containing one TelemetryPoint per line")
    args = parser.parse_args()

    path = Path(args.scenario)
    count = 0
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        errors = list(VALIDATOR.iter_errors(payload))
        if errors:
            raise SystemExit(f"line {line_no}: {errors[0].message}")
        count += 1
    print(f"Replay input accepted: {count} telemetry points")


if __name__ == "__main__":
    main()
