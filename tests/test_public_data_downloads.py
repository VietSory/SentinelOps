import hashlib
from pathlib import Path

import httpx
import pytest

from sentinelops.public_data.downloads import fetch_file,git_blob_sha,safe_relative,verify_bytes


def test_git_blob_checksum_matches_the_git_object_format():
    assert git_blob_sha(b"hello\n")=="ce013625030ba8dba906f756967f9e9ca394464a"


@pytest.mark.parametrize("name",["../outside","a/../../outside","/absolute","a\\outside","C:/outside"])
def test_dataset_paths_cannot_escape_the_selected_root(name):
    with pytest.raises(ValueError): safe_relative(name)


def test_download_checks_both_size_and_publisher_sha_before_publishing(tmp_path):
    expected={"size":6,"sha256":hashlib.sha256(b"hello\n").hexdigest()}
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b"hello\n"))) as client:
        result=fetch_file(client,"https://publisher.test/data",tmp_path/"data.txt",expected,max_bytes=10)
        assert result["sha256"]==expected["sha256"] and (tmp_path/"data.txt").read_bytes()==b"hello\n"
    (tmp_path/"data.txt").write_bytes(b"broken")
    with httpx.Client(transport=httpx.MockTransport(lambda request:pytest.fail("corrupt local data must not be silently replaced"))) as client:
        with pytest.raises(ValueError,match="SHA256"):
            fetch_file(client,"https://publisher.test/data",tmp_path/"data.txt",expected,max_bytes=10)
    assert (tmp_path/"data.txt").read_bytes()==b"broken"


def test_unexpected_stream_bytes_never_create_a_validated_file(tmp_path):
    expected={"size":5,"git_blob":git_blob_sha(b"hello")}
    for content in (b"wrong",b"too many bytes"):
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=content))) as client:
            with pytest.raises(ValueError):
                fetch_file(client,"https://publisher.test/data",tmp_path/"data.txt",expected,max_bytes=10)
        assert not (tmp_path/"data.txt").exists()


def test_budget_is_enforced_before_network_or_local_publication(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request:pytest.fail("budgeted download reached the network"))) as client:
        with pytest.raises(ValueError,match="budget"):
            fetch_file(client,"https://publisher.test/data",tmp_path/"data.txt",{"size":100,"git_blob":"0"*40},max_bytes=10)


def test_manifest_is_verified_before_table_data_are_admitted(tmp_path):
    from sentinelops.public_data.downloads import _save_manifest
    from sentinelops.public_data.audit import load_verified_manifest
    path=tmp_path/"smd"/"data.txt"
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b"hello\n"))) as client:
        record=fetch_file(client,"https://publisher.test/data",path,{"size":6,"git_blob":git_blob_sha(b"hello\n")},max_bytes=10)
    manifest=_save_manifest(tmp_path,{"manifest_version":"public-download/v1","files":[record],"activation_allowed":False})
    selected=Path(manifest["manifest_path"])
    assert load_verified_manifest(selected)[1]
    path.write_bytes(b"broken")
    with pytest.raises(ValueError): load_verified_manifest(selected)
    selected.write_bytes(b"changed")
    with pytest.raises(ValueError,match="manifest checksum"):
        load_verified_manifest(selected)
