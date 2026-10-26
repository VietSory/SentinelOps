import hashlib
from io import BytesIO
import json
from pathlib import Path
from zipfile import BadZipFile, ZIP_STORED, ZipFile

import httpx
import pytest

from sentinelops.public_data.gamma_archive import RangeReader, freeze_archive_catalog
from test_gamma_downloads import publisher, source_files


def zip_blob(files=None):
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
        for name, value in (source_files() if files is None else files).items():
            archive.writestr(name, value)
    return stream.getvalue()


def range_transport(blob, *, failure=None):
    requests = []

    def respond(request):
        if "/view/" in request.url.path:
            return httpx.Response(200, json=publisher())
        assert request.headers["accept-encoding"] == "identity"
        value = request.headers["range"]
        requests.append(value)
        if value == "bytes=-65536":
            begin, end = max(0, len(blob) - 65536), len(blob) - 1
        else:
            begin, end = map(int, value.removeprefix("bytes=").split("-"))
        selected = blob[begin:end + 1]
        headers = {"content-range": f"bytes {begin}-{end}/{len(blob)}", "etag": '"stable"'}
        if failure == "ignored":
            return httpx.Response(200, stream=httpx.ByteStream(blob))
        if failure == "wrong-range":
            headers["content-range"] = f"bytes {begin + 1}-{end}/{len(blob)}"
        elif failure == "truncated":
            selected = selected[:-1]
        elif failure == "overflow":
            selected += b"extra"
        elif failure == "encoded":
            headers["content-encoding"] = "deflate"
        elif failure == "changed" and len(requests) > 1:
            headers["etag"] = '"different"'
        return httpx.Response(206, stream=httpx.ByteStream(selected), headers=headers)
    return httpx.MockTransport(respond), requests


def test_full_catalogue_does_not_read_large_member_payload_and_replays_with_a_frozen_lock(tmp_path):
    files = source_files()
    files["raw_dataset/other/big.data"] = b"x" * 2_000_000
    blob = zip_blob(files)
    transport, requests = range_transport(blob)
    with httpx.Client(transport=transport) as client:
        first = freeze_archive_catalog(client, tmp_path)
        second = freeze_archive_catalog(client, tmp_path, expected_lock=first["lock"])
        assert first["lock"] == second["lock"]
        changed = dict(first["lock"], archive_bytes=1)
        with pytest.raises(ValueError, match="frozen lock"):
            freeze_archive_catalog(client, tmp_path, expected_lock=changed)
    assert first["response_body_bytes"] == 65536 and first["range_requests"] == 1
    assert len(requests) == 3 and first["processed_experiments"] == 1
    path = Path(first["catalog_path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == first["lock"]["catalog_sha256"]
    catalog = json.loads(path.read_text())
    assert len(catalog["files"]) == len(files) and first["activation_allowed"] is False


@pytest.mark.parametrize("failure", ["ignored", "wrong-range", "truncated", "overflow", "encoded"])
def test_range_protocol_errors_refuse_full_or_ambiguous_content(failure):
    transport, _ = range_transport(zip_blob(), failure=failure)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError):
            RangeReader(client, "https://publisher.test/archive")


def test_range_identity_change_and_body_budget_failure_do_not_produce_mixed_archive_evidence():
    blob = zip_blob({"data": b"x" * 100_000})
    transport, _ = range_transport(blob, failure="changed")
    with httpx.Client(transport=transport) as client:
        reader = RangeReader(client, "https://publisher.test/archive")
        with pytest.raises(ValueError, match="identity changed"):
            reader.read(4)
    transport, _ = range_transport(blob)
    with httpx.Client(transport=transport) as client:
        reader = RangeReader(client, "https://publisher.test/archive", max_bytes=65536, max_request_bytes=65536)
        with pytest.raises(ValueError, match="budget"):
            reader.read(4)


def test_selected_member_reads_remain_seekable_and_zip_crc_detects_corruption():
    blob = zip_blob({"data": b"a" * 100_000})
    transport, _ = range_transport(blob)
    with httpx.Client(transport=transport) as client:
        with RangeReader(client, "https://publisher.test/archive") as reader:
            with ZipFile(reader) as archive:
                assert archive.read("data") == b"a" * 100_000
            reader.seek(0, 2)
            assert reader.read(1) == b""
    corrupted = blob[:5000] + b"b" + blob[5001:]
    transport, _ = range_transport(corrupted)
    with httpx.Client(transport=transport) as client:
        with RangeReader(client, "https://publisher.test/archive") as reader:
            with ZipFile(reader) as archive:
                with pytest.raises(BadZipFile, match="CRC"):
                    archive.read("data")


@pytest.mark.parametrize("name", ["../outside", "a//normalized", "a/./normalized"])
def test_unsafe_or_noncanonical_archive_paths_are_rejected_before_inventory_publication(tmp_path, name):
    transport, _ = range_transport(zip_blob({name: b"data"}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError):
            freeze_archive_catalog(client, tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_actual_zip64_entry_count_is_not_truncated_to_classic_zip_limit(tmp_path):
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
        for index in range(65536):
            archive.writestr(f"index/{index}", b"")
    transport, _ = range_transport(stream.getvalue())
    with httpx.Client(transport=transport) as client:
        report = freeze_archive_catalog(client, tmp_path)
    assert report["lock"]["entries"] == 65536 and report["range_requests"] > 1


def test_file_and_directory_cannot_alias_the_same_canonical_output_path(tmp_path):
    transport, _ = range_transport(zip_blob({"alias/": b"", "alias": b"data"}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError, match="canonical and unique"):
            freeze_archive_catalog(client, tmp_path)
