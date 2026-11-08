"""Export the frozen corpus's training source shapes for portable parity checks.

A runs this on the frozen fitting corpus. The manifest carries only query text,
source profile and histogram bounds per route (no observations), bound to the
freeze reference. Send its SHA-256 over a separate trusted channel; B passes both
to `check_model_source_parity`. Raw corpus data never leaves A.
"""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.check_model_source_parity import training_shapes
from sentinelops.experiments.local_corpus import read_binding
from sentinelops.telemetry.store import ArtifactStore

SHAPES_VERSION = "training-source-shapes/v1"


def export(corpus):
    freeze_ref, freeze = read_binding(ArtifactStore(corpus), Path(corpus) / "dataset-freeze.json")
    _, shapes = training_shapes(corpus)
    return {"shapes_version": SHAPES_VERSION, "dataset_ref": freeze_ref, "source_profile": freeze["source_profile"],
            "feature_set_version": freeze["feature_set_version"], "ordered_features": freeze["ordered_features"],
            "routes": shapes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = (json.dumps(export(args.corpus), indent=2, sort_keys=True) + "\n").encode()
    with args.output.open("xb") as stream:
        stream.write(data)
    print(json.dumps({"output": str(args.output), "sha256": hashlib.sha256(data).hexdigest()}))


if __name__ == "__main__":
    main()
