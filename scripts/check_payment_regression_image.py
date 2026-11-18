"""Check the canonical, digest-pinned Payment regression Dockerfile."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.payment_regression import dockerfile, validate_base


DOCKERFILE = Path(__file__).resolve().parents[1] / 'docker' / 'Dockerfile.payment-regression'


def check(base: str, path: Path = DOCKERFILE) -> None:
    """Reject an invalid base or a tracked Dockerfile that drifts from its source."""
    validate_base(base)
    if path.read_text() != dockerfile(base):
        raise ValueError(f'{path} must equal scripts.payment_regression.dockerfile(BASE)')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True,
                        help='digest-pinned <registry>/sentinelops/application@sha256:<64hex> base')
    args = parser.parse_args()
    check(args.base)


if __name__ == '__main__':
    main()
