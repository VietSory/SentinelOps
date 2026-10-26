"""Bounded seekable HTTP ranges for the pinned GAMMA ZIP64 catalogue."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import io
from pathlib import Path
import re
import subprocess
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from sentinelops.public_data.downloads import safe_relative
from sentinelops.public_data.gamma import API, REQUEST_TYPES, canonical_bytes, experiment_id, publish_bytes, publisher_metadata
from sentinelops.public_data.policy import load_policy, policy_hash, require_training_source


class RangeReader(io.RawIOBase):
    """Read only exact 206 ranges; reject ignored/changed/truncated responses."""

    def __init__(self, client, url: str, *, max_bytes=32_000_000, max_request_bytes=24_000_000):
        super().__init__()
        if max_bytes < 65536 or not 65536 <= max_request_bytes <= max_bytes:
            raise ValueError("invalid archive range byte budgets")
        self.client, self.url = client, url
        self.max_bytes, self.max_request_bytes = max_bytes, max_request_bytes
        self.body_bytes = self.requests = self.position = 0
        self.size = self.etag = None
        self.cache = []
        start, blob = self.fetch("bytes=-65536", suffix=True)
        self.cache.append((start, blob))

    def fetch(self, value: str, *, suffix=False) -> tuple[int, bytes]:
        if self.closed:
            raise ValueError("closed archive reader")
        if not suffix:
            match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", value)
            if not match:
                raise ValueError("invalid archive range request")
            start, end = map(int, match.groups())
            length = end - start + 1
            if not 0 <= start <= end < self.size or length > self.max_request_bytes or self.body_bytes + length > self.max_bytes:
                raise ValueError("archive request exceeds its range/body budget")
        with self.client.stream("GET", self.url, headers={"Range": value, "Accept-Encoding": "identity"}) as response:
            self.requests += 1
            if response.status_code != 206:
                raise ValueError("archive server did not honor the exact byte range; full download refused")
            match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", response.headers.get("content-range", ""))
            if not match or response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("invalid archive Content-Range or content encoding")
            start, end, total = map(int, match.groups())
            if not 0 <= start <= end < total:
                raise ValueError("invalid archive byte range bounds")
            expected = f"bytes={start}-{end}"
            if (suffix and (start != max(0, total - 65536) or end != total - 1)) or (not suffix and value != expected):
                raise ValueError("archive response range differs from requested bytes")
            etag = response.headers.get("etag")
            if self.size is None:
                self.size, self.etag = total, etag
            elif self.size != total or self.etag != etag:
                raise ValueError("archive identity changed between range responses")
            length = end - start + 1
            if length > self.max_request_bytes or self.body_bytes + length > self.max_bytes:
                raise ValueError("archive range exceeds its request/total body budget")
            parts, received = [], 0
            for chunk in response.iter_bytes():
                received += len(chunk)
                self.body_bytes += len(chunk)
                if received > length or self.body_bytes > self.max_bytes:
                    raise ValueError("archive stream exceeds declared range/body budget")
                parts.append(chunk)
            if received != length:
                raise ValueError("truncated archive range response")
            return start, b"".join(parts)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if self.closed:
            raise ValueError("closed archive reader")
        if whence not in (io.SEEK_SET, io.SEEK_CUR, io.SEEK_END):
            raise ValueError("invalid archive seek origin")
        position = offset + (0 if whence == io.SEEK_SET else self.position if whence == io.SEEK_CUR else self.size)
        if not 0 <= position <= self.size:
            raise ValueError("archive seek escapes representation")
        self.position = position
        return position

    def read(self, size=-1):
        if self.closed:
            raise ValueError("closed archive reader")
        length = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if not length:
            return b""
        start, end = self.position, self.position + length
        for begin, blob in self.cache:
            if begin <= start and end <= begin + len(blob):
                self.position = end
                return blob[start - begin:end - begin]
        begin, blob = self.fetch(f"bytes={start}-{end - 1}")
        self.cache.append((begin, blob))
        self.position = end
        return blob


def archive_catalog(client, *, policy=None, max_bytes=32_000_000, max_entries=250_000) -> tuple[dict, RangeReader, ZipFile]:
    policy = load_policy() if policy is None else policy
    require_training_source("gamma", policy)
    metadata = publisher_metadata(client, policy)
    url = f"{API}/download/{policy['sources']['gamma']['publisher']}?datasetVersionNumber=1"
    reader = RangeReader(client, url, max_bytes=max_bytes)
    try:
        archive = ZipFile(reader)
        entries = archive.infolist()
        if not entries or len(entries) > max_entries:
            raise ValueError("archive entry count exceeds catalogue budget")
        seen, offsets = set(), set()
        files = []
        cases = defaultdict(set)
        for item in entries:
            name = item.filename
            canonical_name = safe_relative(name).as_posix()
            if canonical_name != name.rstrip("/") or canonical_name in seen:
                raise ValueError("archive paths must be canonical and unique")
            if item.header_offset in offsets or not 0 <= item.header_offset < archive.start_dir:
                raise ValueError("archive member offsets must be unique and precede its central directory")
            seen.add(canonical_name)
            offsets.add(item.header_offset)
            if item.flag_bits & 1 or item.compress_type not in (ZIP_DEFLATED, ZIP_STORED):
                raise ValueError("encrypted/unsupported archive member")
            if item.file_size < 0 or item.compress_size < 0 or item.header_offset + item.compress_size >= archive.start_dir:
                raise ValueError("archive member size/offset exceeds data region")
            files.append({"name": name, "size": item.file_size, "compressed_size": item.compress_size,
                          "header_offset": item.header_offset, "crc32": f"{item.CRC:08x}",
                          "compression": item.compress_type, "directory": item.is_dir()})
            experiment = experiment_id(name)
            if experiment:
                cases[experiment].add(name.split("/")[1])
        catalog = {"catalog_version": "gamma-archive-catalog/v1", "source_version": 1, "version_id": 7638732,
                   "metadata": metadata, "training_data_policy_hash": policy_hash(policy),
                   "source_url": url, "archive_bytes": reader.size, "central_directory_offset": archive.start_dir,
                   "files": sorted(files, key=lambda item: item["name"]),
                   "processed_experiments": {key: sorted(value) for key, value in sorted(cases.items())}}
        if publisher_metadata(client, policy) != metadata:
            raise ValueError("publisher metadata changed during archive inventory")
        return catalog, reader, archive
    except BaseException:
        reader.close()
        raise


def freeze_archive_catalog(client, root: Path, *, expected_lock: dict | None = None, max_bytes=32_000_000,
                           project: Path | None = None) -> dict:
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit the catalogue implementation before freezing source inventory")
    catalog, reader, archive = archive_catalog(client, max_bytes=max_bytes)
    try:
        blob = canonical_bytes(catalog)
        digest = hashlib.sha256(blob).hexdigest()
        lock = {"lock_version": "gamma-archive-catalog-lock/v1", "source_version": 1, "version_id": 7638732,
                "training_data_policy_hash": catalog["training_data_policy_hash"], "catalog_sha256": digest,
                "archive_bytes": reader.size, "entries": len(catalog["files"])}
        if expected_lock is not None and lock != expected_lock:
            raise ValueError("archive catalogue differs from the project-frozen lock")
        destination = root / "inventories" / (digest + ".json")
        publish_bytes(destination, blob)
        modalities = Counter()
        for item in catalog["files"]:
            parts = item["name"].split("/")
            key = parts[0] if len(parts) < 3 or parts[0] != "raw_dataset" else "/".join((parts[0], parts[2]))
            modalities[key] += 1
        return {"report_version": "gamma-archive-catalog/v1", "source_commit": commit,
                "catalog_path": str(destination), "lock": lock,
                "range_requests": reader.requests, "response_body_bytes": reader.body_bytes,
                "processed_experiments": len(catalog["processed_experiments"]),
                "complete_request_type_experiments": sum(set(kinds) == set(REQUEST_TYPES) for kinds in catalog["processed_experiments"].values()),
                "modality_entry_counts": dict(sorted(modalities.items())),
                "training_ready": False, "activation_allowed": False,
                "limitations": ["complete archive member inventory only; no corpus-wide feature/label admission",
                                "catalogue identity and CRC32 metadata are not publisher-supplied file SHA256",
                                "byte-range body counts exclude HTTP headers and protocol overhead"]}
    finally:
        archive.close()
        reader.close()
