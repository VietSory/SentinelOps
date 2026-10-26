from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from sentinelops.public_data.admission import audit_cohort_admission
from sentinelops.public_data.cohort import catalog_identity, download_cohort
from sentinelops.public_data.dataset import build_grouped_dataset, split_assignments
from sentinelops.public_data.gamma import META_FILES, METRICS, canonical_bytes
from sentinelops.public_data.gamma_archive import archive_catalog
from test_gamma_admission import EPOCH, EXPERIMENT, PROFILE, case_content, phase
from test_gamma_archive import range_transport, zip_blob


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_PROFILE = ROOT / "configs/gamma_resource_profile.json"


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


def fixture(tmp_path, *, phase_offset=0):
    tmp_path.mkdir()
    files = {name: b"native metadata\n" for name in META_FILES}
    names = [EXPERIMENT, *[f"source_{index}_0" for index in range(1, 9)]]
    for index, name in enumerate(names):
        offset = index * 3 * 86400
        for path, blob in case_content().items():
            path = path.replace(EXPERIMENT, name)
            if "/prom_metrics/" in path:
                blob = json.dumps([[time + offset, value] for time, value in json.loads(blob)]).encode()
            elif path.endswith("_phases"):
                blob = phase(begin=EPOCH + offset + 500 + phase_offset, end=EPOCH + offset + 700 + phase_offset)
            files[path] = blob
    transport, _ = range_transport(zip_blob(files))
    with httpx.Client(transport=transport) as client:
        catalog, reader, archive = archive_catalog(client)
        archive.close(); reader.close()
        lock = catalog_identity(catalog)
        scope = json.loads((ROOT / "configs/gamma_resource_cohort_scope.json").read_text())
        scope.update(catalog_sha256=lock["catalog_sha256"], experiments=names)
        acquired = download_cohort(client, tmp_path / "raw", scope, lock)
    scope_path = write(tmp_path / "scope.json", scope)
    tree_path = write(tmp_path / "tree.json", acquired["file_tree_lock"])
    inspections = {"record_version": "gamma-development-inspections/v1", "source_scope_sha256": acquired["scope_sha256"],
        "holdout_excluded_experiments": [EXPERIMENT], "group_policy": "exclude-entire-connected-session-group-from-final-holdout"}
    inspection_path = write(tmp_path / "inspections.json", inspections)
    manifest = Path(acquired["manifest_path"])
    admission = audit_cohort_admission(manifest, scope_path, tree_path, PROFILE, inspection_path, tmp_path / "native")
    native = json.loads(Path(admission["report_path"]).read_text())
    groups = {"group_version": "gamma-source-groups/v1", "constraints_only": True,
        "source_file_tree_sha256": acquired["file_tree_lock"]["source_file_tree_sha256"],
        "admission_profile_sha256": native["profile_sha256"], "inspections_sha256": native["inspections_sha256"],
        "groups": native["groups"]}
    groups_path = write(tmp_path / "groups.json", groups)
    config = json.loads((ROOT / "configs/gamma_resource_splits.json").read_text())
    config.update({key: groups[key] for key in ("source_file_tree_sha256", "admission_profile_sha256", "inspections_sha256")})
    config["source_groups_sha256"] = hashlib.sha256(groups_path.read_bytes()).hexdigest()
    config["native_evidence_sha256"] = hashlib.sha256(canonical_bytes({key: native[key] for key in ("cases", "groups")})).hexdigest()
    rank = lambda group: hashlib.sha256(canonical_bytes({"seed": 42, "group_id": group["group_id"]})).hexdigest()
    candidates = sorted([group for group in groups["groups"] if group["final_holdout_eligible"]], key=rank)
    test = {group["group_id"] for group in candidates[:2]}
    calibration = {group["group_id"] for group in sorted(candidates[2:], key=lambda group: (-group["admitted_cases"], rank(group)))[:2]}
    config["assignments"] = []
    for group in groups["groups"]:
        identity = group["group_id"]
        role = "test" if identity in test else "calibration" if identity in calibration else "development" if EXPERIMENT in group["experiments"] else "train"
        config["assignments"].append({"group_id": identity, "split": role, "experiments": group["experiments"]})
    splits_path = write(tmp_path / "splits.json", config)
    args = (manifest, Path(admission["report_path"]), scope_path, tree_path, groups_path, splits_path, RESOURCE_PROFILE)
    return args, config, groups


def test_grouped_dataset_preserves_raw_rates_and_separates_truth_from_predictors(tmp_path):
    args, config, groups = fixture(tmp_path / "fixture")
    roles = split_assignments(config, groups)
    assert roles[EXPERIMENT]["split"] == "development"
    output = tmp_path / "dataset"
    report = build_grouped_dataset(*args, output)
    rows = [json.loads(line) for line in (output / "resource-windows.jsonl").read_text().splitlines()]
    truth = [json.loads(line) for line in (output / "interference-labels.jsonl").read_text().splitlines()]
    index = [json.loads(line) for line in (output / "window-index.jsonl").read_text().splitlines()]
    assert report["rows"] == len(rows) == len(truth) == len(index) == 99
    assert report["training_ready"] is False and report["risk_target_available"] is False
    assert report["source_reference_is_healthy_certificate"] is False
    assert {item["split"] for item in index} == {"train", "calibration", "development", "test"}
    for row, label, record in zip(rows, truth, index):
        assert row["features"]["cpu_cores_mean_5m"] == 1.
        assert row["features"]["cpu_cores_slope_5m"] == 0.
        assert not {"label", "split", "group_id", "events"} & row.keys()
        digest = hashlib.sha256(canonical_bytes(row)).hexdigest()
        assert label["resource_window_sha256"] == record["resource_window_sha256"] == digest
        assert record["split"] == roles[row["experiment"]]["split"]
    for filename, key in (("resource-windows.jsonl", "frame_sha256"), ("interference-labels.jsonl", "label_sha256"), ("window-index.jsonl", "index_sha256")):
        assert hashlib.sha256((output / filename).read_bytes()).hexdigest() == report[key]
    with pytest.raises(FileExistsError):
        build_grouped_dataset(*args, output)


def test_changed_or_inspected_split_is_rejected_before_creating_windows(tmp_path):
    args, config, groups = fixture(tmp_path / "fixture")
    changed = deepcopy(config)
    anchor = next(row for row in changed["assignments"] if EXPERIMENT in row["experiments"])
    anchor["split"] = "test"
    with pytest.raises(ValueError, match="assignments"):
        split_assignments(changed, groups)
    write(args[5], changed)
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="assignments"):
        build_grouped_dataset(*args, output)
    assert not output.exists()


def test_rehashed_admission_cannot_invent_no_interference_training_references(tmp_path):
    args, _, _ = fixture(tmp_path / "fixture")
    modified = json.loads(args[1].read_text())
    modified["cases"][0]["events"] = []
    blob = canonical_bytes(modified)
    path = args[1].parent / (hashlib.sha256(blob).hexdigest() + ".json")
    path.write_bytes(blob)
    changed_args = (args[0], path, *args[2:])
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="case/event values"):
        build_grouped_dataset(*changed_args, output)
    assert not output.exists()


def test_coherent_label_changes_cannot_enter_numeric_feature_values(tmp_path):
    reports, values, truths = [], [], []
    for variant, offset in enumerate((0, 240)):
        args, _, _ = fixture(tmp_path / f"fixture-{variant}", phase_offset=offset)
        output = tmp_path / f"dataset-{variant}"
        reports.append(build_grouped_dataset(*args, output))
        values.append([json.loads(line)["features"] for line in (output / "resource-windows.jsonl").read_text().splitlines()])
        truths.append([json.loads(line)["label"] for line in (output / "interference-labels.jsonl").read_text().splitlines()])
    assert values[0] == values[1] and truths[0] != truths[1]
    assert reports[0]["feature_values_sha256"] == reports[1]["feature_values_sha256"]
