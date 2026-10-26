from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from sentinelops.public_data.cohort import (
    bundle_bytes, catalog_identity, cohort_plan, download_cohort, load_cohort_manifest, read_bundle,
)
from sentinelops.public_data.gamma import META_FILES, METRICS, canonical_bytes
from sentinelops.public_data.gamma_archive import RangeReader, archive_catalog
from test_gamma_archive import range_transport, zip_blob
from test_gamma_downloads import EXPERIMENT, source_files


def source():
    files = source_files()
    for metric in METRICS:
        files[f"raw_dataset/{EXPERIMENT}/prom_metrics/service_{metric}"] = b'[[1692000000,"1"],[1692000015,"2"]]'
    files[f"raw_dataset/{EXPERIMENT}/node_phases"] = b"native source log, unmodified\n"
    files[f"raw_dataset/{EXPERIMENT}/pods_describe/service-pod.log"] = b"Node: node/127.0.0.1\n"
    files["raw_dataset/incomplete_case/args.txt"] = b"{}"
    # Unselected large payload must not be read just to access preceding files.
    files["raw_dataset/other/unused.data"] = b"x" * 100_000
    return files


def prepare(client):
    catalog, reader, archive = archive_catalog(client)
    archive.close()
    reader.close()
    lock = catalog_identity(catalog)
    scope = {"scope_version": "gamma-raw-resource-cohort/v1", "catalog_sha256": lock["catalog_sha256"],
        "training_data_policy_hash": lock["training_data_policy_hash"],
        "experiment_rule": "all-raw-directories-with-args", "experiments": [EXPERIMENT, "incomplete_case"],
        "resource_metrics": list(METRICS), "include_pod_descriptions": True, "include_phase_logs": True,
        "global_metadata": sorted(META_FILES), "selection_uses_model_scores": False,
        "training_ready": False, "activation_allowed": False}
    return catalog, scope, lock


def test_all_args_cohort_is_source_isolated_byte_exact_and_replayable_with_a_tiny_tree_lock(tmp_path):
    original = source()
    transport, _ = range_transport(zip_blob(original))
    with httpx.Client(transport=transport) as client:
        _, scope, lock = prepare(client)
        first = download_cohort(client, tmp_path / "first", scope, lock, workers=2)
        second = download_cohort(client, tmp_path / "second", scope, lock, workers=2, expected_tree=first["file_tree_lock"])
    assert first["file_tree_lock"] == second["file_tree_lock"]
    manifest, groups = load_cohort_manifest(Path(first["manifest_path"]), scope, expected_tree=first["file_tree_lock"])
    assert manifest["experiments"] == 2 and manifest["training_ready"] is False
    assert set(groups) == {None, EXPERIMENT, "incomplete_case"}
    assert [record["role"] for record in groups["incomplete_case"]["members"]] == ["args"]
    for group in groups.values():
        for name, data in read_bundle(group).items():
            assert data == original[name]
            assert not name.startswith("processed_dataset/") and "unused.data" not in name
    assert manifest["role_counts"] == {"args": 2, "global-metadata": 4, "phase": 1, "placement": 1, "resource": 4}
    assert manifest["response_body_bytes"] < 100_000
    assert len({item["sha256"] for item in first["bundles"]}) == 3
    assert [item["sha256"] for item in first["bundles"]] == [item["sha256"] for item in second["bundles"]]


def test_bundle_packaging_is_deterministic_and_source_paths_and_empty_bytes_are_preserved():
    first = bundle_bytes({"raw_dataset/case/empty": b"", "raw_dataset/case/data": b"original\n"})
    second = bundle_bytes({"raw_dataset/case/data": b"original\n", "raw_dataset/case/empty": b""})
    assert first == second


def test_scope_cannot_omit_incomplete_cases_or_add_processed_rows_based_on_outcomes():
    transport, _ = range_transport(zip_blob(source()))
    with httpx.Client(transport=transport) as client:
        catalog, scope, _ = prepare(client)
    altered = deepcopy(scope)
    altered["experiments"].remove("incomplete_case")
    with pytest.raises(ValueError, match="all raw directories"):
        cohort_plan(catalog, altered)
    altered = dict(scope, selection_uses_model_scores=True)
    with pytest.raises(ValueError, match="predeclared"):
        cohort_plan(catalog, altered)


def test_wrong_file_tree_or_byte_budget_prevents_a_success_manifest(tmp_path):
    transport, _ = range_transport(zip_blob(source()))
    with httpx.Client(transport=transport) as client:
        _, scope, lock = prepare(client)
        with pytest.raises(ValueError, match="expanded budget"):
            download_cohort(client, tmp_path / "budget", scope, lock, max_expanded_bytes=1)
        with pytest.raises(ValueError, match="source file tree"):
            download_cohort(client, tmp_path / "tree", scope, lock, expected_tree={"source_file_tree_sha256": "0" * 64})
    assert not list(tmp_path.rglob("manifests/*.json"))


def test_verified_cohort_rejects_mutated_bundle_and_incomplete_self_hashed_manifest(tmp_path):
    transport, _ = range_transport(zip_blob(source()))
    with httpx.Client(transport=transport) as client:
        _, scope, lock = prepare(client)
        result = download_cohort(client, tmp_path, scope, lock)
    path = Path(result["manifest_path"])
    altered = json.loads(path.read_text())
    altered["bundles"].pop()
    blob = canonical_bytes(altered)
    alternate = path.parent / (hashlib.sha256(blob).hexdigest() + ".json")
    alternate.write_bytes(blob)
    with pytest.raises(ValueError, match="coverage/tree"):
        load_cohort_manifest(alternate, scope)
    Path(result["bundles"][0]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="bundle size"):
        load_cohort_manifest(path, scope)


def test_concurrent_prefetch_reserves_the_total_budget_before_network():
    transport, _ = range_transport(zip_blob({"data": b"x" * 100_000}))
    with httpx.Client(transport=transport) as client:
        reader = RangeReader(client, "https://publisher.test/archive", max_bytes=65540, max_request_bytes=65536)
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(reader.fetch, "bytes=0-3") for _ in range(4)]
            successes = 0
            for job in jobs:
                try:
                    job.result()
                    successes += 1
                except ValueError as exc:
                    assert "budget" in str(exc)
        assert successes == 1 and reader.body_bytes == reader.reserved_bytes == 65540
