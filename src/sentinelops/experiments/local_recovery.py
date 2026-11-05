"""Explicit terminal host-interruption accounting; never replay or relabel a group."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import (
    KINDS, ROLES, PROJECT, acquisition_lock, bind, clean_source, docker, docker_clock,
    group_name, read_binding, registered_sessions, registration_path,
)
from sentinelops.telemetry.core import stable_id, timestamp

CODE_PATH = "src/sentinelops/experiments/local_recovery.py"
VERSION = "local-http-host-interruption/v1"


def _code_hash(project, commit):
    if not isinstance(commit, str) or not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("interruption observer needs a committed source identity")
    return hashlib.sha256(subprocess.check_output(
        ["git", "show", commit + ":" + CODE_PATH], cwd=project)).hexdigest()


def _inventory(store, group):
    """Bind every original source byte, including incomplete captures, without links."""
    roots = [registration_path(store, group)]
    roots.extend(store.root / "runs" / (group + "-" + kind) for kind in KINDS)
    entries = []
    for root in roots:
        for part in (root, *root.parents):
            if part == store.root:
                break
            if part.is_symlink():
                raise ValueError("interrupted source cannot traverse symlinks")
        if not root.exists():
            continue
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if path.is_symlink():
                raise ValueError("interrupted source cannot traverse symlinks")
            if not path.is_file():
                if not path.is_dir():
                    raise ValueError("interrupted source contains a special file")
                continue
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            entries.append({"path": path.relative_to(store.root).as_posix(),
                            "size": size, "sha256": digest.hexdigest()})
    return sorted(entries, key=lambda item: item["path"])


def _validate_observation(session, observed):
    if (observed.get("kind") != VERSION
        or observed.get("session_ref") != "artifact://manifests/" + stable_id(session) + ".json"
        or observed.get("compose_project") != session["compose_project"]
        or not isinstance(observed.get("host_boot_sha256"), str)
        or not re.fullmatch("[0-9a-f]{64}", observed["host_boot_sha256"])
        or observed["host_boot_sha256"] == session["context"]["host_boot_sha256"]
        or utc_time(observed["collected_at"]) < utc_time(session["registered_at"])
        or set(observed.get("containers", {})) != set(session["context"]["containers"])):
        raise ValueError("host interruption lacks original scope or changed host boot")
    for service, original in session["context"]["containers"].items():
        item = observed["containers"][service]
        if (item["container_id"] != original["container_id"] or item["image"] != original["image"]
            or item["compose_project"] != session["compose_project"]
            or item["service"] != service or item["running"] is not False or item["status"] != "exited"
            or type(item["exit_code"]) is not int or type(item["restart_count"]) is not int
            or item["restart_count"] != 0):
            raise ValueError("interruption requires original owned stopped, unrestarted containers")
        for field in ("created_at", "started_at", "finished_at"):
            if item[field] != docker_clock(item[field + "_source"]):
                raise ValueError("interruption source clock conversion differs")
        if (item["created_at"] != original["created_at"]
            or not utc_time(item["created_at"]) <= utc_time(item["started_at"]) <= utc_time(session["registered_at"])
            or not utc_time(item["started_at"]) <= utc_time(item["finished_at"]) <= utc_time(observed["collected_at"])):
            raise ValueError("interruption container clocks differ from the original source epoch")


def read_abandonment(store, session, plan, *, project=PROJECT, as_of=None):
    """Replay the immutable disposition, not today's Docker state or approval flags."""
    path = store.root / "sessions" / session["group_id"] / "closed.json"
    if not path.exists():
        return None
    ref, closed = read_binding(store, path)
    if closed.get("closure_version") != VERSION:
        if closed.get("closure_version") != "local-http-session-closed/v1":
            raise ValueError("unknown prospective session closure")
        return None
    from sentinelops.ml.local_data import _session_identity
    _session_identity(session, plan)
    replacement = group_name(plan, session["role"], closed["replacement_ordinal"])
    decision = as_of or datetime.now(timezone.utc)
    if (closed.get("group_id") != session["group_id"]
        or closed.get("session_ref") != "artifact://manifests/" + stable_id(session) + ".json"
        or closed.get("reason") != "host-context-discontinuity"
        or closed.get("plan_sha256") != stable_id(plan)
        or closed.get("excluded_whole_group") is not True or closed.get("source_preserved") is not True
        or closed["replacement_ordinal"] <= plan["minimum_groups"][session["role"]]
        or closed.get("replacement_group_id") != replacement
        or not utc_time(session["registered_at"]) <= utc_time(closed["closed_at"]) <= decision):
        raise ValueError("interrupted group disposition differs from frozen source/role/budget")
    incomplete = [session["group_id"] + "-" + kind for kind in KINDS
                  if not (store.root / "runs" / (session["group_id"] + "-" + kind) / "result.json").exists()]
    if not incomplete or closed.get("incomplete_slots") != incomplete:
        raise ValueError("interruption must retain its exact original incomplete slots")
    observed = store.read(closed["observation_ref"])
    _validate_observation(session, observed)
    if (observed["collected_at"] != closed["closed_at"]
        or observed["observer_code_sha256"] != _code_hash(project, observed["observer_source_commit"])):
        raise ValueError("interruption observer identity differs")
    inventory = store.read(closed["inventory_ref"])
    if inventory != {"inventory_version": VERSION, "group_id": session["group_id"],
                     "files": _inventory(store, session["group_id"])}:
        raise ValueError("interrupted original source changed after terminal accounting")
    from sentinelops.ml.local_data import verify_capture
    for kind in KINDS:
        if session["group_id"] + "-" + kind not in incomplete:
            verify_capture(store, plan, session, kind, project=project, as_of=utc_time(closed["closed_at"]))
    return ref, closed


def _observe(session, source, project):
    actual = json.loads(docker("inspect", *[
        item["container_id"] for item in session["context"]["containers"].values()]))
    values = {}
    for item in actual:
        labels = item["Config"].get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if service in values:
            raise ValueError("interruption observer duplicated a source service")
        state = item["State"]
        values[service] = {
            "container_id": item["Id"], "image": item["Image"],
            "compose_project": labels.get("com.docker.compose.project"), "service": service,
            "running": state["Running"], "status": state["Status"], "exit_code": state["ExitCode"],
            "restart_count": item["RestartCount"],
            "created_at": docker_clock(item["Created"]), "created_at_source": item["Created"],
            "started_at": docker_clock(state["StartedAt"]), "started_at_source": state["StartedAt"],
            "finished_at": docker_clock(state["FinishedAt"]), "finished_at_source": state["FinishedAt"]}
    observed = {"kind": VERSION, "session_ref": "artifact://manifests/" + stable_id(session) + ".json",
        "compose_project": session["compose_project"], "containers": values,
        "host_boot_sha256": stable_id(Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
        "collected_at": timestamp(datetime.now(timezone.utc)), "observer_source_commit": source,
        "observer_code_sha256": _code_hash(project, source)}
    _validate_observation(session, observed)
    return observed


def abandon_interrupted_group(store, plan, *, role, ordinal, replacement_ordinal, project=PROJECT):
    """Explicit operator action. No Docker mutation, quality-based replacement or slot retry."""
    from sentinelops.experiments.local_campaign import campaign_lock
    from sentinelops.ml.local_data import _session_identity, verify_capture
    group, replacement = group_name(plan, role, ordinal), group_name(plan, role, replacement_ordinal)
    if replacement_ordinal <= plan["minimum_groups"][role]:
        raise ValueError("replacement must use a new ordinal beyond the reserved minimum groups")
    with campaign_lock(store), acquisition_lock(store):
        if (store.root / "dataset-freeze.json").exists():
            raise ValueError("cannot abandon a frozen corpus group")
        source = clean_source(project)
        _, session = read_binding(store, registration_path(store, group))
        _session_identity(session, plan)
        closed_path = store.root / "sessions" / group / "closed.json"
        if closed_path.exists() or registration_path(store, replacement).exists():
            raise ValueError("group is already closed or replacement has already begun")
        for other in registered_sessions(store):
            if ROLES.index(other["role"]) > ROLES.index(role):
                raise ValueError("cannot repair an earlier role after a later role has begun")
            prior = read_abandonment(store, other, plan, project=project)
            if prior and prior[1]["replacement_group_id"] == replacement:
                raise ValueError("replacement is reserved by another interrupted group")
        incomplete = []
        # A corrupt completed capture cannot be hidden by an interruption receipt.
        for kind in KINDS:
            root = store.root / "runs" / (group + "-" + kind)
            if (root / "result.json").exists():
                verify_capture(store, plan, session, kind, project=project)
            else:
                incomplete.append(group + "-" + kind)
        if not incomplete:
            raise ValueError("completed groups cannot be replaced by interruption accounting")
        before = _inventory(store, group)
        observed = _observe(session, source, project)
        if _inventory(store, group) != before or clean_source(project) != source:
            raise ValueError("source changed during interruption observation")
        observation_ref = store.write("raw", observed)
        inventory_ref = store.write("manifests", {"inventory_version": VERSION, "group_id": group, "files": before})
        closed = {"closure_version": VERSION, "group_id": group,
            "session_ref": "artifact://manifests/" + stable_id(session) + ".json",
            "plan_sha256": stable_id(plan), "closed_at": observed["collected_at"],
            "reason": "host-context-discontinuity", "observation_ref": observation_ref,
            "inventory_ref": inventory_ref, "incomplete_slots": incomplete,
            "excluded_whole_group": True, "source_preserved": True,
            "replacement_ordinal": replacement_ordinal, "replacement_group_id": replacement}
        ref = bind(store, closed_path, closed)
        return {"closure_ref": ref, **closed, "models_fitted": 0, "activation_allowed": False}


def effective_slots(store, plan, slots, *, project=PROJECT):
    """Follow explicit same-role replacements; never allocate a replacement automatically."""
    resolved = []
    for role, ordinal, kind in slots:
        seen = set()
        while True:
            group = group_name(plan, role, ordinal)
            if group in seen:
                raise ValueError("interrupted replacement chain contains a cycle")
            seen.add(group)
            path = registration_path(store, group)
            if not path.exists():
                break
            _, session = read_binding(store, path)
            if session.get("group_id") != group or session.get("role") != role or session.get("ordinal") != ordinal:
                raise ValueError("replacement path differs from original group/role registration")
            value = read_abandonment(store, session, plan, project=project)
            if value is None:
                break
            ordinal = value[1]["replacement_ordinal"]
        resolved.append((role, ordinal, kind))
    if len(set(resolved)) != len(resolved):
        raise ValueError("interrupted replacements duplicate an acquisition slot")
    return resolved
