"""Predeclared all-args GAMMA raw cohort; immutable native-byte ZIP shards."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import stat
import subprocess
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from sentinelops.public_data.downloads import _save_manifest
from sentinelops.public_data.gamma import META_FILES, METRICS, canonical_bytes, phase_file, publish_bytes, publisher_metadata
from sentinelops.public_data.gamma_archive import archive_catalog
from sentinelops.public_data.policy import load_policy, policy_hash


def catalog_identity(catalog: dict) -> dict:
    return {"lock_version": "gamma-archive-catalog-lock/v1", "source_version": 1, "version_id": 7638732,
            "training_data_policy_hash": catalog["training_data_policy_hash"],
            "catalog_sha256": hashlib.sha256(canonical_bytes(catalog)).hexdigest(),
            "archive_bytes": catalog["archive_bytes"], "entries": len(catalog["files"])}


def cohort_plan(catalog: dict, scope: dict) -> list[dict]:
    expected = {"scope_version", "catalog_sha256", "training_data_policy_hash", "experiment_rule", "experiments",
                "resource_metrics", "include_pod_descriptions", "include_phase_logs", "global_metadata",
                "selection_uses_model_scores", "training_ready", "activation_allowed"}
    if (set(scope) != expected or scope["scope_version"] != "gamma-raw-resource-cohort/v1"
        or scope["experiment_rule"] != "all-raw-directories-with-args"
        or scope["resource_metrics"] != list(METRICS) or scope["global_metadata"] != sorted(META_FILES)
        or scope["include_pod_descriptions"] is not True or scope["include_phase_logs"] is not True
        or any(scope[key] is not False for key in ("selection_uses_model_scores", "training_ready", "activation_allowed"))):
        raise ValueError("cohort scope differs from the predeclared raw-resource contract")
    if (scope["catalog_sha256"] != hashlib.sha256(canonical_bytes(catalog)).hexdigest()
        or scope["training_data_policy_hash"] != catalog["training_data_policy_hash"]):
        raise ValueError("cohort scope differs from its frozen catalogue/policy")
    experiments = sorted({item["name"].split("/")[1] for item in catalog["files"]
                          if re.fullmatch(r"raw_dataset/[a-z0-9_]+/args\.txt", item["name"])})
    if not experiments or scope["experiments"] != experiments:
        raise ValueError("cohort must include all raw directories with args, without outcome-based selection")
    selected = []
    for item in catalog["files"]:
        name = item["name"]
        parts = name.split("/")
        role, experiment = None, None
        if name in META_FILES:
            role = "global-metadata"
        elif len(parts) >= 3 and parts[0] == "raw_dataset" and parts[1] in experiments:
            experiment = parts[1]
            if len(parts) == 3 and parts[2] == "args.txt":
                role = "args"
            elif phase_file(name):
                role = "phase"
            elif len(parts) == 4 and parts[2] == "pods_describe" and parts[3].endswith(".log"):
                role = "placement"
            elif len(parts) == 4 and parts[2] == "prom_metrics" and any(parts[3].endswith("_" + key) for key in METRICS):
                role = "resource"
        if role:
            if item["directory"]:
                raise ValueError("selected data member is a directory")
            selected.append({**item, "role": role, "experiment": experiment})
    if {item["name"] for item in selected if item["role"] == "global-metadata"} != META_FILES:
        raise ValueError("cohort lacks the frozen global metadata files")
    return sorted(selected, key=lambda item: item["name"])


def range_plan(catalog: dict, selected: list[dict], *, gap=4096, block_bytes=1_048_576) -> list[tuple[int, int]]:
    if not 0 <= gap <= 4096 or not 65536 <= block_bytes <= 1_048_576:
        raise ValueError("invalid bounded coalescing profile")
    physical = sorted(catalog["files"], key=lambda item: item["header_offset"])
    following = {item["name"]: physical[index + 1]["header_offset"] if index + 1 < len(physical)
                 else catalog["central_directory_offset"] for index, item in enumerate(physical)}
    blocks = []
    for item in sorted(selected, key=lambda item: item["header_offset"]):
        begin, end = item["header_offset"], following[item["name"]]
        if not 30 + item["compressed_size"] <= end - begin <= item["compressed_size"] + 131100:
            raise ValueError("selected ZIP member span exceeds its local-header/data bound")
        if blocks and begin - blocks[-1][1] <= gap and end - blocks[-1][0] <= block_bytes:
            blocks[-1] = blocks[-1][0], end
        else:
            blocks.append((begin, end))
    return blocks


def source_tree(records: list[dict]) -> str:
    fields = ("source_path", "size", "sha256", "role")
    return hashlib.sha256(canonical_bytes([{key: item[key] for key in fields}
                                         for item in sorted(records, key=lambda item: item["source_path"])] )).hexdigest()


def tree_lock(scope_sha: str, catalog_sha: str, records: list[dict]) -> dict:
    return {"lock_version": "gamma-cohort-file-tree/v1", "scope_sha256": scope_sha,
            "catalog_sha256": catalog_sha, "files": len(records),
            "expanded_bytes": sum(item["size"] for item in records), "source_file_tree_sha256": source_tree(records)}


def bundle_bytes(files: dict[str, bytes]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    return stream.getvalue()


def download_cohort(client, root: Path, scope: dict, catalog_lock: dict, *, expected_tree: dict | None = None,
                    workers=4, max_body_bytes=128_000_000, max_expanded_bytes=128_000_000,
                    max_member_bytes=2_000_000, project: Path | None = None) -> dict:
    if not 1 <= workers <= 4:
        raise ValueError("cohort supports one to four bounded range workers")
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit the cohort scope/implementation before freezing source bytes")
    policy = load_policy()
    if scope.get("training_data_policy_hash") != policy_hash(policy) or scope.get("catalog_sha256") != catalog_lock.get("catalog_sha256"):
        raise ValueError("cohort source policy/catalogue lock mismatch")
    catalog, reader, archive = archive_catalog(client, max_bytes=max_body_bytes)
    try:
        if catalog_identity(catalog) != catalog_lock:
            raise ValueError("cohort archive differs from the project-frozen catalogue lock")
        selected = cohort_plan(catalog, scope)
        if any(item["size"] > max_member_bytes for item in selected) or sum(item["size"] for item in selected) > max_expanded_bytes:
            raise ValueError("selected cohort exceeds per-member/total expanded budget")
        blocks = range_plan(catalog, selected)
        if reader.reserved_bytes + sum(end - begin for begin, end in blocks) > max_body_bytes:
            raise ValueError("planned cohort ranges exceed the total response-body budget")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Bound in-flight work, so a failure does not leave all catalogue
            # ranges queued and downloading before executor shutdown.
            for index in range(0, len(blocks), workers):
                jobs = [pool.submit(reader.fetch, f"bytes={begin}-{end - 1}") for begin, end in blocks[index:index + workers]]
                for job in jobs:
                    reader.cache.append(job.result())
                if index % 64 == 0:
                    print(f"GAMMA COHORT ranges={min(index + workers, len(blocks))}/{len(blocks)} body_bytes={reader.body_bytes}", flush=True)
        scope_sha = hashlib.sha256(canonical_bytes(scope)).hexdigest()
        catalog_sha = catalog_lock["catalog_sha256"]
        publish_bytes(root / "inventories" / (catalog_sha + ".json"), canonical_bytes(catalog))
        grouped = defaultdict(list)
        for item in selected:
            grouped[item["experiment"] or "global-metadata"].append(item)
        records, bundles = [], []
        for group, members in sorted(grouped.items()):
            content, member_records = {}, []
            for item in members:
                info = archive.getinfo(item["name"])
                if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG):
                    raise ValueError("cohort selected a nonregular ZIP member")
                data = archive.read(info)  # Local header, filename and CRC are checked by ZipFile.
                if len(data) != item["size"]:
                    raise ValueError("cohort member length differs from frozen catalogue")
                content[item["name"]] = data
                member_records.append({"source_path": item["name"], "role": item["role"], "size": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest(), "archive_crc32": item["crc32"]})
            blob = bundle_bytes(content)
            digest = hashlib.sha256(blob).hexdigest()
            path = root / "gamma" / "cohort-v1" / scope_sha / "bundles" / (digest + ".zip")
            publish_bytes(path, blob)
            bundles.append({"experiment": None if group == "global-metadata" else group,
                            "path": str(path), "sha256": digest, "size": len(blob), "members": member_records})
            records.extend(member_records)
        frozen_tree = tree_lock(scope_sha, catalog_sha, records)
        if expected_tree is not None and frozen_tree != expected_tree:
            raise ValueError("acquired cohort bytes differ from the project-frozen source file tree")
        if publisher_metadata(client, policy) != catalog["metadata"]:
            raise ValueError("publisher metadata changed during cohort acquisition")
        case_roles = {group: dict(Counter(item["role"] for item in members)) for group, members in sorted(grouped.items())
                      if group != "global-metadata"}
        return _save_manifest(root, {"manifest_version": "gamma-cohort-download/v1", "dataset": "GAMMA", "revision": "1",
            "source_version_id": 7638732, "publisher": policy["sources"]["gamma"]["publisher"], "source_commit": commit,
            "training_data_policy_hash": policy_hash(policy), "scope": scope, "scope_sha256": scope_sha,
            "catalog_sha256": catalog_sha, "source_url": catalog["source_url"], "bundles": bundles,
            "file_tree_lock": frozen_tree, "role_counts": dict(Counter(item["role"] for item in records)),
            "case_role_counts": case_roles,
            "cases_missing_roles": {role: [group for group, roles in case_roles.items() if role not in roles]
                                    for role in ("resource", "placement", "phase")},
            "range_requests": reader.requests, "response_body_bytes": reader.body_bytes,
            "storage": "project-zip-shards-original-member-bytes/v1", "experiments": len(scope["experiments"]),
            "activation_allowed": False, "serving_compatible": False, "training_ready": False,
            "limitations": ["all source bytes acquired; semantic/label/session/split admission remains separate",
                "locally frozen SHA256 tree is not a publisher-supplied cryptographic checksum",
                "project ZIP shards preserve original member bytes, not original archive packaging",
                "response-body bytes include metadata/coalescing overhead but exclude HTTP protocol overhead"]})
    finally:
        archive.close()
        reader.close()


def load_cohort_manifest(path: Path, scope: dict, *, expected_tree: dict | None = None) -> tuple[dict, dict[str | None, dict]]:
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != path.stem:
        raise ValueError("cohort manifest checksum mismatch")
    manifest = json.loads(blob)
    policy = load_policy()
    scope_sha = hashlib.sha256(canonical_bytes(scope)).hexdigest()
    if (manifest.get("manifest_version") != "gamma-cohort-download/v1" or manifest.get("dataset") != "GAMMA"
        or manifest.get("revision") != "1" or manifest.get("source_version_id") != 7638732
        or manifest.get("publisher") != policy["sources"]["gamma"]["publisher"]
        or manifest.get("training_data_policy_hash") != policy_hash(policy)
        or manifest.get("scope") != scope or manifest.get("scope_sha256") != scope_sha
        or manifest.get("activation_allowed") is not False or manifest.get("training_ready") is not False):
        raise ValueError("cohort manifest differs from the frozen source/scope policy")
    root = path.parent.parent.resolve()
    catalog_blob = (root / "inventories" / (scope["catalog_sha256"] + ".json")).read_bytes()
    if hashlib.sha256(catalog_blob).hexdigest() != scope["catalog_sha256"] or manifest.get("catalog_sha256") != scope["catalog_sha256"]:
        raise ValueError("cohort catalogue checksum mismatch")
    selected = {item["name"]: item for item in cohort_plan(json.loads(catalog_blob), scope)}
    records, groups, seen = [], {}, set()
    for bundle in manifest["bundles"]:
        group = bundle["experiment"]
        target = Path(bundle["path"]).resolve()
        expected = root / "gamma" / "cohort-v1" / scope_sha / "bundles" / (bundle["sha256"] + ".zip")
        if group in groups or target != expected or not target.is_relative_to(root):
            raise ValueError("duplicate/escaping cohort bundle identity")
        for record in bundle["members"]:
            name = record["source_path"]
            item = selected.get(name)
            if (name in seen or item is None or group != item["experiment"] or record["role"] != item["role"]
                or record["size"] != item["size"] or record["archive_crc32"] != item["crc32"]):
                raise ValueError("cohort member differs from the exact frozen catalogue scope")
            seen.add(name)
        content = read_bundle(bundle)
        for record in bundle["members"]:
            name = record["source_path"]
            data = content[name]
            if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("cohort member integrity mismatch")
            records.append(record)
        groups[group] = bundle
    frozen_tree = tree_lock(scope_sha, scope["catalog_sha256"], records)
    if (seen != set(selected) or set(groups) != {None, *scope["experiments"]}
        or frozen_tree != manifest["file_tree_lock"] or (expected_tree is not None and frozen_tree != expected_tree)):
        raise ValueError("cohort file coverage/tree differs from frozen evidence")
    return manifest, groups


def read_bundle(bundle: dict) -> dict[str, bytes]:
    target = Path(bundle["path"])
    if (not 0 <= bundle["size"] <= 128_000_000 or target.stat().st_size != bundle["size"]
        or any(not 0 <= item["size"] <= 2_000_000 for item in bundle["members"])
        or sum(item["size"] for item in bundle["members"]) > 128_000_000):
        raise ValueError("cohort bundle size mismatch")
    blob = target.read_bytes()
    if hashlib.sha256(blob).hexdigest() != bundle["sha256"]:
        raise ValueError("cohort bundle checksum mismatch")
    with ZipFile(BytesIO(blob)) as archive:
        if archive.namelist() != [item["source_path"] for item in bundle["members"]]:
            raise ValueError("cohort ZIP members differ from manifest")
        if any(archive.getinfo(item["source_path"]).file_size != item["size"]
               or archive.getinfo(item["source_path"]).compress_type != ZIP_STORED
               or archive.getinfo(item["source_path"]).flag_bits & 1 for item in bundle["members"]):
            raise ValueError("cohort ZIP expanded member size differs from manifest")
        return {name: archive.read(name) for name in archive.namelist()}
