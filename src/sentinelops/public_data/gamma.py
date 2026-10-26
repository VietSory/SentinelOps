"""Bounded GAMMA v1 acquisition, with honest locally frozen integrity metadata."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import Lock
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

from sentinelops.public_data.downloads import _save_manifest, safe_relative
from sentinelops.public_data.policy import load_policy, policy_hash, require_training_source


DEFAULT_EXPERIMENTS = ["cpu_aug12_25min_200_0"]
REQUEST_TYPES = ("compose", "home", "user")
COUNTERS = ("container_cpu_usage_seconds_total", "container_network_receive_bytes_total", "container_network_transmit_bytes_total")
METRICS = (*COUNTERS, "container_memory_usage_bytes")
META_FILES = {f"meta-data/meta-data/{name}" for name in
              ("placement.csv", "compose_ms_rpc_map.csv", "home_ms_rpc_map.csv", "user_ms_rpc_map.csv")}
API = "https://www.kaggle.com/api/v1/datasets"


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def publish_bytes(path: Path, data: bytes) -> str:
    """Publish without overwriting existing evidence, even under a concurrent writer."""
    digest = hashlib.sha256(data).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("immutable GAMMA file already has different contents")
        return digest
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("immutable GAMMA file already has different contents")
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def _json(client, url: str, *, params=None, budget: int = 2_000_000):
    parts = []
    length = 0
    with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            length += len(chunk)
            if length > budget:
                raise ValueError("GAMMA metadata exceeds its size budget")
            parts.append(chunk)
    value = json.loads(b"".join(parts))
    if not isinstance(value, dict):
        raise ValueError("invalid GAMMA metadata response")
    return value


def publisher_metadata(client, policy: dict) -> dict:
    source = policy["sources"]["gamma"]
    data = _json(client, f"{API}/view/{source['publisher']}")
    if data.get("ref") != source["publisher"] or data.get("currentVersionNumber") != source["version"]:
        raise ValueError("GAMMA publisher/version differs from frozen v1; no silent substitution")
    if data.get("licenseName") != "Apache 2.0":
        raise ValueError("GAMMA declared license differs from frozen policy")
    versions = [item for item in data.get("versions", []) if item.get("versionNumber") == source["version"]]
    if len(versions) != 1 or versions[0].get("status") != "Ready":
        raise ValueError("frozen GAMMA version is not published Ready")
    # Keep source identity and declarations; no signed redirect URLs or pagination tokens.
    return {key: data.get(key) for key in
            ("ref", "title", "subtitle", "creatorName", "currentVersionNumber", "licenseName", "totalBytes", "versions")}


def experiment_id(name: str) -> str | None:
    parts = name.split("/")
    if len(parts) == 4 and parts[0] == "processed_dataset" and parts[1] in REQUEST_TYPES:
        match = re.fullmatch(r"(.+)_graph_[1-9][0-9]*\.csv", parts[-1])
        return match.group(1) if match else None
    return None


def selected_file(name: str, selected: set[str]) -> bool:
    if name in META_FILES or experiment_id(name) in selected:
        return True
    parts = name.split("/")
    if len(parts) < 3 or parts[0] != "raw_dataset" or parts[1] not in selected:
        return False
    return (len(parts) == 3 and parts[2] == "args.txt") or (
        len(parts) == 4 and parts[2] == "prom_metrics" and any(parts[3].endswith("_" + key) for key in METRICS)) or (
        len(parts) == 4 and parts[2] == "processed_traces"
        and re.fullmatch(r"(compose|home|user)_graph_paths_[1-9][0-9]*", parts[3]) is not None)


def inventory_gamma(client, policy: dict, experiments: list[str], *, max_pages: int = 60) -> dict:
    require_training_source("gamma", policy)
    selected = sorted(set(experiments))
    if not selected or len(selected) != len(experiments) or any(not re.fullmatch(r"[a-z0-9_]+", name) for name in selected):
        raise ValueError("GAMMA experiments must be unique canonical identifiers")
    metadata = publisher_metadata(client, policy)
    publisher = policy["sources"]["gamma"]["publisher"]
    upper = f"raw_dataset/{selected[-1]}/"
    token = None
    tokens = set()
    previous = ""
    catalog = []
    retained = []
    completed = False
    pages = 0
    for _ in range(max_pages):
        params = {"pageSize": 200, "datasetVersionNumber": 1}
        if token:
            params["pageToken"] = token
        page = _json(client, f"{API}/list/{publisher}", params=params)
        pages += 1
        if page.get("errorMessage"):
            raise ValueError("GAMMA inventory returned an error")
        records = page.get("datasetFiles")
        if not isinstance(records, list) or not records:
            raise ValueError("GAMMA inventory unexpectedly empty")
        for record in records:
            name, size = record.get("name"), record.get("totalBytes")
            if not isinstance(name, str) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError("invalid GAMMA file metadata")
            safe_relative(name)
            if name <= previous:
                raise ValueError("GAMMA inventory must be strictly ordered and unique")
            previous = name
            if name > upper and not name.startswith(upper):
                completed = True
                break
            item = {"name": name, "size": size}
            if experiment_id(name) is not None:
                catalog.append(item)
            if selected_file(name, set(selected)):
                retained.append(item)
        if completed:
            break
        token = page.get("nextPageToken")
        if not token:
            completed = True
            break
        if not isinstance(token, str) or token in tokens:
            raise ValueError("GAMMA inventory pagination repeated a token")
        tokens.add(token)
        print(f"GAMMA INVENTORY pages={pages} selected_files={len(retained)}", flush=True)
    if not completed:
        raise ValueError("GAMMA inventory scope incomplete within metadata budget")
    names = {item["name"] for item in retained}
    required = META_FILES | {f"raw_dataset/{identifier}/args.txt" for identifier in selected}
    if not required <= names:
        raise ValueError("selected GAMMA experiment metadata is missing")
    for identifier in selected:
        for kind in REQUEST_TYPES:
            if not any(experiment_id(name) == identifier and name.split("/")[1] == kind for name in names):
                raise ValueError("selected GAMMA experiment has incomplete request-type coverage")
    if publisher_metadata(client, policy) != metadata:
        raise ValueError("GAMMA publisher metadata changed during inventory")
    return {"inventory_version": "gamma-inventory/v1", "metadata": metadata,
            "source_version": 1, "version_id": 7638732, "training_data_policy_hash": policy_hash(policy),
            "selection": selected, "processed_catalog": catalog, "selected_files": retained,
            "scope_complete": True, "metadata_pages": pages,
            "omitted_raw_modalities": ["application-logs", "kubernetes-logs", "full-traces", "other-resource-metrics"]}


def fetch_gamma_file(client, publisher: str, target: Path, item: dict, *, max_expanded: int, max_wire: int,
                     reserve_wire=None, expected_sha256: str | None = None) -> dict:
    name, size = item["name"], item["size"]
    if not 0 <= size <= max_expanded:
        raise ValueError("GAMMA file exceeds expanded size budget")
    safe_relative(name)
    graph_metadata = re.fullmatch(r"raw_dataset/[a-z0-9_]+/processed_traces/(compose|home|user)_graph_paths_[1-9][0-9]*", name)
    if name not in META_FILES and not (name.endswith((".csv", "/args.txt")) or "/prom_metrics/" in name or graph_metadata):
        raise ValueError("non-data GAMMA file refused")
    url = f"{API}/download/{publisher}/{quote(name, safe='')}?datasetVersionNumber=1"
    chunks = []
    wire_size = 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if reserve_wire is not None:
                reserve_wire(len(chunk))
            wire_size += len(chunk)
            if wire_size > max_wire:
                raise ValueError("GAMMA download exceeds wire size budget")
            chunks.append(chunk)
    wire = b"".join(chunks)
    transport = "plain"
    if wire.startswith(b"PK\x03\x04"):
        try:
            with ZipFile(BytesIO(wire)) as archive:
                members = archive.infolist()
                if len(members) != 1:
                    raise ValueError("GAMMA single-file archive has unexpected members")
                member = members[0]
                safe_relative(member.filename)
                if (member.filename not in (name, Path(name).name) or member.is_dir()
                    or member.flag_bits & 1 or stat.S_ISLNK(member.external_attr >> 16)
                    or member.file_size != size or member.file_size > max_expanded):
                    raise ValueError("GAMMA archive identity or expanded size differs")
                data = archive.read(member)  # ZipFile verifies the CRC while reading.
        except BadZipFile as exc:
            raise ValueError("GAMMA archive integrity failed") from exc
        transport = "single-file-zip-crc-verified"
    else:
        data = wire
    if len(data) != size:
        raise ValueError("GAMMA expanded file differs from publisher size")
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("GAMMA bytes differ from the project-frozen file hash")
    digest = publish_bytes(target, data)
    print(f"GAMMA FROZEN {name} bytes={size}", flush=True)
    return {"path": str(target), "source_path": name, "source_url": url, "size": size, "sha256": digest,
            "publisher_checksum": None, "checksum_origin": "locally-frozen-sha256",
            "project_expected_sha256": expected_sha256,
            "transport_verification": transport, "wire_bytes": wire_size}


def download_gamma(client, root: Path, *, experiments: list[str] | None = None, policy: dict | None = None,
                   max_bytes: int = 256_000_000, max_file_bytes: int = 128_000_000,
                   max_wire_bytes: int = 32_000_000, max_total_wire_bytes: int = 128_000_000,
                   max_pages: int = 60, workers: int = 4, file_lock: dict | None = None) -> dict:
    policy = load_policy() if policy is None else policy
    require_training_source("gamma", policy)
    if not 1 <= workers <= 4:
        raise ValueError("GAMMA downloader supports one to four bounded workers")
    inventory = inventory_gamma(client, policy, DEFAULT_EXPERIMENTS if experiments is None else experiments, max_pages=max_pages)
    items = inventory["selected_files"]
    if sum(item["size"] for item in items) > max_bytes or any(item["size"] > max_file_bytes for item in items):
        raise ValueError("GAMMA selection exceeds its expanded size budget")
    locked_files = {}
    if file_lock is not None:
        if (file_lock.get("lock_version") != "gamma-project-file-lock/v1" or file_lock.get("source_version") != 1
            or file_lock.get("training_data_policy_hash") != policy_hash(policy)
            or file_lock.get("selection") != inventory["selection"]):
            raise ValueError("GAMMA project file lock differs from source policy/selection")
        locked_files = file_lock["files"]
        if set(locked_files) != {item["name"] for item in items}:
            raise ValueError("GAMMA project file lock does not cover the exact inventory")
        for item in items:
            locked = locked_files[item["name"]]
            if locked.get("size") != item["size"] or not re.fullmatch(r"[0-9a-f]{64}", locked.get("sha256", "")):
                raise ValueError("GAMMA project file lock size/hash is invalid")
    destination = root / "gamma" / "v1"
    inventory_blob = canonical_bytes(inventory)
    inventory_sha = hashlib.sha256(inventory_blob).hexdigest()
    inventory_path = root / "inventories" / f"{inventory_sha}.json"
    publish_bytes(inventory_path, inventory_blob)
    publisher = policy["sources"]["gamma"]["publisher"]
    wire_lock = Lock()
    consumed_wire = 0

    def reserve_wire(count):
        nonlocal consumed_wire
        with wire_lock:
            consumed_wire += count
            if consumed_wire > max_total_wire_bytes:
                raise ValueError("GAMMA selection exceeds total wire budget")

    def fetch(item):
        return fetch_gamma_file(client, publisher, destination / safe_relative(item["name"]), item,
                                max_expanded=max_file_bytes, max_wire=min(max_wire_bytes, item["size"] + 65536),
                                reserve_wire=reserve_wire, expected_sha256=locked_files.get(item["name"], {}).get("sha256"))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(fetch, items))
    if publisher_metadata(client, policy) != inventory["metadata"]:
        raise ValueError("GAMMA publisher metadata changed during acquisition")
    return _save_manifest(root, {"manifest_version": "public-download/v2", "dataset": "GAMMA", "revision": "1",
        "source_version_id": 7638732, "publisher": publisher, "declared_license": "Apache-2.0",
        "selection": inventory["selection"], "inventory_path": str(inventory_path), "inventory_sha256": inventory_sha,
        "training_data_policy_hash": policy_hash(policy), "files": records,
        "project_file_lock_sha256": hashlib.sha256(canonical_bytes(file_lock)).hexdigest() if file_lock is not None else None,
        "created_at": datetime.now(timezone.utc).isoformat(), "serving_compatible": False, "activation_allowed": False,
        "wire_bytes": consumed_wire, "expanded_bytes": sum(item["size"] for item in items),
        "limitations": ["publisher supplies file sizes but no independent file SHA256",
                        "scoped capability audit omits raw logs, full traces and other metrics",
                        "training and live feature/label readiness are not certified by acquisition"]})


def load_gamma_manifest(path: Path, *, policy: dict | None = None) -> tuple[dict, dict[str, Path], dict]:
    policy = load_policy() if policy is None else policy
    blob = path.read_bytes()
    if path.stem != hashlib.sha256(blob).hexdigest():
        raise ValueError("GAMMA manifest checksum mismatch")
    manifest = json.loads(blob)
    require_training_source(manifest.get("dataset", ""), policy)
    if (manifest.get("manifest_version") != "public-download/v2" or manifest.get("revision") != "1"
        or manifest.get("publisher") != policy["sources"]["gamma"]["publisher"]
        or manifest.get("training_data_policy_hash") != policy_hash(policy)
        or manifest.get("source_version_id") != 7638732 or manifest.get("activation_allowed") is not False):
        raise ValueError("GAMMA manifest differs from frozen source contract")
    root = path.parent.parent.resolve()
    inventory_path = Path(manifest["inventory_path"]).resolve()
    if not inventory_path.is_relative_to(root):
        raise ValueError("GAMMA inventory escapes evidence root")
    inventory_blob = inventory_path.read_bytes()
    if hashlib.sha256(inventory_blob).hexdigest() != manifest["inventory_sha256"]:
        raise ValueError("GAMMA inventory checksum mismatch")
    inventory = json.loads(inventory_blob)
    if (inventory.get("training_data_policy_hash") != policy_hash(policy) or inventory.get("selection") != manifest["selection"]
        or inventory.get("scope_complete") is not True or inventory.get("source_version") != 1
        or inventory.get("version_id") != 7638732):
        raise ValueError("GAMMA inventory/selection differs from manifest")
    expected = {item["name"]: item["size"] for item in inventory["selected_files"]}
    if len(expected) != len(inventory["selected_files"]) or any(not selected_file(name, set(manifest["selection"])) for name in expected):
        raise ValueError("GAMMA inventory has duplicate or unselected file identities")
    files = {}
    for record in manifest["files"]:
        target = Path(record["path"]).resolve()
        source_path = record["source_path"]
        relative = safe_relative(source_path)
        if target != root / "gamma" / "v1" / relative or not target.is_relative_to(root):
            raise ValueError("GAMMA manifest file escapes its source root")
        if source_path in files or record["size"] != expected.get(source_path):
            raise ValueError("GAMMA file/size differs from inventory")
        data = target.read_bytes()
        if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("GAMMA file integrity mismatch")
        if record.get("project_expected_sha256") is not None and record["project_expected_sha256"] != record["sha256"]:
            raise ValueError("GAMMA project file lock hash differs from acquired bytes")
        if record.get("publisher_checksum") is not None or record.get("checksum_origin") != "locally-frozen-sha256":
            raise ValueError("GAMMA checksum provenance is misrepresented")
        files[source_path] = target
    if set(files) != set(expected):
        raise ValueError("GAMMA manifest does not cover the entire selected inventory")
    return manifest, files, inventory
