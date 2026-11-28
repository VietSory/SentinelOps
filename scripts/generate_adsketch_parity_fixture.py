"""Record data from the external OpsPAI/ADSketch reference for the v17 parity test.

From the repository root, use the pinned B01 reference environment (its
versions are reported in the JSON). On NixOS, export LD_LIBRARY_PATH from
NIX_LD_LIBRARY_PATH if needed. The reference checkout must be outside this repo.

    PYTHONDONTWRITEBYTECODE=1 NUMBA_NUM_THREADS=2 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
      /tmp/sentinelops-b-evidence/reference/env/bin/python \
      scripts/generate_adsketch_parity_fixture.py \
      /tmp/sentinelops-b-evidence/reference/ADSketch
"""

import argparse
from importlib.metadata import version
from itertools import groupby
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/adsketch_parity"
REFERENCE_REVISION = "f7bd642c93ff25612514ed711f47cc095a6a66fd"


def expand_input(fixture):
    normal = fixture["normal_period"] * fixture["normal_periods"]
    tile = fixture["normal_period"] * fixture["discovery_periods_per_tile"]
    labels = [0] * len(tile)
    for episode in fixture["episodes"]:
        start = episode["start"]
        stop = start + len(episode["values"])
        tile[start:stop] = episode["values"]
        labels[start:stop] = [1] * len(episode["values"])
    return normal, tile * fixture["discovery_tiles"], labels * fixture["discovery_tiles"]


def author_output(reference, inputs):
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip()
    if revision != REFERENCE_REVISION:
        raise ValueError(f"Expected ADSketch revision {REFERENCE_REVISION}, got {revision}")
    status = subprocess.check_output(
        ["git", "-C", str(reference), "status", "--porcelain", "--untracked-files=no"], text=True,
    )
    if status.strip():
        raise ValueError("Expected unchanged tracked files in the ADSketch checkout")
    sys.path.insert(0, str(reference))

    import numpy as np
    from adsketch.motif_operations import anomaly_pattern_discovery
    from adsketch.utils import scale_two_metrics, seed_everything

    normal, discovery, _ = expand_input(inputs)
    window_length = inputs["window_length"]
    seed_everything(inputs["seed"])
    scaled_normal, scaled_discovery = scale_two_metrics(np.array(normal), np.array(discovery))
    _, anomalies, counts, centers, radii = anomaly_pattern_discovery(
        scaled_normal, scaled_discovery, window_length, inputs["discovery_quantile"] * 100,
    )
    windows = np.lib.stride_tricks.sliding_window_view(scaled_discovery, window_length)
    distances = np.linalg.norm(windows[:, None, :] - np.asarray(centers)[None, :, :], axis=2)
    return {
        "reference_revision": revision,
        "versions": {package: version(package) for package in (
            "networkx", "numba", "numpy", "scikit-learn", "scipy", "stumpy",
        )},
        "patterns": [
            {"kind": "anomalous" if index in anomalies else "normal", "count": int(count),
             "center": np.asarray(center).tolist(), "radius": float(radius)}
            for index, (count, center, radius) in enumerate(zip(counts, centers, radii, strict=True))
        ],
        "nearest_pattern_runs": [
            [int(pattern), sum(1 for _ in run)] for pattern, run in groupby(distances.argmin(axis=1))
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="External ADSketch checkout at the pinned revision")
    parser.add_argument("--output", type=Path, default=FIXTURES / "author.json")
    args = parser.parse_args()
    reference = args.reference.resolve()
    if reference.is_relative_to(ROOT):
        parser.error("The ADSketch reference must be outside the repository")
    inputs = json.loads((FIXTURES / "input.json").read_text())
    args.output.write_text(json.dumps(author_output(reference, inputs), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
