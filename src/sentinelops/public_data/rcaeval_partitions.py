"""Frozen metadata-only RCAEval partitions; evaluator metadata never becomes RCA evidence."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from sentinelops.public_data.downloads import verify_bytes
from sentinelops.public_data.policy import load_policy

REVISION = "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e"
INDEX_SHA256 = "c49a288920dbba2e8e724679a14636d5c7eb2b45426bba14007ef79a6c0ab1bb"
INSPECTED = ("re1ob_adservice_cpu_1", "re2ob_checkoutservice_cpu_1", "re3ob_adservice_f3_1")
DEFAULT_PARTITIONS = Path(__file__).resolve().parents[3] / "configs/rcaeval_partitions.json"
# Frozen from index metadata before any additional case telemetry is opened.
FROZEN_PARTITIONS_SHA256 = "e8267898af1ea52444adfa85fb9bc477ccf450fd9bebc1a99019998035b188ef"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def group_metadata(rows: list[dict], *, buffer_seconds: int = 600) -> list[dict]:
    """Conservative transitive closure, including across different published systems."""
    if type(buffer_seconds) is not int or buffer_seconds < 0 or not isinstance(rows, list) or not rows:
        raise ValueError("invalid grouping input/buffer")
    if any(not isinstance(row, dict) or not isinstance(row.get("case"), str) or not row["case"] for row in rows):
        raise ValueError("invalid RCAEval case identity")
    rows = sorted(rows, key=lambda row: row["case"])
    names = [row["case"] for row in rows]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError("duplicate/invalid RCAEval case identity")
    for row in rows:
        if any(not isinstance(row.get(key), str) or not row[key] for key in ("system", "root_cause_service", "fault")):
            raise ValueError("missing RCAEval grouping metadata")
        if any(type(row.get(key)) is not int for key in ("time_start", "time_end", "inject_time")):
            raise ValueError("non-integral RCAEval time metadata")
        if row["time_start"] < 0 or row["time_end"] < row["time_start"]:
            raise ValueError("invalid RCAEval capture interval")
    parents = list(range(len(rows)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(i, j):
        parents[root(i)] = root(j)

    for i, row in enumerate(rows):
        family = tuple(row[key] for key in ("system", "root_cause_service", "fault"))
        for j, other in enumerate(rows[:i]):
            same_family = family == tuple(other[key] for key in ("system", "root_cause_service", "fault"))
            close = max(row["time_start"], other["time_start"]) <= min(row["time_end"], other["time_end"]) + buffer_seconds
            if same_family or close:
                join(i, j)
    members: dict[int, list[str]] = {}
    for i, name in enumerate(names):
        members.setdefault(root(i), []).append(name)
    return sorted([{"group_id": hashlib.sha256(canonical(cases)).hexdigest(), "cases": cases}
                   for cases in members.values()], key=lambda group: group["group_id"])


def partition_metadata(rows: list[dict]) -> dict:
    groups = group_metadata(rows)
    names = {row["case"] for row in rows}
    if not set(INSPECTED) <= names:
        raise ValueError("previously inspected cases missing from RCAEval index")
    exposed = {g["group_id"] for g in groups if set(g["cases"]) & set(INSPECTED)}
    candidates = sorted([g for g in groups if g["group_id"] not in exposed],
                        key=lambda g: hashlib.sha256(canonical({"seed": 42, "group_id": g["group_id"]})).hexdigest())
    if len(candidates) < 4:
        raise ValueError("insufficient unexposed conservative groups for separate validation/holdout")
    holdout = {g["group_id"] for g in candidates[:2]}
    validation = {g["group_id"] for g in candidates[2:4]}
    assignments = []
    for group in groups:
        role = "sealed-holdout" if group["group_id"] in holdout else "validation" if group["group_id"] in validation else "development"
        assignments.append({**group, "role": role, "previously_inspected": group["group_id"] in exposed})
    return {"partition_version": "rcaeval-conservative-groups/v1", "revision": REVISION,
            "index_sha256": INDEX_SHA256, "algorithm": "capture-interval-and-fault-family-closure/v1",
            "interval_buffer_seconds": 600, "seed": 42, "heldout_groups": 2, "validation_groups": 2,
            "previously_inspected_cases": list(INSPECTED), "assignments": assignments,
            "ml_fitting_allowed": False, "holdout_opened": False, "activation_allowed": False,
            "independent_sessions_established": False}


def load_partitions(path: Path = DEFAULT_PARTITIONS) -> dict:
    policy = load_policy()["sources"]["rcaeval"]
    if policy["revision"] != REVISION or policy["ml_fitting_allowed"] is not False:
        raise ValueError("RCAEval partitions differ from frozen training source role")
    config = json.loads(path.read_text(encoding="utf-8"))
    if hashlib.sha256(canonical(config)).hexdigest() != FROZEN_PARTITIONS_SHA256:
        raise ValueError("RCAEval partition policy differs from the frozen metadata-only selection")
    return config


def require_cases(cases: list[str], partition: dict | None = None) -> dict[str, str]:
    # An explicit dictionary is checked too; callers cannot supply a custom policy to open test.
    config = load_partitions() if partition is None else partition
    if hashlib.sha256(canonical(config)).hexdigest() != FROZEN_PARTITIONS_SHA256:
        raise ValueError("RCAEval partition policy differs from the frozen metadata-only selection")
    roles = {name: group["role"] for group in config["assignments"] for name in group["cases"]}
    if not cases or any(name not in roles for name in cases):
        raise ValueError("RCAEval selection is not covered by the frozen case inventory")
    if any(roles[name] == "sealed-holdout" for name in cases):
        raise ValueError("RCAEval holdout is sealed until the separate P11 evaluation protocol")
    return {name: roles[name] for name in cases}


def read_index(manifest_path: Path) -> tuple[list[dict], dict]:
    """Read only the verified index. Do not touch telemetry files listed beside it."""
    blob = manifest_path.read_bytes()
    if manifest_path.stem != hashlib.sha256(blob).hexdigest():
        raise ValueError("RCAEval source manifest checksum mismatch")
    manifest = json.loads(blob)
    if (manifest.get("manifest_version") != "public-download/v1" or manifest.get("dataset") != "RCAEval"
        or manifest.get("revision") != REVISION or manifest.get("activation_allowed") is not False):
        raise ValueError("unsupported RCAEval source manifest")
    public_root = manifest_path.parent.parent.resolve()
    target = (public_root / "rcaeval" / REVISION / "cases.parquet").resolve()
    if not target.is_relative_to(public_root):
        raise ValueError("RCAEval index escapes the public-data root")
    records = [record for record in manifest["files"] if Path(record["path"]).resolve() == target]
    if len(records) != 1:
        raise ValueError("RCAEval index missing/duplicated in source manifest")
    record = records[0]
    data = target.read_bytes()
    digest = verify_bytes(data, record["publisher_checksum"])
    if digest != INDEX_SHA256 or digest != record["sha256"]:
        raise ValueError("RCAEval index differs from the frozen publisher-verified bytes")
    # Decode verified bytes, rather than reopening a path that could change after verification.
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows = pq.read_table(pa.BufferReader(data)).to_pylist()
    if len(rows) != 735:
        raise ValueError("RCAEval index no longer accounts for all 735 cases")
    return rows, manifest


def audit_partitions(manifest_path: Path, partition_path: Path = DEFAULT_PARTITIONS, *, project: Path | None = None) -> dict:
    commit = None
    if project is not None:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit partition protocol/code before the measured RCAEval audit")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    config = load_partitions(partition_path)
    rows, manifest = read_index(manifest_path)
    require_cases(manifest["selection"], config)
    if partition_metadata(rows) != config:
        raise ValueError("RCAEval metadata does not reproduce the frozen groups/roles")
    assignments = {case: group for group in config["assignments"] for case in group["cases"]}
    by_role = {}
    quarantined = []
    overlaps = Counter()
    for i, row in enumerate(rows):
        group = assignments[row["case"]]
        entry = by_role.setdefault(group["role"], {"cases": 0, "groups": set(), "systems": Counter(),
                                                  "cases_without_logs": 0, "cases_without_traces": 0,
                                                  "label_metadata_quarantined": 0})
        entry["cases"] += 1
        entry["groups"].add(group["group_id"])
        entry["systems"][row["system"]] += 1
        entry["cases_without_logs"] += not row["has_logs"]
        entry["cases_without_traces"] += not row["has_traces"]
        if not row["time_start"] <= row["inject_time"] <= row["time_end"]:
            entry["label_metadata_quarantined"] += 1
            quarantined.append({"case": row["case"], "role": group["role"],
                                "reason": "injection-time-outside-capture-range"})
        for other in rows[:i]:
            if row["system"] != other["system"] and max(row["time_start"], other["time_start"]) <= min(row["time_end"], other["time_end"]):
                overlaps["/".join(sorted([row["system"], other["system"]]))] += 1
    for value in by_role.values():
        value["groups"] = len(value["groups"])
        value["systems"] = dict(sorted(value["systems"].items()))
    return {"audit_version": "rcaeval-partition-audit/v1", "source_revision": REVISION, "source_commit": commit,
            "index_sha256": INDEX_SHA256, "partition_sha256": FROZEN_PARTITIONS_SHA256,
            "source_manifest_sha256": manifest_path.stem, "cases": len(rows),
            "groups": len(config["assignments"]), "roles": by_role,
            "cross_system_overlapping_pairs": dict(sorted(overlaps.items())),
            "label_metadata_quarantine": sorted(quarantined, key=lambda row: row["case"]),
            "already_inspected_cases": sorted(manifest["selection"]),
            "inspected_group_closure_cases": sum(len(g["cases"]) for g in config["assignments"] if g["previously_inspected"]),
            "case_telemetry_read": False, "holdout_opened": False, "ml_fitting_allowed": False,
            "independent_sessions_established": False, "full_G4_accepted": False, "activation_allowed": False,
            "limitations": ["Conservative metadata groups are not proven independent capture sessions.",
                            "The full index was inspected for grouping; no additional case telemetry was opened.",
                            "Fault/injection metadata is evaluator-only, not RCA evidence or SLO/precursor truth.",
                            "Missing logs/traces and invalid label clocks remain explicit; no imputation.",
                            "No localization/abstention accuracy, source semantic parity or final holdout acceptance is claimed."]}
