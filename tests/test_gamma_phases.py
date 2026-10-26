from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from sentinelops.contracts import validate_payload
from sentinelops.public_data.gamma import download_gamma
from sentinelops.public_data.phases import audit_phase_labels, classify_window, overlap_seconds, parse_phases
from sentinelops.public_data.resources import build_resource_windows
from test_gamma_downloads import EXPERIMENT, transport
from test_gamma_resources import PROFILE, source_fixture


def phase_text(start=1692000500, end=1692000700):
    return (f"Bottleneck of type cpu with measure 0.75 starts at {start}\n"
            f"Bottleneck of type CPU with measure 0.75 ends at {end}\n")


def test_native_phase_parser_pairs_case_insensitive_records_without_deriving_labels_from_metrics():
    events = parse_phases(phase_text(), node="node", source_sha256="a" * 64)
    assert len(events) == 1 and events[0]["kind"] == "cpu"
    assert events[0]["start"] == 1692000500 and events[0]["end"] == 1692000700
    other = parse_phases(phase_text(), node="other", source_sha256="a" * 64)
    assert other[0]["event_id"] != events[0]["event_id"]


@pytest.mark.parametrize("text", ["", "schedule: cpu 75%", phase_text().splitlines()[0],
    phase_text().splitlines()[1], phase_text(1692000700, 1692000500),
    phase_text(1692000500, 1692000500), phase_text(1692000500000000, 1692000700000000),
    phase_text().replace("type CPU", "type mem"),
    phase_text().splitlines()[0] + "\n" + phase_text()])
def test_missing_ambiguous_reversed_or_wrong_unit_phase_truth_is_not_repaired(text):
    with pytest.raises(ValueError):
        parse_phases(text, node="node", source_sha256="a" * 64)


def event(node, begin, end):
    return {"node": node, "start": begin, "end": end,
            "event_id": hashlib.sha256(f"{node}/{begin}/{end}".encode()).hexdigest()}


def test_other_host_interference_and_boundary_uncertainty_never_become_nominal_references():
    events = [event("target", 100, 200), event("target", 150, 250)]
    direct = classify_window(events, "target", 0, 300)
    assert direct["direct_interference_seconds"] == 150
    assert overlap_seconds(events, 0, 300) == 150
    assert classify_window(events, "peer", 0, 300)["label"] == "other-host-interference-context"
    assert classify_window(events, "peer", 250, 550)["label"] == "boundary-uncertain"
    assert classify_window(events, "peer", 251, 551)["label"] == "no-recorded-interference-reference"
    assert classify_window(events, "target", -200, 100)["label"] == "boundary-uncertain"


def acquire(tmp_path, *, targets=None, start=1692000500):
    files = source_fixture(truth_marker="unused")
    files[f"raw_dataset/{EXPERIMENT}/args.txt"] = json.dumps({"bottlenecked_nodes": ["node"] if targets is None else targets}).encode()
    files[f"raw_dataset/{EXPERIMENT}/node_phases"] = phase_text(start, start + 200).encode()
    with httpx.Client(transport=transport(files)) as client:
        acquisition = download_gamma(client, tmp_path / "source", experiments=[EXPERIMENT], workers=1,
                                     include_phase_evidence=True)
    manifest = Path(acquisition["manifest_path"])
    resources = tmp_path / "resources"
    build_resource_windows(manifest, PROFILE, resources)
    return manifest, resources


def test_real_join_shape_preserves_features_and_emits_separate_hashed_labels(tmp_path):
    manifest, resources = acquire(tmp_path)
    original = (resources / "resource-windows.jsonl").read_bytes()
    output = tmp_path / "labels"
    report = audit_phase_labels(manifest, resources, output)
    assert report["rows"] == 11 and report["training_ready"] is False
    assert report["label_counts"] == {"direct-recorded-interference": 7, "no-recorded-interference-reference": 4}
    assert (resources / "resource-windows.jsonl").read_bytes() == original
    labels = [json.loads(line) for line in (output / "interference-labels.jsonl").read_text().splitlines()]
    assert all("features" not in row and row["precursor_target_available"] is False for row in labels)
    assert labels[0]["resource_window_sha256"] == hashlib.sha256(original.splitlines()[0]).hexdigest()
    altered = deepcopy(labels[0])
    altered["label"] = "no-recorded-interference-reference"
    with pytest.raises(ValueError, match="contradicts"):
        validate_payload("research-interference-label.schema.json", altered)
    (resources / "resource-windows.jsonl").write_bytes(original.replace(b"cpu_cores_mean_5m", b"invented_feature"))
    with pytest.raises(ValueError, match="frames/report"):
        audit_phase_labels(manifest, resources, tmp_path / "tampered")


@pytest.mark.parametrize("targets", [["node", "node"], ["other"], ["node", "other"], []])
def test_phase_coverage_cannot_omit_declared_targets_or_invent_unknown_placement_nodes(tmp_path, targets):
    manifest, resources = acquire(tmp_path, targets=targets)
    with pytest.raises(ValueError, match="targets|nodes"):
        audit_phase_labels(manifest, resources, tmp_path / "labels")


def test_source_clock_mismatch_fails_instead_of_declaring_every_window_nominal(tmp_path):
    manifest, resources = acquire(tmp_path, start=1692100500)
    with pytest.raises(ValueError, match="capture clock"):
        audit_phase_labels(manifest, resources, tmp_path / "labels")
