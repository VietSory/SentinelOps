from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from sentinelops.public_data.gamma import (
    META_FILES, download_gamma, fetch_gamma_file, inventory_gamma, load_gamma_manifest,
)
from sentinelops.public_data.policy import load_policy, require_historical_smd, require_training_source


EXPERIMENT = "cpu_aug12_25min_200_0"


def publisher():
    return {"ref": load_policy()["sources"]["gamma"]["publisher"], "currentVersionNumber": 1,
            "licenseName": "Apache 2.0", "versions": [{"versionNumber": 1, "status": "Ready"}]}


def source_files():
    values = {name: b"source metadata\n" for name in META_FILES}
    for kind in ("compose", "home", "user"):
        values[f"processed_dataset/{kind}/multi-modal-data-separate/{EXPERIMENT}_graph_1.csv"] = b"header\n1\n"
    values[f"raw_dataset/{EXPERIMENT}/args.txt"] = b"{}"
    values[f"raw_dataset/{EXPERIMENT}/prom_metrics/service_container_cpu_usage_seconds_total"] = b"[[1,\"0\"],[2,\"1\"]]"
    return values


def transport(files=None, *, metadata=None, pages=None):
    files = source_files() if files is None else files
    metadata = publisher() if metadata is None else metadata

    def respond(request):
        if "/view/" in request.url.path:
            return httpx.Response(200, json=metadata)
        if "/list/" in request.url.path:
            assert request.url.params["datasetVersionNumber"] == "1"
            payload = pages if pages is not None else {"datasetFiles": [
                {"name": name, "totalBytes": len(blob)} for name, blob in sorted(files.items())]}
            return httpx.Response(200, json=payload)
        relative = request.url.path.split("/download/", 1)[1].split("/", 2)[2]
        assert request.url.params["datasetVersionNumber"] == "1"
        return httpx.Response(200, content=files[relative])
    return httpx.MockTransport(respond)


def test_acquisition_freezes_exact_scope_and_honest_integrity_provenance(tmp_path):
    with httpx.Client(transport=transport()) as client:
        result = download_gamma(client, tmp_path, experiments=[EXPERIMENT], workers=1)
    manifest, files, inventory = load_gamma_manifest(Path(result["manifest_path"]))
    assert set(files) == set(source_files())
    assert manifest["activation_allowed"] is False and inventory["scope_complete"] is True
    assert all(item["publisher_checksum"] is None for item in manifest["files"])
    assert all(item["checksum_origin"] == "locally-frozen-sha256" for item in manifest["files"])
    target = files[next(iter(files))]
    target.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        load_gamma_manifest(Path(result["manifest_path"]))


def test_policy_rejects_ml_fitting_from_evaluation_or_historical_sources():
    for source in ("RCAEval", "SMD", "unknown"):
        with pytest.raises(ValueError, match="GAMMA only"):
            require_training_source(source, load_policy())
    require_training_source("GAMMA", load_policy())
    with pytest.raises(ValueError):
        require_historical_smd("machine-1-2", acknowledged=True)
    with pytest.raises(ValueError):
        require_historical_smd("machine-1-1", acknowledged=False)


def test_legacy_downloader_cannot_expand_smd_or_replace_rcaeval_before_network(tmp_path):
    from sentinelops.public_data.downloads import download_rcaeval, download_smd
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("policy rejected source reached network"))) as client:
        with pytest.raises(ValueError, match="historical"):
            download_smd(client, {"default_machine": "machine-1-2"}, tmp_path)
        with pytest.raises(ValueError, match="frozen"):
            download_rcaeval(client, {"repository": "mirror/RCAEval", "revision": "0" * 40}, tmp_path)


@pytest.mark.parametrize("change", ["version", "license", "publisher", "not-ready"])
def test_changed_publisher_identity_never_silently_changes_the_frozen_source(tmp_path, change):
    value = publisher()
    if change == "version":
        value["currentVersionNumber"] = 2
    elif change == "license":
        value["licenseName"] = "Unknown"
    elif change == "publisher":
        value["ref"] = "mirror/gamma"
    else:
        value["versions"][0]["status"] = "Draft"
    with httpx.Client(transport=transport(metadata=value)) as client:
        with pytest.raises(ValueError):
            download_gamma(client, tmp_path, experiments=[EXPERIMENT])
    assert not list(tmp_path.rglob("*.csv"))


def test_inventory_rejects_duplicate_paths_instead_of_merging_conflicting_files():
    pages = {"datasetFiles": [{"name": "meta-data/a.csv", "totalBytes": 1}] * 2}
    with httpx.Client(transport=transport(pages=pages)) as client:
        with pytest.raises(ValueError, match="ordered and unique"):
            inventory_gamma(client, load_policy(), [EXPERIMENT])


def test_incomplete_inventory_or_missing_request_types_cannot_be_admitted():
    pages = {"datasetFiles": [{"name": sorted(META_FILES)[0], "totalBytes": 1}], "nextPageToken": "again"}
    with httpx.Client(transport=transport(pages=pages)) as client:
        with pytest.raises(ValueError, match="scope incomplete"):
            inventory_gamma(client, load_policy(), [EXPERIMENT], max_pages=1)
    files = source_files()
    files.pop(next(name for name in files if name.startswith("processed_dataset/home/")))
    with httpx.Client(transport=transport(files)) as client:
        with pytest.raises(ValueError, match="request-type coverage"):
            inventory_gamma(client, load_policy(), [EXPERIMENT])


def test_total_expanded_and_wire_limits_prevent_a_success_manifest(tmp_path):
    with httpx.Client(transport=transport()) as client:
        with pytest.raises(ValueError, match="expanded size budget"):
            download_gamma(client, tmp_path, experiments=[EXPERIMENT], max_bytes=1)
        with pytest.raises(ValueError, match="total wire budget"):
            download_gamma(client, tmp_path, experiments=[EXPERIMENT], max_total_wire_bytes=1, workers=1)
    assert not list((tmp_path / "manifests").glob("*.json"))


@pytest.mark.parametrize("member", ["../escape.csv", "wrong.csv"])
def test_single_file_archives_cannot_redirect_extraction(tmp_path, member):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(member, b"data")
    item = {"name": "processed_dataset/compose/data.csv", "size": 4}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=buffer.getvalue()))) as client:
        with pytest.raises(ValueError):
            fetch_gamma_file(client, "owner/source", tmp_path / "data.csv", item, max_expanded=10, max_wire=1000)
    assert not (tmp_path / "data.csv").exists()


def test_zip_crc_is_checked_and_a_non_data_script_is_not_downloaded(tmp_path):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", b"unique-original-content")
    blob = buffer.getvalue().replace(b"unique-original-content", b"unique-corrupted-contnt")
    item = {"name": "processed_dataset/compose/data.csv", "size": 23}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=blob))) as client:
        with pytest.raises(ValueError, match="integrity"):
            fetch_gamma_file(client, "owner/source", tmp_path / "data.csv", item, max_expanded=100, max_wire=1000)
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("script reached the network"))) as client:
        with pytest.raises(ValueError, match="non-data"):
            fetch_gamma_file(client, "owner/source", tmp_path / "rename.sh", {"name": "rename.sh", "size": 1}, max_expanded=10, max_wire=10)


def test_reader_rejects_a_self_hashed_manifest_that_omits_an_inventory_file(tmp_path):
    with httpx.Client(transport=transport()) as client:
        result = download_gamma(client, tmp_path, experiments=[EXPERIMENT], workers=1)
    value = deepcopy(result)
    value.pop("manifest_path")
    value["files"].pop()
    blob = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "manifests" / (hashlib.sha256(blob).hexdigest() + ".json")
    path.write_bytes(blob)
    with pytest.raises(ValueError, match="entire selected inventory"):
        load_gamma_manifest(path)


def test_project_frozen_hash_rejects_same_size_changed_source_bytes_before_publication(tmp_path):
    original = b"original"
    item = {"name": "processed_dataset/compose/data.csv", "size": len(original)}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"tampered"))) as client:
        with pytest.raises(ValueError, match="project-frozen file hash"):
            fetch_gamma_file(client, "owner/source", tmp_path / "data.csv", item, max_expanded=100, max_wire=100,
                             expected_sha256=hashlib.sha256(original).hexdigest())
    assert not (tmp_path / "data.csv").exists()


def file_lock(files):
    from sentinelops.public_data.policy import policy_hash
    return {"lock_version": "gamma-project-file-lock/v1", "source_version": 1,
            "training_data_policy_hash": policy_hash(load_policy()), "selection": [EXPERIMENT],
            "files": {name: {"size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
                      for name, blob in files.items()}}


def test_phase_scope_is_explicit_and_cannot_extend_a_lock_with_other_data(tmp_path):
    files = source_files()
    phase = f"raw_dataset/{EXPERIMENT}/userv2_phases"
    files[phase] = b"native phase evidence\n"
    with httpx.Client(transport=transport(files)) as client:
        old = inventory_gamma(client, load_policy(), [EXPERIMENT])
        assert phase not in {item["name"] for item in old["selected_files"]}
        assert "include_phase_evidence" not in old
        result = download_gamma(client, tmp_path, experiments=[EXPERIMENT], workers=1,
                                include_phase_evidence=True, phase_extension=True, file_lock=file_lock(source_files()))
    manifest, acquired, _ = load_gamma_manifest(Path(result["manifest_path"]))
    assert phase in acquired and manifest["phase_only_lock_extension"] is True
    files[f"raw_dataset/{EXPERIMENT}/prom_metrics/other_container_memory_usage_bytes"] = b"[]"
    with httpx.Client(transport=transport(files)) as client:
        with pytest.raises(ValueError, match="exact inventory"):
            download_gamma(client, tmp_path, experiments=[EXPERIMENT], include_phase_evidence=True,
                           phase_extension=True, file_lock=file_lock(source_files()))


def test_verified_cache_reuse_does_not_fetch_and_corrupt_cache_is_never_silently_repaired(tmp_path):
    item = {"name": "processed_dataset/compose/data.csv", "size": 4}
    target = tmp_path / "data.csv"
    target.write_bytes(b"data")
    digest = hashlib.sha256(b"data").hexdigest()
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("cache read reached network"))) as client:
        result = fetch_gamma_file(client, "owner/source", target, item, max_expanded=100, max_wire=100,
                                  expected_sha256=digest)
        assert result["wire_bytes"] == 0 and result["transport_verification"] == "project-frozen-cache"
        target.write_bytes(b"evil")
        with pytest.raises(ValueError, match="cached GAMMA file"):
            fetch_gamma_file(client, "owner/source", target, item, max_expanded=100, max_wire=100,
                             expected_sha256=digest)
