from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from sentinelops.public_data.downloads import download_rcaeval
from sentinelops.public_data.rcaeval_partitions import (
    INSPECTED, canonical, group_metadata, load_partitions,
    partition_metadata, read_index, require_cases,
)


def row(case, start, end, *, system="ob", service="a", fault="cpu"):
    return {"case": case, "system": system, "root_cause_service": service, "fault": fault,
            "time_start": start, "time_end": end, "inject_time": (start + end) // 2}


def test_overlap_closure_crosses_systems_and_fault_family_links_distant_repetitions():
    rows = [row("a", 1000, 1100), row("b", 100000, 100100),
            row("c", 100050, 100150, system="ss", service="b"),
            row("d", 100140, 100200, system="tt", service="c"),
            row("e", 1000000, 1000100, service="other")]
    groups = group_metadata(rows)
    assert sorted(sorted(g["cases"]) for g in groups) == [["a", "b", "c", "d"], ["e"]]
    assert group_metadata(list(reversed(rows))) == groups


def test_buffer_boundary_joins_before_split_including_touching_intervals():
    a = row("a", 1000, 1100)
    b = row("b", 1700, 1800, service="b")
    assert len(group_metadata([a, b])) == 1
    b["time_start"] += 1
    assert len(group_metadata([a, b])) == 2


def test_invalid_label_clock_does_not_remove_case_from_capture_group():
    a = row("a", 1000, 1100)
    a["inject_time"] = 1
    b = row("b", 1050, 1200, system="ss")
    assert group_metadata([a, b])[0]["cases"] == ["a", "b"]


@pytest.mark.parametrize("change", ["duplicate", "reverse-time", "missing-system", "float-time", "bool-time", "missing-case"])
def test_bad_group_metadata_fails_instead_of_inventing_sessions(change):
    rows = [row("a", 1000, 1100)]
    if change == "duplicate": rows.append(deepcopy(rows[0]))
    elif change == "reverse-time": rows[0]["time_end"] = 999
    elif change == "missing-system": del rows[0]["system"]
    elif change == "missing-case": del rows[0]["case"]
    elif change == "float-time": rows[0]["time_start"] = 1.0
    else: rows[0]["time_start"] = True
    with pytest.raises(ValueError): group_metadata(rows)


def test_inspected_cases_drag_their_entire_connected_group_out_of_test():
    rows = [row(name, 1000, 1100) for name in INSPECTED]
    rows += [row("neighbor", 1050, 1200, system="ss", service="neighbor")]
    rows += [row(f"fresh-{i}", 10000 * (i + 1), 10000 * (i + 1) + 100, service=f"new-{i}") for i in range(6)]
    config = partition_metadata(rows)
    exposed = [g for g in config["assignments"] if g["previously_inspected"]]
    assert len(exposed) == 1 and exposed[0]["role"] == "development"
    assert "neighbor" in exposed[0]["cases"]
    assert partition_metadata(list(reversed(rows))) == config
    assert sum(g["role"] == "sealed-holdout" for g in config["assignments"]) == 2
    assert sum(g["role"] == "validation" for g in config["assignments"]) == 2


def test_insufficient_unexposed_groups_does_not_reassign_inspected_data():
    with pytest.raises(ValueError, match="insufficient"):
        partition_metadata([row(name, 1000, 1100) for name in INSPECTED])


def test_committed_policy_accounts_for_every_case_and_locks_whole_exposed_groups():
    config = load_partitions()
    cases = [name for group in config["assignments"] for name in group["cases"]]
    assert len(cases) == len(set(cases)) == 735
    for group in config["assignments"]:
        if group["previously_inspected"]:
            assert group["role"] == "development"
    assert set(require_cases(list(INSPECTED)).values()) == {"development"}
    sealed = next(g["cases"][0] for g in config["assignments"] if g["role"] == "sealed-holdout")
    with pytest.raises(ValueError, match="sealed"):
        require_cases([INSPECTED[0], sealed])
    with pytest.raises(ValueError, match="inventory"):
        require_cases(["re1ob_not-in-the-pinned-release_cpu_1"])


def test_tampered_assignment_or_open_flag_cannot_unlock_heldout(tmp_path):
    for mutation in ("role", "open", "seed", "membership"):
        config = load_partitions()
        heldout = next(g for g in config["assignments"] if g["role"] == "sealed-holdout")
        case = heldout["cases"][0]
        if mutation == "role": heldout["role"] = "development"
        elif mutation == "open": config["holdout_opened"] = True
        elif mutation == "seed": config["seed"] = 43
        else: heldout["cases"].pop()
        with pytest.raises(ValueError, match="frozen"):
            require_cases([case], config)
        p = tmp_path / "partition.json"
        p.write_text(json.dumps(config))
        with pytest.raises(ValueError, match="frozen"):
            load_partitions(p)


def test_downloader_rejects_entire_mixed_selection_before_network_or_raw_reads(tmp_path, monkeypatch):
    config = json.loads(Path("configs/public_datasets.json").read_text())["rcaeval"]
    heldout = next(g["cases"][0] for g in load_partitions()["assignments"] if g["role"] == "sealed-holdout")
    monkeypatch.setattr("sentinelops.public_data.downloads.fetch_file", lambda *a, **k: pytest.fail("case file accessed"))
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("publisher network accessed"))) as client:
        for selection in ([heldout], [INSPECTED[0], heldout], ["re1ob_unknown_cpu_1"]):
            with pytest.raises(ValueError):
                download_rcaeval(client, config, tmp_path, cases=selection)
    assert not list(tmp_path.iterdir())


def test_default_acquisition_still_passes_local_admission_before_network(tmp_path):
    config = json.loads(Path("configs/public_datasets.json").read_text())["rcaeval"]
    seen = []
    def publisher(request):
        seen.append(str(request.url))
        raise httpx.ConnectError("synthetic publisher unavailable")
    with httpx.Client(transport=httpx.MockTransport(publisher)) as client:
        with pytest.raises(httpx.ConnectError): download_rcaeval(client, config, tmp_path)
    assert len(seen) == 1 and not list(tmp_path.iterdir())


def save_manifest(root, data):
    target = root / "manifests" / (hashlib.sha256(canonical(data)).hexdigest() + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical(data))
    return target


def test_metadata_reader_rejects_manifest_tampering_before_index_access(tmp_path):
    p = save_manifest(tmp_path, {})
    p.write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        read_index(p)


def test_metadata_reader_never_accepts_wrong_revision_or_unlisted_index(tmp_path):
    base = {"manifest_version": "public-download/v1", "dataset": "RCAEval", "revision": "wrong",
            "activation_allowed": False, "files": []}
    with pytest.raises(ValueError, match="unsupported"):
        read_index(save_manifest(tmp_path, base))
    base["revision"] = load_partitions()["revision"]
    with pytest.raises(ValueError, match="missing/duplicated"):
        read_index(save_manifest(tmp_path, base))


def test_index_bytes_must_match_frozen_identity_not_only_a_self_consistent_manifest(tmp_path):
    revision = load_partitions()["revision"]
    index = tmp_path / "rcaeval" / revision / "cases.parquet"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"different index")
    digest = hashlib.sha256(index.read_bytes()).hexdigest()
    data = {"manifest_version": "public-download/v1", "dataset": "RCAEval", "revision": revision,
            "activation_allowed": False, "files": [{"path": str(index), "sha256": digest,
                "publisher_checksum": {"size": index.stat().st_size, "sha256": digest}}]}
    with pytest.raises(ValueError, match="frozen"):
        read_index(save_manifest(tmp_path, data))


def test_existing_manifest_reader_cannot_bypass_holdout_guard_by_reading_cached_files(tmp_path):
    from sentinelops.public_data.audit import load_verified_manifest
    heldout = next(g["cases"][0] for g in load_partitions()["assignments"] if g["role"] == "sealed-holdout")
    data = {"manifest_version": "public-download/v1", "dataset": "RCAEval",
            "revision": load_partitions()["revision"], "activation_allowed": False,
            "selection": [INSPECTED[0], heldout], "files": [{"path": str(tmp_path / "must-not-be-read.parquet")}]}
    with pytest.raises(ValueError, match="sealed"):
        load_verified_manifest(save_manifest(tmp_path, data))
    data["revision"] = "different"
    data["selection"] = list(INSPECTED)
    with pytest.raises(ValueError, match="revision"):
        load_verified_manifest(save_manifest(tmp_path, data))


@pytest.mark.parametrize("hidden", ["sealed", "unselected-development", "different-revision", "outside-layout", "wrong-dataset-label", "duplicate"])
def test_declared_development_selection_cannot_hide_a_later_unadmitted_file(tmp_path, monkeypatch, hidden):
    from sentinelops.public_data.audit import load_verified_manifest
    config = load_partitions()
    revision = config["revision"]
    base = tmp_path / "rcaeval" / revision
    if hidden == "sealed":
        case = next(g["cases"][0] for g in config["assignments"] if g["role"] == "sealed-holdout")
        bad = base / case / "traces.parquet"
    elif hidden == "unselected-development":
        case = next(c for g in config["assignments"] if g["role"] == "development" for c in g["cases"] if c not in INSPECTED)
        bad = base / case / "metrics.parquet"
    elif hidden == "different-revision":
        bad = tmp_path / "rcaeval" / "other-revision" / INSPECTED[0] / "metrics.parquet"
    elif hidden == "outside-layout":
        bad = base / INSPECTED[0] / "unlisted.bin"
    else:
        bad = base / INSPECTED[0] / "metrics.parquet"
    data = {"manifest_version": "public-download/v1", "dataset": "RCAEval", "revision": revision,
            "activation_allowed": False, "selection": [INSPECTED[0]],
            "files": [{"path": str(base / "README.md")}, {"path": str(bad)}]}
    if hidden == "wrong-dataset-label": data["dataset"] = "SMD"
    if hidden == "duplicate": data["files"].append(deepcopy(data["files"][-1]))
    selected = save_manifest(tmp_path, data)
    original = Path.read_bytes
    def deny_cache(path):
        if path.resolve().is_relative_to(tmp_path / "rcaeval"):
            pytest.fail("raw/cache bytes were touched before the entire manifest was admitted")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", deny_cache)
    with pytest.raises(ValueError):
        load_verified_manifest(selected)


def test_revision_symlink_cannot_move_the_admitted_cache_outside_public_root(tmp_path):
    from sentinelops.public_data.rcaeval_partitions import REVISION, require_manifest_files
    public = tmp_path / "public"
    (public / "rcaeval").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (public / "rcaeval" / REVISION).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        require_manifest_files({"selection": list(INSPECTED), "files": [
            {"path": str(public / "rcaeval" / REVISION / "README.md")}]}, public)
